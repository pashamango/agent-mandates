use axum::extract::{Json, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::Router;
use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::{info, warn};

use std::sync::Arc;
use crate::config::Config;
use crate::db::Database;
use crate::linear::LinearClient;
use crate::router::SmartRouter;

type HmacSha256 = Hmac<Sha256>;

#[derive(Clone)]
pub struct AppState {
    pub config: Config,
    pub db: Database,
    pub router: Arc<SmartRouter>,
    pub linear: Option<Arc<LinearClient>>,
}

pub fn create_router(state: AppState) -> Router {
    Router::new()
        .route("/healthz", get(healthz_handler))
        .route("/linear-webhook", post(webhook_handler))
        .with_state(state)
}

async fn healthz_handler(State(state): State<AppState>) -> impl IntoResponse {
    let stats = state.db.stats();
    (StatusCode::OK, Json(serde_json::json!({ "ok": true, "stats": stats })))
}

async fn webhook_handler(
    State(state): State<AppState>,
    headers: HeaderMap,
    body_bytes: axum::body::Bytes,
) -> impl IntoResponse {
    let (status, body) = handle_webhook(state, headers, body_bytes).await;
    (status, Json(body)).into_response()
}

pub async fn handle_webhook(
    state: AppState,
    headers: HeaderMap,
    body_bytes: axum::body::Bytes,
) -> (StatusCode, serde_json::Value) {
    // 1. Signature Verification
    if let Some(secret) = &state.config.linear_webhook_secret {
        let sig_opt = headers.get("Linear-Signature").and_then(|v| v.to_str().ok());
        if let Some(sig) = sig_opt {
            let mut mac = HmacSha256::new_from_slice(secret.as_bytes()).expect("HMAC can take key of any size");
            mac.update(&body_bytes);
            let expected = hex::encode(mac.finalize().into_bytes());

            if sig.to_lowercase() != expected.to_lowercase() {
                warn!("Invalid webhook signature");
                return (StatusCode::UNAUTHORIZED, serde_json::json!({ "error": "Invalid signature" }));
            }
        } else {
            warn!("Missing Linear-Signature header");
            return (StatusCode::UNAUTHORIZED, serde_json::json!({ "error": "Missing signature" }));
        }
    }

    // 2. Parse JSON & check timestamp
    let payload: serde_json::Value = match serde_json::from_slice(&body_bytes) {
        Ok(p) => p,
        Err(e) => {
            warn!("Invalid JSON payload: {}", e);
            return (StatusCode::BAD_REQUEST, serde_json::json!({ "error": "Invalid JSON" }));
        }
    };

    if let Some(ts) = payload.get("webhookTimestamp").and_then(|t| t.as_f64()) {
        let now_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64()
            * 1000.0;
        let age_secs = (now_ms - ts).abs() / 1000.0;
        if age_secs > 300.0 {
            warn!("Rejected stale webhook (age: {:.1}s)", age_secs);
            return (StatusCode::UNAUTHORIZED, serde_json::json!({ "error": "Stale webhook" }));
        }
    }

    let delivery_id = headers
        .get("Linear-Delivery")
        .and_then(|v| v.to_str().ok())
        .or_else(|| payload.get("webhookId").and_then(|v| v.as_str()))
        .unwrap_or("deliv-unknown");

    let event_type = payload.get("type").and_then(|v| v.as_str()).unwrap_or("");
    let action = payload.get("action").and_then(|v| v.as_str()).unwrap_or("");
    let issue_id = payload
        .pointer("/data/id")
        .and_then(|v| v.as_str())
        .unwrap_or("");

    // 3. Durable Inbox Insertion
    let raw_str = String::from_utf8_lossy(&body_bytes);
    let accepted = state.db.record_inbox(delivery_id, event_type, action, issue_id, &raw_str, "accepted");
    if !accepted {
        info!("Duplicate delivery {} ignored", delivery_id);
        return (StatusCode::OK, serde_json::json!({ "status": "duplicate_ignored" }));
    }

    // 4. Evaluate Event
    let (should_enqueue, cmd_type, builder, issue_info) = evaluate_event(event_type, action, &payload, &state.config);

    if !should_enqueue || issue_info.is_none() {
        info!("Event ignored by filter (type={}, action={})", event_type, action);
        return (StatusCode::OK, serde_json::json!({ "status": "ignored" }));
    }

    let info = issue_info.unwrap();

    // Prevent duplicate concurrent execution for the same issue
    if state.db.is_issue_already_enqueued(&info.id) {
        info!("Issue {} already has active or pending job, ignoring duplicate webhook", info.identifier);
        return (StatusCode::OK, serde_json::json!({ "status": "already_queued" }));
    }

    // 5. Smart Router & Canonical Gate Enforcement (§3, §4, §5, §6)
    if cmd_type != "CANCEL" {
        state.router.refresh_pilot_gate("fleetd-evolution/evidence/PILOT_MIN_221_RECEIPT.json");
        state.router.refresh_slot_health(&state.config.codex_bin);
        state.router.refresh_quota_signals("fleetd-evolution/data/quota_signals.json");

        let handoff_commit = crate::router::resolve_handoff_commit(None, &info.description);
        let resolved_project = info.project.clone().or_else(|| {
            crate::canonical::extract_project_from_text(&format!("{}\n{}", info.title, info.description))
        });

        let now_secs = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let wait_time_secs = info.created_at.as_deref()
            .and_then(crate::linear::parse_timestamp_secs)
            .map(|c| now_secs.saturating_sub(c))
            .unwrap_or(0);

        let task_cand = crate::router::TaskCandidate {
            id: info.id.clone(),
            title: info.title.clone(),
            description: info.description.clone(),
            project: resolved_project,
            tier: None,
            status: info.status.clone().or_else(|| Some("Todo".to_string())),
            assignee: info.assignee.clone(),
            wait_time_secs,
        };

        let decision = state.router.route_and_claim_task(&task_cand, 3600, false, handoff_commit.as_deref());
        if !decision.success {
            info!("Webhook rejected task {} via vNext: {}", info.identifier, decision.reason);
            if let Some(rework_msg) = &decision.rework_comment {
                if let Some(linear) = &state.linear {
                    let _ = linear.post_comment(&info.id, rework_msg).await;
                }
            }
            return (
                StatusCode::OK,
                serde_json::json!({
                    "status": "rejected",
                    "reason": decision.reason,
                    "rework_comment": decision.rework_comment,
                }),
            );
        }

        let routed_builder = decision.slot.unwrap_or(builder);
        let chosen_slot = if routed_builder == "codex-local" { "codex" } else { "agy" };

        match state.db.enqueue_job(
            delivery_id,
            &info.id,
            &info.identifier,
            &info.title,
            &info.description,
            &cmd_type,
            chosen_slot,
        ) {
            Ok(run_id) => {
                info!("Enqueued job {} for builder {} (cmd={}, run_id={}, commit={:?})", info.identifier, chosen_slot, cmd_type, run_id, handoff_commit);
                (
                    StatusCode::OK,
                    serde_json::json!({
                        "status": "queued",
                        "builder": chosen_slot,
                        "run_id": run_id,
                        "commit_sha": handoff_commit,
                    }),
                )
            }
            Err(e) => {
                warn!("Failed to enqueue job: {}", e);
                (StatusCode::INTERNAL_SERVER_ERROR, serde_json::json!({ "error": "Database error" }))
            }
        }
    } else {
        match state.db.enqueue_job(
            delivery_id,
            &info.id,
            &info.identifier,
            &info.title,
            &info.description,
            &cmd_type,
            &builder,
        ) {
            Ok(run_id) => {
                info!("Enqueued cancel job {} for builder {} (run_id={})", info.identifier, builder, run_id);
                (
                    StatusCode::OK,
                    serde_json::json!({ "status": "queued", "builder": builder, "run_id": run_id }),
                )
            }
            Err(e) => {
                warn!("Failed to enqueue cancel job: {}", e);
                (StatusCode::INTERNAL_SERVER_ERROR, serde_json::json!({ "error": "Database error" }))
            }
        }
    }
}

pub struct IssueInfo {
    pub id: String,
    pub identifier: String,
    pub title: String,
    pub description: String,
    pub project: Option<String>,
    pub status: Option<String>,
    pub assignee: Option<String>,
    pub created_at: Option<String>,
}

fn evaluate_event(
    event_type: &str,
    action: &str,
    payload: &serde_json::Value,
    config: &Config,
) -> (bool, String, String, Option<IssueInfo>) {
    let data = match payload.get("data") {
        Some(d) => d,
        None => return (false, String::new(), String::new(), None),
    };

    if event_type == "Issue" {
        let team_key = data
            .pointer("/team/key")
            .and_then(|k| k.as_str())
            .or_else(|| data.get("teamKey").and_then(|k| k.as_str()))
            .unwrap_or("");

        if !team_key.is_empty() && !config.allowed_teams.iter().any(|t| t == team_key) {
            return (false, String::new(), String::new(), None);
        }

        // Check if assigned to Pavel Cherkashin (Principal/Reviewer - do not auto-run!)
        let assignee_id = data
            .pointer("/assignee/id")
            .and_then(|v| v.as_str())
            .or_else(|| data.get("assigneeId").and_then(|v| v.as_str()))
            .unwrap_or("");

        if !assignee_id.is_empty() && assignee_id == config.pavel_user_id {
            return (false, String::new(), String::new(), None);
        }

        let mut builder_choice = "";
        if let Some(labels) = data.get("labels").and_then(|l| l.as_array()) {
            for l in labels {
                let name = l
                    .get("name")
                    .and_then(|n| n.as_str())
                    .or_else(|| l.as_str())
                    .unwrap_or("");
                if name.eq_ignore_ascii_case("Builder: codex") || name.eq_ignore_ascii_case("[Builder: codex]") {
                    builder_choice = "codex";
                    break;
                } else if name.eq_ignore_ascii_case("Builder: agy") || name.eq_ignore_ascii_case("[Builder: agy]") {
                    builder_choice = "agy";
                    break;
                } else if name.eq_ignore_ascii_case("Role: Builder-Ready") || name.eq_ignore_ascii_case("Builder: any") {
                    builder_choice = "any";
                }
            }
        }

        // If explicitly assigned to bot user, treat as "any" if not already set
        if builder_choice.is_empty() && !assignee_id.is_empty() && assignee_id == config.bot_user_id {
            builder_choice = "any";
        }

        let project = data.pointer("/project/name").and_then(|v| v.as_str())
            .or_else(|| data.get("projectName").and_then(|v| v.as_str()))
            .map(|s| s.to_string());
        let status = data.pointer("/state/name").and_then(|v| v.as_str())
            .or_else(|| data.get("stateName").and_then(|v| v.as_str()))
            .map(|s| s.to_string());
        let assignee = data.pointer("/assignee/name").and_then(|v| v.as_str())
            .or_else(|| data.get("assigneeName").and_then(|v| v.as_str()))
            .map(|s| s.to_string());
        let created_at = data.get("createdAt").and_then(|v| v.as_str()).map(|s| s.to_string());

        if !builder_choice.is_empty() {
            if action == "create" {
                return (
                    true,
                    "START".to_string(),
                    builder_choice.to_string(),
                    Some(IssueInfo {
                        id: data.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        identifier: data.get("identifier").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        title: data.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        description: data.get("description").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        project,
                        status,
                        assignee,
                        created_at,
                    }),
                );
            }

            if action == "update" {
                if payload.pointer("/updatedFrom/labels").is_some()
                    || payload.pointer("/updatedFrom/labelIds").is_some()
                    || payload.pointer("/updatedFrom/assigneeId").is_some()
                {
                    return (
                        true,
                        "START".to_string(),
                        builder_choice.to_string(),
                        Some(IssueInfo {
                            id: data.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                            identifier: data.get("identifier").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                            title: data.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                            description: data.get("description").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                            project,
                            status,
                            assignee,
                            created_at,
                        }),
                    );
                }
            }
        }
    } else if event_type == "Comment" && action == "create" {
        let body = data.get("body").and_then(|b| b.as_str()).unwrap_or("");
        // Avoid infinite recursion loops!
        if body.contains("<!-- agy-run:")
            || body.contains("<!-- codex-run:")
            || body.contains("<!-- vacuum-claim:")
        {
            return (false, String::new(), String::new(), None);
        }

        let lower = body.to_lowercase();
        let target_opt = if let Some(idx) = lower.find("@agy") {
            Some(("agy", &body[idx + 4..]))
        } else if let Some(idx) = lower.find("@codex") {
            Some(("codex", &body[idx + 6..]))
        } else if let Some(idx) = lower.find("@builder") {
            Some(("any", &body[idx + 8..]))
        } else {
            None
        };

        if let Some((target_builder, after)) = target_opt {
            let mut parts = after.trim_start().split_whitespace();
            if let Some(cmd_raw) = parts.next() {
                let cmd = cmd_raw.trim_end_matches(|c| c == ':' || c == ',').to_uppercase();
                if ["START", "FOLLOWUP", "REWORK", "CANCEL"].contains(&cmd.as_str()) {
                    let issue_id = data
                        .pointer("/issue/id")
                        .and_then(|v| v.as_str())
                        .or_else(|| data.get("issueId").and_then(|v| v.as_str()))
                        .unwrap_or("")
                        .to_string();

                    let identifier = data
                        .pointer("/issue/identifier")
                        .and_then(|v| v.as_str())
                        .unwrap_or(&issue_id)
                        .to_string();

                    let issue_obj = data.get("issue");
                    let project = issue_obj.and_then(|i| i.pointer("/project/name")).and_then(|v| v.as_str())
                        .or_else(|| issue_obj.and_then(|i| i.get("projectName")).and_then(|v| v.as_str()))
                        .map(|s| s.to_string());
                    let status = issue_obj.and_then(|i| i.pointer("/state/name")).and_then(|v| v.as_str())
                        .or_else(|| issue_obj.and_then(|i| i.get("stateName")).and_then(|v| v.as_str()))
                        .map(|s| s.to_string());
                    let assignee = issue_obj.and_then(|i| i.pointer("/assignee/name")).and_then(|v| v.as_str())
                        .or_else(|| issue_obj.and_then(|i| i.get("assigneeName")).and_then(|v| v.as_str()))
                        .map(|s| s.to_string());
                    let created_at = issue_obj.and_then(|i| i.get("createdAt")).and_then(|v| v.as_str()).map(|s| s.to_string());

                    return (
                        true,
                        cmd,
                        target_builder.to_string(),
                        Some(IssueInfo {
                            id: issue_id,
                            identifier,
                            title: "(comment command)".to_string(),
                            description: format!("Instruction from comment:\n{}", body),
                            project,
                            status,
                            assignee,
                            created_at,
                        }),
                    );
                }
            }
        }
    }

    (false, String::new(), String::new(), None)
}
