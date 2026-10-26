use std::path::PathBuf;
use std::time::{Duration, Instant};
use tracing::{error, info, warn};

use crate::config::Config;
use crate::db::Database;
use crate::linear::LinearClient;
use crate::runner::run_agent;

pub async fn run_builder_loop(builder_name: &'static str, config: Config, db: Database) {
    info!("Builder loop started for slot '{}'", builder_name);
    // Pacing: even out agy quota consumption. Bursts of back-to-back builds
    // exhaust the ~10h sliding window ("Individual quota reached").
    let pace_secs: u64 = if builder_name == "agy" {
        config.agy_min_interval_secs
    } else {
        0
    };
    let mut last_start: Option<Instant> = None;

    loop {
        let job_opt = match db.fetch_next_pending_job_for_builder(builder_name) {
            Ok(j) => j,
            Err(e) => {
                error!("Database error fetching job for {}: {}", builder_name, e);
                tokio::time::sleep(Duration::from_secs(2)).await;
                continue;
            }
        };

        match job_opt {
            Some(job) => {
                if pace_secs > 0 {
                    if let Some(prev) = last_start {
                        let elapsed = prev.elapsed().as_secs();
                        if elapsed < pace_secs {
                            let wait = pace_secs - elapsed;
                            info!(
                                "Pacing slot '{}': waiting {}s before next build to even out load",
                                builder_name, wait
                            );
                            tokio::time::sleep(Duration::from_secs(wait)).await;
                        }
                    }
                }
                last_start = Some(Instant::now());
                info!(
                    "Slot '{}' processing job {} (run_id={}, cmd={})",
                    builder_name, job.identifier, job.run_id, job.command_type
                );
                process_job(&config, &db, &job).await;
            }
            None => {
                tokio::time::sleep(Duration::from_millis(1000)).await;
            }
        }
    }
}

async fn process_job(config: &Config, db: &Database, job: &crate::db::Job) {
    let brief = format!(
        "You are {}, the autonomous builder agent on {}.\n\
         Linear ticket: {}\n\
         Run ID: {}\n\
         Command: {}\n\n\
         TASK:\n\
         Title: {}\n\n\
         Description:\n{}\n\n\
         MANDATE & CONSTITUTION:\n\
         1. Strictly adhere to Mindrock Agent Constitution (AGENTS.md).\n\
         2. Work in your assigned branch (or worktree).\n\
         3. Produce structured output: Outcome, Summary, Evidence/Artifacts, Checks, Limitations.\n\
         4. Do NOT mark Done in Linear (reserved for Principal Pavel Cherkashin).\n",
        job.builder, config.node_name, job.identifier, job.run_id, job.command_type, job.title, job.description
    );

    let home = std::env::var("HOME").unwrap_or_else(|_| "/Users/pavelcherkashin".to_string());
    let default_cwd = if config.node_name == "darwin" {
        PathBuf::from(format!("{}/ChitAkasha-Darwin", home))
    } else {
        PathBuf::from(format!("{}/ChitAkasha-Wolfram", home))
    };
    let cwd = if default_cwd.exists() {
        default_cwd
    } else {
        PathBuf::from(&home)
    };
    let bin_path = if job.builder == "codex" {
        &config.codex_bin
    } else {
        &config.agy_bin
    };

    let result = run_agent(&job.builder, bin_path, &brief, &cwd, 3600).await;

    let is_success = result.exit_code == 0 && !result.stdout.trim().is_empty() && !result.timed_out;
    let status = if is_success { "completed" } else { "failed" };

    let mut err_msg = result.error_msg;
    if result.exit_code == 0 && result.stdout.trim().is_empty() {
        err_msg = "Process exited with code 0 but stdout was empty".to_string();
    }

    let _ = db.complete_job(
        job.id,
        status,
        result.exit_code,
        &result.stdout,
        &result.stderr,
        &err_msg,
    );

    info!(
        "Slot '{}' completed job {} with status {}",
        job.builder, job.identifier, status
    );

    // Format comment with idempotent marker and cross-agent QA trigger
    let marker = format!("<!-- {}-run:{} -->", job.builder, job.run_id);
    let mut parts = vec![
        format!("### {} build report: `{}`", job.builder, job.identifier),
        marker.clone(),
        format!(
            "**Run ID:** `{}` | **Command:** `{}` | **Status:** {}",
            job.run_id,
            job.command_type,
            if is_success { "✅ COMPLETED" } else { "❌ FAILED" }
        ),
        String::new(),
    ];

    if !is_success {
        parts.push(format!(
            "**Failure Reason:** {}\n",
            if err_msg.is_empty() {
                format!("Exit code {}", result.exit_code)
            } else {
                err_msg
            }
        ));
        if !result.stderr.trim().is_empty() {
            parts.push("```text".to_string());
            parts.push(result.stderr.trim()[..result.stderr.trim().len().min(3000)].to_string());
            parts.push("```\n".to_string());
        }
    }

    if !result.stdout.trim().is_empty() {
        parts.push("#### Output".to_string());
        parts.push(result.stdout.trim().to_string());
    }

    let qa_agent = if job.builder == "agy" { "@Codex" } else { "@agy" };
    let rework_agent = if job.builder == "agy" { "@agy" } else { "@codex" };

    if is_success {
        parts.push(String::new());
        parts.push("---".to_string());
        parts.push(format!("{} — проведи независимый QA этой задачи по критериям приёмки.", qa_agent));
        parts.push(format!("Если найдены ошибки — ответь: `{} REWORK: <замечания>`.", rework_agent));
        parts.push("Если всё выполнено корректно — подтверди `PASS` и назначь задачу на @pavel.cherkashin для финального одобрения.".to_string());
    }

    let comment_body = parts.join("\n");
    let _ = db.enqueue_outbox(job.id, &job.issue_id, &job.identifier, &comment_body, &marker, &job.builder);
}

pub async fn process_outbox_item(
    db: &Database,
    linear: &LinearClient,
    item: &crate::db::OutboxItem,
    verify_remote: bool,
) -> Result<bool, String> {
    // 1. Reconciliation: check if already posted
    let already_posted = linear.comment_exists(&item.issue_id, &item.marker).await;
    if !already_posted {
        if let Err(e) = linear.post_comment(&item.issue_id, &item.comment_body).await {
            warn!("Failed to post comment for {}: {}", item.identifier, e);
            let _ = db.mark_outbox_failed(item.id, &e);
            return Err(e);
        }
    }

    // Resolve builder safely from outbox metadata or report header/marker
    let builder = crate::handoff_gate::resolve_builder(&item.builder, &item.marker, &item.comment_body);

    // Resolve expected branch from Linear
    let expected_branch = match linear.get_issue_branch_name(&item.issue_id).await {
        Ok(b) => b,
        Err(e) => {
            warn!("Failed to query issue branchName for {}: {}", item.identifier, e);
            None
        }
    };

    // 2. Preflight Gate Check (MIN-236)
    match crate::handoff_gate::validate_handoff_gate(&item.comment_body, expected_branch.as_deref(), &builder, verify_remote).await {
        Ok(coords) => {
            info!(
                "Issue {} passed Handoff Gate (repo={}, branch={}, commit={}), advancing to In Review + QA label",
                item.identifier, coords.repo, coords.branch, coords.commit
            );
            let _ = linear.set_in_review_and_qa_label(&item.issue_id).await;
            let _ = db.mark_outbox_delivered(item.id);
            info!("Delivered outbox comment for issue {}", item.identifier);
            Ok(true)
        }
        Err(err) => {
            warn!(
                "Issue {} failed Handoff Gate ({:?}), transition to In Review BLOCKED. Issuing auto-REWORK to @{}",
                item.identifier, err, builder
            );
            let rework_body = err.to_rework_comment(&builder);
            let _ = linear.post_comment(&item.issue_id, &rework_body).await;
            let _ = db.mark_outbox_delivered(item.id);
            info!("Handoff Gate intercepted delivery for issue {}", item.identifier);
            Ok(false)
        }
    }
}

pub async fn run_outbox_loop_with_client(_config: Config, db: Database, linear: LinearClient) {
    info!("Outbox delivery loop started");
    loop {
        let items = match db.fetch_pending_outbox() {
            Ok(i) => i,
            Err(e) => {
                error!("Database error fetching outbox: {}", e);
                tokio::time::sleep(Duration::from_secs(2)).await;
                continue;
            }
        };

        if items.is_empty() {
            tokio::time::sleep(Duration::from_millis(2000)).await;
            continue;
        }

        for item in items {
            let _ = process_outbox_item(&db, &linear, &item, true).await;
        }
    }
}

pub async fn run_outbox_loop(config: Config, db: Database) {
    let linear = LinearClient::new(&config.linear_api_url, &config.linear_api_key);
    run_outbox_loop_with_client(config, db, linear).await;
}
