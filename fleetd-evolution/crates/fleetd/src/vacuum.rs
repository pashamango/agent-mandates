use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tracing::{info, warn};

use crate::canonical::{extract_project_from_text, validate_canonical_rules};
use crate::config::Config;
use crate::db::Database;
use crate::linear::{parse_timestamp_secs, LinearClient};
use crate::router::{resolve_handoff_commit, SmartRouter, TaskCandidate};

pub async fn run_vacuum_loop(
    config: Config,
    db: Database,
    router: Arc<SmartRouter>,
    linear: Arc<LinearClient>,
) {
    if !config.vacuum_enabled {
        info!("Vacuum loop disabled by configuration");
        return;
    }

    info!(
        "Vacuum loop started (interval: {}s, allowed_teams: {:?})",
        config.vacuum_interval_secs, config.allowed_teams
    );

    // Initial delay so fleetd settles on startup
    tokio::time::sleep(Duration::from_secs(10)).await;

    loop {
        tokio::time::sleep(Duration::from_secs(config.vacuum_interval_secs)).await;

        // Dynamic health, pilot receipt, and quota signal refresh (§3.3, §3.4, §6)
        router.refresh_pilot_gate("fleetd-evolution/evidence/PILOT_MIN_221_RECEIPT.json");
        router.refresh_slot_health(&config.codex_bin);
        router.refresh_quota_signals("fleetd-evolution/data/quota_signals.json");

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

                    // 1. Resolve actual task attributes (§4, §3.3)
                    let resolved_project = candidate.project_name.clone().or_else(|| {
                        extract_project_from_text(&format!("{}\n{}", candidate.title, candidate.description))
                    });

                    let resolved_status = candidate.state_name.clone().or_else(|| {
                        match candidate.state_type.as_deref() {
                            Some("unstarted") => Some("Todo".to_string()),
                            Some("backlog") => Some("Backlog".to_string()),
                            _ => None,
                        }
                    });

                    let now_secs = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .map(|d| d.as_secs())
                        .unwrap_or(0);

                    let wait_time_secs = candidate.created_at.as_deref()
                        .and_then(parse_timestamp_secs)
                        .map(|created| now_secs.saturating_sub(created))
                        .unwrap_or(0);

                    let resolved_assignee = candidate.assignee_name.clone();
                    let handoff_commit = resolve_handoff_commit(None, &candidate.description);

                    // Canonical validation pre-check (§4)
                    let verdict = validate_canonical_rules(
                        resolved_project.as_deref(),
                        &candidate.title,
                        &candidate.description,
                        resolved_status.as_deref(),
                        None,
                        resolved_assignee.as_deref(),
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
                        project: resolved_project,
                        tier: verdict.extracted_tier,
                        status: resolved_status,
                        assignee: resolved_assignee,
                        wait_time_secs,
                    };

                    let decision = router.route_and_claim_task(&task_cand, 3600, false, handoff_commit.as_deref());
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
