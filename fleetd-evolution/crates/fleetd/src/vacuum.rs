use std::sync::Arc;
use std::time::Duration;
use tracing::{info, warn};

use crate::canonical::validate_canonical_rules;
use crate::config::Config;
use crate::db::Database;
use crate::lease::DurableLeaseManager;
use crate::ledger::RoutingLedger;
use crate::linear::LinearClient;
use crate::router::{SmartRouter, TaskCandidate};

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
    let lease_manager = Arc::new(DurableLeaseManager::new("fleetd-evolution/data/active_leases.json"));
    let ledger = Arc::new(RoutingLedger::new("fleetd-evolution/data/routing_ledger.jsonl"));
    // Fail-closed default for pilot_passed (§6.3)
    let router = SmartRouter::new(300, lease_manager, ledger, false);

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

        info!("Vacuum checking for backlog candidates");

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
                        continue;
                    }
                    if config.node_name == "darwin" && has_host_wolfram {
                        continue;
                    }

                    // 1. Canonical validation pre-check (§4)
                    let verdict = validate_canonical_rules(
                        None, // Project will be checked from candidate description or GraphQL
                        &candidate.title,
                        &candidate.description,
                        Some("Todo"),
                        None,
                        None,
                    );

                    if !verdict.passed {
                        if let Some(comment) = &verdict.rework_comment {
                            info!("Vacuum canonical rejection for {}: {}", candidate.identifier, verdict.reason);
                            let _ = linear.post_comment(&candidate.id, comment).await;
                        }
                        continue;
                    }

                    // 2. Smart routing & atomic single lease check (§3, §5, §6)
                    let task_cand = TaskCandidate {
                        id: candidate.id.clone(),
                        title: candidate.title.clone(),
                        description: candidate.description.clone(),
                        project: Some("Linear Project".to_string()),
                        tier: verdict.extracted_tier,
                        status: Some("Todo".to_string()),
                        assignee: None,
                        wait_time_secs: 0,
                    };

                    let decision = router.route_and_claim_task(&task_cand, 3600, false, None);
                    if !decision.success {
                        info!("Vacuum router skipped {}: {}", candidate.identifier, decision.reason);
                        continue;
                    }

                    let issue_builder = decision.slot.unwrap_or_else(|| "agy".to_string());
                    let slot_name = if issue_builder == "codex-local" { "codex" } else { "agy" };

                    info!(
                        "Vacuum claiming issue {} ({}) for builder {}",
                        candidate.identifier, candidate.title, slot_name
                    );

                    // 1. Move to In Progress
                    let _ = linear.set_in_progress(&candidate.id).await;

                    // 2. Assign to bot user
                    let _ = linear.set_assignee(&candidate.id, &config.bot_user_id).await;

                    // 3. Post claim comment
                    let claim_marker = format!("<!-- vacuum-claim:{} -->", candidate.id);
                    let claim_body = format!(
                        "🤖 **Vacuum Auto-Claim**\n{}\nProactively pulled from backlog into `In Progress` for autonomous builder `{}`.\nExecution started on Wolfram.",
                        claim_marker, slot_name
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
                        slot_name,
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
