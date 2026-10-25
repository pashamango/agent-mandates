use std::time::Duration;
use tracing::{info, warn};

use crate::config::Config;
use crate::db::Database;
use crate::linear::LinearClient;

pub async fn run_vacuum_loop(config: Config, db: Database) {
    if !config.vacuum_enabled {
        info!("Vacuum loop disabled by configuration");
        return;
    }

    info!(
        "Vacuum loop started (interval: {}s, allowed_teams: {:?})",
        config.vacuum_interval_secs, config.allowed_teams
    );

    let linear = LinearClient::new(&config.linear_api_url, &config.linear_api_key);

    // Initial delay so fleetd settles on startup
    tokio::time::sleep(Duration::from_secs(10)).await;

    loop {
        tokio::time::sleep(Duration::from_secs(config.vacuum_interval_secs)).await;

        // Check if any slot is available
        let agy_active = db.has_active_job_for_builder("agy");
        let codex_active = db.has_active_job_for_builder("codex");

        if agy_active && codex_active {
            // Both slots busy, no vacuum pull needed
            continue;
        }

        // Check if pending jobs already exist in SQLite queue
        let stats = db.stats();
        let pending = stats.get("jobs_pending").and_then(|p| p.as_i64()).unwrap_or(0);
        if pending > 0 {
            // Wait for existing pending jobs to be picked up
            continue;
        }

        // Priority to agy first, then codex
        let candidate_builder = if !agy_active {
            "agy"
        } else {
            "codex"
        };

        info!("Vacuum checking for backlog candidates (idle slot: {})", candidate_builder);

        match linear.fetch_vacuum_candidates(&config.allowed_teams, 10).await {
            Ok(candidates) => {
                for candidate in candidates {
                    // Check if already in SQLite db (pending or running)
                    if db.is_issue_already_enqueued(&candidate.id) {
                        continue;
                    }

                    // Check veto / icebox / postmaster-triage labels
                    let ignored_labels = [
                        "VETO: Human-Hold",
                        "State: Icebox",
                        "triage:postmaster",
                        "job-lead",
                        "grant",
                    ];
                    let has_ignored_label = candidate.labels.iter().any(|l| {
                        ignored_labels.iter().any(|ig| ig.eq_ignore_ascii_case(l))
                    });
                    if has_ignored_label {
                        continue;
                    }

                    // Skip personal mailbox tickets that got synced to Linear
                    let lower_title = candidate.title.to_lowercase();
                    if lower_title.starts_with("[minutta]") || lower_title.contains("mailbox") {
                        continue;
                    }

                    // Node routing: respect Host labels
                    let has_host_darwin = candidate.labels.iter().any(|l| {
                        l.eq_ignore_ascii_case("Host: darwin") || l.eq_ignore_ascii_case("Role: Forgejo-Admin")
                    });
                    let has_host_wolfram = candidate.labels.iter().any(|l| l.eq_ignore_ascii_case("Host: wolfram"));

                    if config.node_name == "wolfram" && has_host_darwin {
                        // Leave task for Darwin node
                        continue;
                    }
                    if config.node_name == "darwin" && has_host_wolfram {
                        // Leave task for Wolfram node
                        continue;
                    }

                    // Respect explicit Builder label if set on the candidate
                    let issue_builder = if candidate.labels.iter().any(|l| l.eq_ignore_ascii_case("Builder: codex")) {
                        "codex"
                    } else if candidate.labels.iter().any(|l| l.eq_ignore_ascii_case("Builder: agy")) {
                        "agy"
                    } else {
                        candidate_builder
                    };

                    // Only take if the target slot is currently free
                    if (issue_builder == "agy" && agy_active) || (issue_builder == "codex" && codex_active) {
                        continue;
                    }

                    info!(
                        "Vacuum claiming issue {} ({}) for builder {}",
                        candidate.identifier, candidate.title, issue_builder
                    );

                    // 1. Move to In Progress
                    let _ = linear.set_in_progress(&candidate.id).await;

                    // 2. Assign to bot user
                    let _ = linear.set_assignee(&candidate.id, &config.bot_user_id).await;

                    // 3. Post claim comment
                    let claim_marker = format!("<!-- vacuum-claim:{} -->", candidate.id);
                    let claim_body = format!(
                        "🤖 **Vacuum Auto-Claim**\n{}\nProactively pulled from backlog into `In Progress` for autonomous builder `{}`.\nExecution started on Wolfram.",
                        claim_marker, issue_builder
                    );
                    let _ = linear.post_comment(&candidate.id, &claim_body).await;

                    // 4. Enqueue into DB
                    let delivery_id = format!("vacuum-{}", &uuid::Uuid::new_v4().simple().to_string()[..10]);
                    let _ = db.enqueue_job(
                        &delivery_id,
                        &candidate.id,
                        &candidate.identifier,
                        &candidate.title,
                        &candidate.description,
                        "START",
                        issue_builder,
                    );

                    info!("Vacuum successfully claimed and enqueued {}", candidate.identifier);
                    // Claim at most one job per tick
                    break;
                }
            }
            Err(e) => {
                warn!("Vacuum candidate fetch failed: {}", e);
            }
        }
    }
}
