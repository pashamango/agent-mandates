//! Smart Router (fleetd vNext - Sections 3, 5 & 6)
//! Multi-lane routing: agy (primary) vs codex-local (fallback) vs codex-cloud (isolated external).
//! Single-lease atomic protection against double claims.
//! Fail-closed quota guards and pilot gate.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use sha2::{Digest, Sha256};

use crate::canonical::validate_canonical_rules;
use crate::lease::DurableLeaseManager;
use crate::ledger::RoutingLedger;
use crate::model::{LocalModelEngine, ModelInferenceDecision};

#[derive(Debug, Clone)]
pub struct TaskCandidate {
    pub id: String,
    pub title: String,
    pub description: String,
    pub project: Option<String>,
    pub tier: Option<String>,
    pub status: Option<String>,
    pub assignee: Option<String>,
    pub wait_time_secs: u64,
}

#[derive(Debug, Clone)]
pub struct RoutingDecision {
    pub success: bool,
    pub slot: Option<String>,
    pub reason: String,
    pub rework_comment: Option<String>,
}

pub struct SmartRouter {
    pub pacing_secs: u64,
    pub last_agy_start: Mutex<f64>,
    pub slot_quota_status: Mutex<HashMap<String, String>>,
    pub slot_health_status: Mutex<HashMap<String, String>>,
    pub pilot_passed: Mutex<bool>,
    pub lease_manager: Arc<DurableLeaseManager>,
    pub ledger: Arc<RoutingLedger>,
    pub model_engine: LocalModelEngine,
}

pub fn resolve_model_weights_path() -> std::path::PathBuf {
    if let Ok(env_path) = std::env::var("FLEETD_MODEL_WEIGHTS_PATH") {
        if std::path::Path::new(&env_path).exists() {
            return std::path::PathBuf::from(env_path);
        }
    }
    let candidate_paths = [
        "fleetd-evolution/data/models/mindrock_router_v1.weights",
        "data/models/mindrock_router_v1.weights",
        "../../data/models/mindrock_router_v1.weights",
        "../data/models/mindrock_router_v1.weights",
        "../../fleetd-evolution/data/models/mindrock_router_v1.weights",
    ];
    for p in candidate_paths {
        if std::path::Path::new(p).exists() {
            return std::path::PathBuf::from(p);
        }
    }
    std::path::PathBuf::from("fleetd-evolution/data/models/mindrock_router_v1.weights")
}

impl SmartRouter {
    pub fn new(
        pacing_secs: u64,
        lease_manager: Arc<DurableLeaseManager>,
        ledger: Arc<RoutingLedger>,
        pilot_passed: bool,
    ) -> Self {
        let mut quotas = HashMap::new();
        quotas.insert("agy".to_string(), "OK".to_string());
        quotas.insert("codex-local".to_string(), "OK".to_string());

        let mut healths = HashMap::new();
        healths.insert("agy".to_string(), "HEALTH_OK".to_string());
        healths.insert("codex-local".to_string(), "HEALTH_OK".to_string());

        let weights_path = resolve_model_weights_path();
        Self {
            pacing_secs,
            last_agy_start: Mutex::new(0.0),
            slot_quota_status: Mutex::new(quotas),
            slot_health_status: Mutex::new(healths),
            pilot_passed: Mutex::new(pilot_passed),
            lease_manager,
            ledger,
            model_engine: LocalModelEngine::new(weights_path),
        }
    }

    fn now_secs() -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0)
    }

    pub fn set_slot_quota(&self, slot: &str, status: &str) {
        if let Ok(mut map) = self.slot_quota_status.lock() {
            map.insert(slot.to_string(), status.to_string());
        }
    }

    pub fn set_slot_health(&self, slot: &str, status: &str) {
        if let Ok(mut map) = self.slot_health_status.lock() {
            map.insert(slot.to_string(), status.to_string());
        }
    }

    pub fn set_pilot_passed(&self, passed: bool) {
        if let Ok(mut p) = self.pilot_passed.lock() {
            *p = passed;
        }
    }

    pub fn release_lease(&self, task_id: &str) -> Result<(), String> {
        self.lease_manager.release_lease(task_id)
    }

    pub fn refresh_pilot_gate<P: AsRef<std::path::Path>>(&self, receipt_path: P) -> bool {
        let (passed, _) = verify_pilot_receipt(receipt_path);
        self.set_pilot_passed(passed);
        passed
    }

    pub fn refresh_slot_health<P: AsRef<std::path::Path>>(&self, codex_bin: P) {
        let p = codex_bin.as_ref();
        if !p.exists() {
            self.set_slot_health("codex-local", "HEALTH_FAILED");
            let _ = self.ledger.record_decision(serde_json::json!({
                "event": "SLOT_HEALTH_CHECK_FAILED",
                "slot": "codex-local",
                "reason": format!("Binary not found: {:?}", p),
            }));
            return;
        }

        // 1. Version check
        let version_ok = match std::process::Command::new(p).arg("--version").output() {
            Ok(o) => o.status.success(),
            Err(_) => false,
        };
        if !version_ok {
            self.set_slot_health("codex-local", "HEALTH_FAILED");
            let _ = self.ledger.record_decision(serde_json::json!({
                "event": "SLOT_HEALTH_CHECK_FAILED",
                "slot": "codex-local",
                "reason": "CLI --version check failed",
            }));
            return;
        }

        // 2. Active Probe Task Dispatch to verify §6.1 builder contract:
        // Slot must accept a probe task, execute work in an isolated repo, and produce a verifiable git handoff
        let probe_ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let probe_dir = format!("/tmp/codex_health_git_probe_{}", probe_ts);
        let verify_dir = format!("/tmp/codex_health_verify_{}", probe_ts);
        let probe_path = std::path::Path::new(&probe_dir);
        let verify_path = std::path::Path::new(&verify_dir);

        let probe_res = (|| -> Result<(), String> {
            std::fs::create_dir_all(probe_path).map_err(|e| e.to_string())?;

            // Initialize probe git workspace
            let init = std::process::Command::new("git")
                .arg("init")
                .arg(&probe_dir)
                .output()
                .map_err(|e| e.to_string())?;
            if !init.status.success() {
                return Err("git init failed".to_string());
            }

            // Dispatch probe task to the slot binary with 3s timeout guard
            let mut cmd = std::process::Command::new(p);
            cmd.args(&["--health-probe", &probe_dir]);
            cmd.current_dir(probe_path);
            cmd.stdout(std::process::Stdio::null());
            cmd.stderr(std::process::Stdio::null());

            let mut child = match cmd.spawn() {
                Ok(c) => c,
                Err(_) => {
                    let mut doc = std::process::Command::new(p);
                    doc.arg("doctor");
                    doc.current_dir(probe_path);
                    doc.stdout(std::process::Stdio::null());
                    doc.stderr(std::process::Stdio::null());
                    doc.spawn().map_err(|e| format!("Failed to spawn probe: {}", e))?
                }
            };

            let start_wait = std::time::Instant::now();
            let status = loop {
                match child.try_wait() {
                    Ok(Some(s)) => break s,
                    Ok(None) => {
                        if start_wait.elapsed() > std::time::Duration::from_secs(3) {
                            let _ = child.kill();
                            return Err("Slot probe timed out after 3s".to_string());
                        }
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                    Err(e) => return Err(format!("Error polling child: {}", e)),
                }
            };

            if !status.success() {
                return Err(format!("Slot probe exited with failure status: {}", status));
            }

            // Read and strictly verify handoff receipt produced by the slot (§6.1)
            let handoff_file = probe_path.join("evidence").join("HANDOFF.json");
            if !handoff_file.exists() {
                return Err("Missing required evidence/HANDOFF.json receipt from slot probe".to_string());
            }

            let content = std::fs::read_to_string(&handoff_file).map_err(|e| format!("Failed to read handoff receipt: {}", e))?;
            let v: serde_json::Value = serde_json::from_str(&content).map_err(|e| format!("Corrupted handoff JSON: {}", e))?;

            let repo_url = v.get("repo_url").and_then(|s| s.as_str()).unwrap_or("");
            let branch = v.get("branch").and_then(|s| s.as_str()).unwrap_or("");
            let commit_sha = v.get("commit_sha").and_then(|s| s.as_str()).unwrap_or("");

            if repo_url.is_empty() || branch.is_empty() || commit_sha.len() != 40 || !commit_sha.chars().all(|c| c.is_ascii_hexdigit()) {
                return Err(format!(
                    "Incomplete or invalid git handoff receipt: repo_url='{}', branch='{}', commit_sha='{}'",
                    repo_url, branch, commit_sha
                ));
            }

            // Verify clean clone and checkout reproducibility (§6.1)
            let clone_out = std::process::Command::new("git")
                .args(&["clone", "--depth", "1", repo_url, &verify_dir])
                .output()
                .map_err(|e| e.to_string())?;
            if !clone_out.status.success() {
                return Err(format!("Clean clone failed: {}", String::from_utf8_lossy(&clone_out.stderr)));
            }

            let check_rev = std::process::Command::new("git")
                .current_dir(verify_path)
                .args(&["rev-parse", "HEAD"])
                .output()
                .map_err(|e| e.to_string())?;
            let verified_sha = String::from_utf8_lossy(&check_rev.stdout).trim().to_string();
            if verified_sha != commit_sha {
                return Err(format!("Commit SHA mismatch in clean clone: {} vs {}", verified_sha, commit_sha));
            }

            Ok(())
        })();

        let _ = std::fs::remove_dir_all(probe_path);
        let _ = std::fs::remove_dir_all(verify_path);

        match probe_res {
            Ok(()) => {
                self.set_slot_health("codex-local", "HEALTH_OK");
            }
            Err(e) => {
                self.set_slot_health("codex-local", "HEALTH_FAILED");
                let _ = self.ledger.record_decision(serde_json::json!({
                    "event": "SLOT_HEALTH_CHECK_FAILED",
                    "slot": "codex-local",
                    "reason": format!("Git handoff probe failed: {}", e),
                }));
            }
        }
    }

    pub fn refresh_quota_signals<P: AsRef<std::path::Path>>(&self, signals_path: P) {
        let p = signals_path.as_ref();
        if !p.exists() {
            let _ = self.ledger.record_decision(serde_json::json!({
                "event": "QUOTA_SIGNALS_FILE_MISSING",
                "path": p.to_string_lossy(),
            }));
            self.set_slot_quota("agy", "QUOTA_SOURCE_UNAVAILABLE");
            self.set_slot_quota("codex-local", "QUOTA_SOURCE_UNAVAILABLE");
            return;
        }

        let content = match std::fs::read_to_string(p) {
            Ok(c) => c,
            Err(e) => {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "event": "QUOTA_SIGNALS_READ_ERROR",
                    "error": e.to_string(),
                }));
                self.set_slot_quota("agy", "QUOTA_SOURCE_UNAVAILABLE");
                self.set_slot_quota("codex-local", "QUOTA_SOURCE_UNAVAILABLE");
                return;
            }
        };

        let val: serde_json::Value = match serde_json::from_str(&content) {
            Ok(v) => v,
            Err(e) => {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "event": "QUOTA_SIGNALS_PARSE_ERROR",
                    "error": e.to_string(),
                }));
                self.set_slot_quota("agy", "QUOTA_SOURCE_UNAVAILABLE");
                self.set_slot_quota("codex-local", "QUOTA_SOURCE_UNAVAILABLE");
                return;
            }
        };

        // Strict fail-closed schema validation (Defect 5): Missing/incomplete fields fail-closed!
        let agy = match val.get("agy").and_then(|v| v.as_str()) {
            Some(s) if !s.trim().is_empty() => s,
            _ => {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "event": "QUOTA_SIGNALS_SCHEMA_INCOMPLETE",
                    "missing_field": "agy",
                }));
                self.set_slot_quota("agy", "QUOTA_SOURCE_UNAVAILABLE");
                self.set_slot_quota("codex-local", "QUOTA_SOURCE_UNAVAILABLE");
                return;
            }
        };

        let codex_val = match val.get("codex-local").and_then(|v| v.as_str()) {
            Some(s) if !s.trim().is_empty() => s,
            _ => {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "event": "QUOTA_SIGNALS_SCHEMA_INCOMPLETE",
                    "missing_field": "codex-local",
                }));
                self.set_slot_quota("agy", "QUOTA_SOURCE_UNAVAILABLE");
                self.set_slot_quota("codex-local", "QUOTA_SOURCE_UNAVAILABLE");
                return;
            }
        };

        let qa_starvation_risk = match val.get("cloud_qa_starvation_risk").and_then(|v| v.as_bool()) {
            Some(b) => b,
            None => {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "event": "QUOTA_SIGNALS_SCHEMA_INCOMPLETE",
                    "missing_field": "cloud_qa_starvation_risk",
                }));
                self.set_slot_quota("agy", "QUOTA_SOURCE_UNAVAILABLE");
                self.set_slot_quota("codex-local", "QUOTA_SOURCE_UNAVAILABLE");
                return;
            }
        };

        let chatgpt_pool_remaining = match val.get("chatgpt_pool_remaining").and_then(|v| v.as_u64()) {
            Some(n) => n,
            None => {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "event": "QUOTA_SIGNALS_SCHEMA_INCOMPLETE",
                    "missing_field": "chatgpt_pool_remaining",
                }));
                self.set_slot_quota("agy", "QUOTA_SOURCE_UNAVAILABLE");
                self.set_slot_quota("codex-local", "QUOTA_SOURCE_UNAVAILABLE");
                return;
            }
        };

        self.set_slot_quota("agy", agy);

        let mut codex = codex_val.to_string();
        // Section 3.4: Protect cloud QA ChatGPT pool from starvation by codex-local
        if qa_starvation_risk || chatgpt_pool_remaining < 20 {
            codex = "EXHAUSTED_TO_PROTECT_QA".to_string();
            let _ = self.ledger.record_decision(serde_json::json!({
                "event": "CODEX_LOCAL_THROTTLED_FOR_QA_RESERVE",
                "chatgpt_pool_remaining": chatgpt_pool_remaining,
            }));
        }
        self.set_slot_quota("codex-local", &codex);
    }

    pub fn is_pilot_passed(&self) -> bool {
        self.pilot_passed.lock().map(|p| *p).unwrap_or(false)
    }

    /// Wolfram Offline Model Inference (§5.1, §5.2)
    pub fn infer_local_model_decision(
        &self,
        task: &TaskCandidate,
    ) -> Option<ModelInferenceDecision> {
        let verdict = validate_canonical_rules(
            task.project.as_deref(),
            &task.title,
            &task.description,
            task.status.as_deref(),
            task.tier.as_deref(),
            task.assignee.as_deref(),
        );

        let tier = verdict.extracted_tier.unwrap_or_else(|| task.tier.clone().unwrap_or_else(|| "ВП".to_string()));
        let status = task.status.as_deref().unwrap_or("");
        let agy_quota = self.slot_quota_status.lock().ok()
            .and_then(|m| m.get("agy").cloned())
            .unwrap_or_else(|| "OK".to_string());
        let codex_quota = self.slot_quota_status.lock().ok()
            .and_then(|m| m.get("codex-local").cloned())
            .unwrap_or_else(|| "OK".to_string());
        let cloud_qa_risk = codex_quota == "EXHAUSTED_TO_PROTECT_QA";
        let chatgpt_pool = if cloud_qa_risk { 10 } else { 100 };

        match self.model_engine.infer(
            &tier,
            status,
            task.wait_time_secs,
            &agy_quota,
            &codex_quota,
            cloud_qa_risk,
            chatgpt_pool,
        ) {
            Ok(decision) => Some(decision),
            Err(err) => {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "event": "MODEL_ROUTER_UNAVAILABLE",
                    "reason": err,
                    "fallback": "deterministic_rules",
                }));
                None
            }
        }
    }

    /// Deterministic fallback routing rules (§3–§4, §5.2)
    pub fn fallback_deterministic_routing(&self, task: &TaskCandidate) -> Option<String> {
        let verdict = validate_canonical_rules(
            task.project.as_deref(),
            &task.title,
            &task.description,
            task.status.as_deref(),
            task.tier.as_deref(),
            task.assignee.as_deref(),
        );

        let tier = verdict.extracted_tier.unwrap_or_else(|| task.tier.clone().unwrap_or_else(|| "ВП".to_string()));
        let status = task.status.as_deref().unwrap_or("");
        let agy_quota = self.slot_quota_status.lock().ok()
            .and_then(|m| m.get("agy").cloned())
            .unwrap_or_else(|| "OK".to_string());

        // Fallback conditions: Tier is ВС or ВП, and wait > 2h in Todo OR agy quota exhausted
        let time_trigger = task.wait_time_secs > 7200 && status.eq_ignore_ascii_case("Todo");
        let quota_trigger = agy_quota != "OK";

        if (tier == "ВС" || tier == "ВП") && (time_trigger || quota_trigger) {
            Some("codex-local".to_string())
        } else {
            Some("agy".to_string())
        }
    }

    pub fn route_and_claim_task(
        &self,
        task: &TaskCandidate,
        lease_ttl_secs: u64,
        commit_sha: Option<&str>,
    ) -> RoutingDecision {
        let model_decision = self.infer_local_model_decision(task);
        let suggested_slot = match &model_decision {
            Some(dec) => Some(dec.recommended_slot.clone()),
            None => self.fallback_deterministic_routing(task),
        };
        let router_mode = if model_decision.is_some() {
            "model_offline"
        } else {
            "model_offline_fallback_rule"
        };

        self.route_and_claim_task_internal(
            task,
            lease_ttl_secs,
            suggested_slot,
            router_mode,
            commit_sha,
        )
    }

    pub fn route_and_claim_task_with_model(
        &self,
        task: &TaskCandidate,
        lease_ttl_secs: u64,
        commit_sha: Option<&str>,
        model_fn: &dyn Fn(&TaskCandidate) -> Option<String>,
    ) -> RoutingDecision {
        let suggested_slot = model_fn(task);
        let router_mode = if suggested_slot.is_some() {
            "model_offline"
        } else {
            "model_offline_fallback_rule"
        };
        self.route_and_claim_task_internal(
            task,
            lease_ttl_secs,
            suggested_slot,
            router_mode,
            commit_sha,
        )
    }

    fn route_and_claim_task_internal(
        &self,
        task: &TaskCandidate,
        lease_ttl_secs: u64,
        suggested_slot: Option<String>,
        router_mode: &str,
        commit_sha: Option<&str>,
    ) -> RoutingDecision {
        // 1. Canonical validation gate check
        let verdict = validate_canonical_rules(
            task.project.as_deref(),
            &task.title,
            &task.description,
            task.status.as_deref(),
            task.tier.as_deref(),
            task.assignee.as_deref(),
        );

        if !verdict.passed {
            if let Err(e) = self.ledger.record_decision(serde_json::json!({
                "task_id": task.id,
                "event": "CANONICAL_GATE_REJECTED",
                "reason": verdict.reason,
                "rework_comment": verdict.rework_comment,
                "tier": task.tier,
            })) {
                return RoutingDecision {
                    success: false,
                    slot: None,
                    reason: format!("LEDGER_RECORD_FAILED: {}", e),
                    rework_comment: None,
                };
            }
            return RoutingDecision {
                success: false,
                slot: None,
                reason: verdict.reason,
                rework_comment: verdict.rework_comment,
            };
        }

        // 2. Check for active durable lease (single-lease protection)
        match self.lease_manager.get_lease(&task.id) {
            Ok(Some(existing)) => {
                if let Err(e) = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": "DOUBLE_CLAIM_REJECTED",
                    "slot": existing.slot,
                    "commit_sha": existing.commit_sha,
                })) {
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason: format!("LEDGER_RECORD_FAILED: {}", e),
                        rework_comment: None,
                    };
                }
                return RoutingDecision {
                    success: false,
                    slot: None,
                    reason: "DOUBLE_CLAIM_REJECTED".to_string(),
                    rework_comment: None,
                };
            }
            Err(e) => {
                // Corrupted lease storage -> fail-closed!
                return RoutingDecision {
                    success: false,
                    slot: None,
                    reason: format!("CORRUPTED_LEASE_STORAGE: {}", e),
                    rework_comment: None,
                };
            }
            Ok(None) => {}
        }

        // 3. Local offline model routing decision
        if suggested_slot.is_none() {
            if let Err(e) = self.ledger.record_decision(serde_json::json!({
                "task_id": task.id,
                "event": "MODEL_ROUTER_UNAVAILABLE",
                "fallback": "deterministic_rules",
            })) {
                return RoutingDecision {
                    success: false,
                    slot: None,
                    reason: format!("LEDGER_RECORD_FAILED: {}", e),
                    rework_comment: None,
                };
            }
        }

        let tier = verdict.extracted_tier.unwrap_or_else(|| "ВП".to_string());
        let status = task.status.as_deref().unwrap_or("");
        let agy_quota = self.slot_quota_status.lock().ok()
            .and_then(|m| m.get("agy").cloned())
            .unwrap_or_else(|| "OK".to_string());
        let codex_quota = self.slot_quota_status.lock().ok()
            .and_then(|m| m.get("codex-local").cloned())
            .unwrap_or_else(|| "OK".to_string());
        let codex_health = self.slot_health_status.lock().ok()
            .and_then(|m| m.get("codex-local").cloned())
            .unwrap_or_else(|| "HEALTH_OK".to_string());
        let pilot_ok = self.is_pilot_passed();

        let time_trigger = task.wait_time_secs > 7200 && status.eq_ignore_ascii_case("Todo");
        let quota_trigger = agy_quota != "OK";
        let is_candidate_by_policy = (tier == "ВС" || tier == "ВП")
            && (time_trigger || quota_trigger)
            && codex_health == "HEALTH_OK"
            && codex_quota == "OK";

        let is_fallback_candidate = is_candidate_by_policy && pilot_ok;

        // 4. Resolve target slot
        let target_slot: String;
        if suggested_slot.as_deref() == Some("codex-local") {
            if is_fallback_candidate {
                target_slot = "codex-local".to_string();
            } else if is_candidate_by_policy && !pilot_ok {
                if let Err(e) = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": "PILOT_GATE_BLOCKED",
                    "slot": "codex-local",
                    "reason": "PILOT_NOT_PASSED",
                })) {
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason: format!("LEDGER_RECORD_FAILED: {}", e),
                        rework_comment: None,
                    };
                }
                return RoutingDecision {
                    success: false,
                    slot: None,
                    reason: "PILOT_GATE_BLOCKED_FALLBACK_DISABLED".to_string(),
                    rework_comment: None,
                };
            } else {
                if agy_quota != "OK" {
                    let reason = "SLOT_QUOTA_EXHAUSTED_AGY".to_string();
                    if let Err(e) = self.ledger.record_decision(serde_json::json!({
                        "task_id": task.id,
                        "event": "QUOTA_FAIL_CLOSED",
                        "slot": "agy",
                        "reason": &reason,
                    })) {
                        return RoutingDecision {
                            success: false,
                            slot: None,
                            reason: format!("LEDGER_RECORD_FAILED: {}", e),
                            rework_comment: None,
                        };
                    }
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason,
                        rework_comment: None,
                    };
                }
                target_slot = "agy".to_string();
            }
        } else {
            if agy_quota != "OK" {
                if is_fallback_candidate {
                    target_slot = "codex-local".to_string();
                } else if is_candidate_by_policy && !pilot_ok {
                    if let Err(e) = self.ledger.record_decision(serde_json::json!({
                        "task_id": task.id,
                        "event": "PILOT_GATE_BLOCKED",
                        "slot": "codex-local",
                        "reason": "PILOT_NOT_PASSED",
                    })) {
                        return RoutingDecision {
                            success: false,
                            slot: None,
                            reason: format!("LEDGER_RECORD_FAILED: {}", e),
                            rework_comment: None,
                        };
                    }
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason: "PILOT_GATE_BLOCKED_FALLBACK_DISABLED".to_string(),
                        rework_comment: None,
                    };
                } else {
                    let reason = "SLOT_QUOTA_EXHAUSTED_AGY".to_string();
                    if let Err(e) = self.ledger.record_decision(serde_json::json!({
                        "task_id": task.id,
                        "event": "QUOTA_FAIL_CLOSED",
                        "slot": "agy",
                        "reason": &reason,
                    })) {
                        return RoutingDecision {
                            success: false,
                            slot: None,
                            reason: format!("LEDGER_RECORD_FAILED: {}", e),
                            rework_comment: None,
                        };
                    }
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason,
                        rework_comment: None,
                    };
                }
            } else {
                target_slot = "agy".to_string();
            }
        }

        // 5. Final quota check
        let target_quota = if target_slot == "agy" { &agy_quota } else { &codex_quota };
        if target_quota != "OK" {
            let reason = format!("SLOT_QUOTA_EXHAUSTED_{}", target_slot.to_uppercase());
            if let Err(e) = self.ledger.record_decision(serde_json::json!({
                "task_id": task.id,
                "event": "QUOTA_FAIL_CLOSED",
                "slot": target_slot,
                "reason": &reason,
            })) {
                return RoutingDecision {
                    success: false,
                    slot: None,
                    reason: format!("LEDGER_RECORD_FAILED: {}", e),
                    rework_comment: None,
                };
            }
            return RoutingDecision {
                success: false,
                slot: None,
                reason,
                rework_comment: None,
            };
        }

        // 6. Pacing check for agy (minimum 300 seconds between ALL agy runs)
        let now = Self::now_secs();
        let last_start = self.last_agy_start.lock().map(|g| *g).unwrap_or(0.0);
        if target_slot == "agy" && (now - last_start) < self.pacing_secs as f64 {
            if let Err(e) = self.ledger.record_decision(serde_json::json!({
                "task_id": task.id,
                "event": "PACING_BACKOFF",
                "slot": "agy",
            })) {
                return RoutingDecision {
                    success: false,
                    slot: None,
                    reason: format!("LEDGER_RECORD_FAILED: {}", e),
                    rework_comment: None,
                };
            }
            return RoutingDecision {
                success: false,
                slot: None,
                reason: "PACING_BACKOFF".to_string(),
                rework_comment: None,
            };
        }

        // 7. Acquire durable lease atomically (with atomic fallback cap=1 guard)
        let max_fallback = if target_slot == "codex-local" { Some(1) } else { None };
        match self.lease_manager.acquire_lease_atomic(&task.id, &target_slot, lease_ttl_secs, commit_sha, max_fallback) {
            Ok(Ok(())) => {
                if target_slot == "agy" {
                    if let Ok(mut g) = self.last_agy_start.lock() {
                        *g = now;
                    }
                }
                if let Err(e) = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": "TASK_CLAIMED",
                    "slot": target_slot,
                    "router_mode": router_mode,
                    "commit_sha": commit_sha,
                })) {
                    let _ = self.lease_manager.release_lease(&task.id);
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason: format!("LEDGER_RECORD_FAILED: {}", e),
                        rework_comment: None,
                    };
                }
                let reason = if router_mode == "model_offline_fallback_rule" || suggested_slot.is_none() {
                    "deterministic_fallback".to_string()
                } else {
                    "CLAIM_GRANTED".to_string()
                };
                RoutingDecision {
                    success: true,
                    slot: Some(target_slot),
                    reason,
                    rework_comment: None,
                }
            }
            Ok(Err(reason)) => {
                if let Err(e) = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": &reason,
                    "slot": target_slot,
                })) {
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason: format!("LEDGER_RECORD_FAILED: {}", e),
                        rework_comment: None,
                    };
                }
                RoutingDecision {
                    success: false,
                    slot: None,
                    reason,
                    rework_comment: None,
                }
            }
            Err(e) => {
                if let Err(le) = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": "LEASE_ACQUIRE_ERROR",
                    "error": &e,
                })) {
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason: format!("LEDGER_RECORD_FAILED: {}", le),
                        rework_comment: None,
                    };
                }
                RoutingDecision {
                    success: false,
                    slot: None,
                    reason: format!("LEASE_ACQUIRE_ERROR: {}", e),
                    rework_comment: None,
                }
            }
        }
    }
}

pub fn find_manifest_path() -> Option<&'static str> {
    let candidate_paths = [
        "fleetd-evolution/evidence/MANIFEST.sha256",
        "evidence/MANIFEST.sha256",
        "../../fleetd-evolution/evidence/MANIFEST.sha256",
        "../../evidence/MANIFEST.sha256",
    ];
    for p in candidate_paths {
        if std::path::Path::new(p).exists() {
            return Some(p);
        }
    }
    None
}

pub fn verify_manifest_root(manifest_path: &str) -> Result<String, String> {
    let bytes = std::fs::read(manifest_path).map_err(|e| format!("MANIFEST_READ_ERROR: {}", e))?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    let computed_manifest_sha = hex::encode(hasher.finalize());

    let trusted = match std::env::var("FLEETD_TRUSTED_MANIFEST_SHA256") {
        Ok(t) if !t.trim().is_empty() => t.trim().to_string(),
        _ => {
            return Err("MANIFEST_ROOT_UNAUTHENTICATED: FLEETD_TRUSTED_MANIFEST_SHA256 root of trust is required but not set".to_string());
        }
    };

    if computed_manifest_sha != trusted {
        return Err(format!(
            "MANIFEST_ROOT_UNAUTHENTICATED: computed manifest hash {} does not match trusted {}",
            computed_manifest_sha, trusted
        ));
    }
    Ok(computed_manifest_sha)
}

pub fn find_manifest_entry(filename: &str) -> Result<String, String> {
    let manifest_path = find_manifest_path()
        .ok_or_else(|| "MANIFEST_NOT_FOUND: evidence/MANIFEST.sha256 not accessible".to_string())?;

    let _ = verify_manifest_root(manifest_path)?;

    let content = std::fs::read_to_string(manifest_path)
        .map_err(|e| format!("MANIFEST_READ_ERROR: {}", e))?;

    for line in content.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() == 2 && parts[1].ends_with(filename) {
            return Ok(parts[0].to_string());
        }
    }
    Err(format!(
        "RECEIPT_NOT_IN_MANIFEST: receipt file '{}' is not present in authenticated manifest",
        filename
    ))
}

pub fn verify_pilot_receipt<P: AsRef<std::path::Path>>(receipt_path: P) -> (bool, String) {
    let p = receipt_path.as_ref();
    if !p.exists() {
        return (false, format!("Receipt file not found: {:?}", p));
    }
    let bytes = match std::fs::read(p) {
        Ok(b) => b,
        Err(e) => return (false, format!("Failed to read receipt: {}", e)),
    };

    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    let computed_sha = hex::encode(hasher.finalize());

    let val: serde_json::Value = match serde_json::from_slice(&bytes) {
        Ok(v) => v,
        Err(e) => return (false, format!("Corrupted receipt JSON: {}", e)),
    };

    let filename = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
    // Strict manifest enforcement (Defect 3 & 4): receipt must be authenticated in manifest!
    let expected_sha = match find_manifest_entry(filename) {
        Ok(sha) => sha,
        Err(err) => return (false, err),
    };

    if computed_sha != expected_sha {
        return (
            false,
            format!(
                "RECEIPT_CHECKSUM_MISMATCH: computed {} does not match manifest {}",
                computed_sha, expected_sha
            ),
        );
    }

    let pilot_id = val.get("pilot_task_id").and_then(|v| v.as_str()).unwrap_or("");
    if pilot_id != "MIN-221" {
        return (false, format!("Unexpected pilot task ID: {}", pilot_id));
    }

    // Verify git coordinates
    let git_coords = val.get("git_coordinates");
    let repo_url = git_coords.and_then(|g| g.get("repo_url")).and_then(|v| v.as_str()).unwrap_or("");
    let branch = git_coords.and_then(|g| g.get("branch")).and_then(|v| v.as_str()).unwrap_or("");
    let commit_sha = git_coords.and_then(|g| g.get("commit_sha")).and_then(|v| v.as_str()).unwrap_or("");

    if repo_url != "https://github.com/pashamango/math.git"
        || branch != "collatz-v0.1.0-m3"
        || commit_sha != "16590a7574b188225896cdf99268bc476cbe18a3"
    {
        return (
            false,
            format!(
                "Pilot git coordinates mismatch: repo={}, branch={}, commit={}",
                repo_url, branch, commit_sha
            ),
        );
    }

    // Verify authenticated reviewer identity (Codex Linear UUID)
    let reviewer = val.pointer("/independent_qa_audit/reviewer").and_then(|v| v.as_str()).unwrap_or("");
    let reviewer_id = val.pointer("/independent_qa_audit/reviewer_id").and_then(|v| v.as_str()).unwrap_or("");
    let authorized_reviewer_id = "3bc09df8-b112-4d8d-8d0c-519043c77030";

    if reviewer_id.is_empty() || reviewer_id != authorized_reviewer_id {
        return (
            false,
            format!(
                "UNAUTHORIZED_REVIEWER: reviewer_id '{}' is missing or does not match authorized independent QA UUID '{}'",
                reviewer_id, authorized_reviewer_id
            ),
        );
    }

    if !(reviewer.contains("Codex") || reviewer.contains("Independent QA")) {
        return (
            false,
            format!(
                "UNAUTHORIZED_REVIEWER: reviewer name '{}' does not match authorized independent QA",
                reviewer
            ),
        );
    }

    let verdict = val.pointer("/independent_qa_audit/verdict").and_then(|v| v.as_str()).unwrap_or("");
    if verdict.eq_ignore_ascii_case("PASS") {
        (true, "PILOT_QA_PASS".to_string())
    } else {
        (false, format!("Pilot independent QA verdict is '{}' (not PASS)", verdict))
    }
}

pub fn resolve_handoff_commit(_cwd: Option<&std::path::Path>, issue_text: &str) -> Option<String> {
    if let Some(pos) = issue_text.find("commit_sha") {
        let after = &issue_text[pos + 10..];
        for word in after.split(|c: char| c == '"' || c == '\'' || c == '`' || c == ':' || c.is_whitespace()) {
            if word.len() == 40 && word.chars().all(|c| c.is_ascii_hexdigit()) {
                return Some(word.to_string());
            }
        }
    }
    for word in issue_text.split(|c: char| c == '`' || c == '"' || c == '\'' || c.is_whitespace()) {
        if word.len() == 40 && word.chars().all(|c| c.is_ascii_hexdigit()) {
            return Some(word.to_string());
        }
    }
    None
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq)]
pub struct HistoricalCase {
    pub id: String,
    pub title: String,
    pub project: String,
    pub description: String,
    pub tier: String,
    pub status: String,
    pub wait_time_secs: u64,
    pub assignee: Option<String>,
    pub agy_quota: Option<String>,
    pub expected_outcome: String,
}

pub fn get_historical_test_suite() -> Vec<HistoricalCase> {
    vec![
        HistoricalCase {
            id: "MIN-124".to_string(),
            title: "InsightCodex: Riemann null-models and holdout stress-test".to_string(),
            project: "MathLab".to_string(),
            description: "Assignee: Codex\nTier: ВС\nInvestigation of Riemann zeros.\n@Codex please review null-model bounds.".to_string(),
            tier: "ВС".to_string(),
            status: "Todo".to_string(),
            wait_time_secs: 1800,
            assignee: Some("Codex".to_string()),
            agy_quota: None,
            expected_outcome: "REJECT_CLOUD_LANE".to_string(),
        },
        HistoricalCase {
            id: "MIN-173".to_string(),
            title: "Complex dev program research".to_string(),
            project: "Infrastructure".to_string(),
            description: "Builder: codex-cloud\nTier: ВП\nAmbiguous builder marker.".to_string(),
            tier: "ВП".to_string(),
            status: "Todo".to_string(),
            wait_time_secs: 500,
            assignee: None,
            agy_quota: None,
            expected_outcome: "REWORK_AMBIGUOUS_MARKER".to_string(),
        },
        HistoricalCase {
            id: "MIN-213".to_string(),
            title: "Cartesia Software Engineer Agent Harness".to_string(),
            project: "Поиск работы".to_string(),
            description: "Builder: agy\nTier: МП\nStatus: submitted cv-ready awaiting-tap. Application was sent yesterday.".to_string(),
            tier: "МП".to_string(),
            status: "Todo".to_string(),
            wait_time_secs: 300,
            assignee: None,
            agy_quota: None,
            expected_outcome: "FROZEN_SUBMITTED".to_string(),
        },
        HistoricalCase {
            id: "MIN-221".to_string(),
            title: "Collatz v0.1.0 — M3: CacheCheck".to_string(),
            project: "Project Collatz".to_string(),
            description: "Builder: agy\nTier: ВП\nCacheCheck verification.".to_string(),
            tier: "ВП".to_string(),
            status: "Todo".to_string(),
            wait_time_secs: 7500,
            assignee: None,
            agy_quota: None,
            expected_outcome: "CLAIM_FALLBACK_CODEX".to_string(),
        },
        HistoricalCase {
            id: "MIN-219".to_string(),
            title: "Collatz v0.1.0 — M0/M1 Lake Skeleton".to_string(),
            project: "Project Collatz".to_string(),
            description: "Builder: agy\nTier: ВС\nFoundation lake build.".to_string(),
            tier: "ВС".to_string(),
            status: "Todo".to_string(),
            wait_time_secs: 1000,
            assignee: None,
            agy_quota: Some("QUOTA_EXHAUSTED_429".to_string()),
            expected_outcome: "CLAIM_FALLBACK_QUOTA".to_string(),
        },
    ]
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize, PartialEq)]
pub struct PolicyMetrics {
    pub on_time_ratio: f64,
    pub double_claims: usize,
    pub missed_triggers: usize,
    pub uncanonical_dispatched: usize,
}

pub fn evaluate_policy_metrics(
    router: &SmartRouter,
    test_suite: Option<&[HistoricalCase]>,
    custom_model_decision: Option<&dyn Fn(&TaskCandidate) -> Option<String>>,
) -> PolicyMetrics {
    let default_suite = get_historical_test_suite();
    let suite = test_suite.unwrap_or(&default_suite);

    let mut total_vs_vp = 0;
    let mut on_time_count = 0;
    let mut double_claims = 0;
    let mut missed_triggers = 0;
    let mut uncanonical_dispatched = 0;

    for item in suite {
        let task_cand = TaskCandidate {
            id: item.id.clone(),
            title: item.title.clone(),
            description: item.description.clone(),
            project: Some(item.project.clone()),
            tier: Some(item.tier.clone()),
            status: Some(item.status.clone()),
            assignee: item.assignee.clone(),
            wait_time_secs: item.wait_time_secs,
        };

        let orig_quota = router.slot_quota_status.lock().ok()
            .and_then(|m| m.get("agy").cloned())
            .unwrap_or_else(|| "OK".to_string());

        if let Some(q) = &item.agy_quota {
            router.set_slot_quota("agy", q);
        }

        let is_vs_vp = item.tier == "ВС" || item.tier == "ВП";
        if is_vs_vp {
            total_vs_vp += 1;
        }

        let decision = if let Some(custom_fn) = custom_model_decision {
            router.route_and_claim_task_with_model(&task_cand, 3600, None, custom_fn)
        } else {
            router.route_and_claim_task(&task_cand, 3600, None)
        };

        if item.agy_quota.is_some() {
            router.set_slot_quota("agy", &orig_quota);
        }

        if ["REJECT_CLOUD_LANE", "REWORK_AMBIGUOUS_MARKER", "FROZEN_SUBMITTED"].contains(&item.expected_outcome.as_str()) {
            if decision.success {
                uncanonical_dispatched += 1;
            }
        }

        if ["CLAIM_FALLBACK_CODEX", "CLAIM_FALLBACK_QUOTA"].contains(&item.expected_outcome.as_str()) {
            if decision.success && decision.slot.as_deref() == Some("codex-local") {
                on_time_count += 1;
            } else {
                missed_triggers += 1;
            }
        }

        if decision.success {
            let second_decision = router.route_and_claim_task(&task_cand, 3600, None);
            if second_decision.success {
                double_claims += 1;
            }
        }

        let _ = router.lease_manager.release_lease(&item.id);
    }

    let on_time_ratio = if total_vs_vp > 0 {
        on_time_count as f64 / total_vs_vp as f64
    } else {
        1.0
    };

    PolicyMetrics {
        on_time_ratio,
        double_claims,
        missed_triggers,
        uncanonical_dispatched,
    }
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PolicyUpdateResult {
    pub status: String,
    pub policy_accepted: bool,
    pub baseline_metrics: PolicyMetrics,
    pub candidate_metrics: PolicyMetrics,
    pub reason: String,
}

pub fn evaluate_and_apply_policy_update(
    router: &SmartRouter,
    candidate_model_fn: &dyn Fn(&TaskCandidate) -> Option<String>,
    baseline_metrics: Option<PolicyMetrics>,
    test_suite: Option<&[HistoricalCase]>,
) -> PolicyUpdateResult {
    let baseline = baseline_metrics.unwrap_or_else(|| evaluate_policy_metrics(router, test_suite, None));
    let candidate = evaluate_policy_metrics(router, test_suite, Some(candidate_model_fn));

    let is_degraded = candidate.double_claims > baseline.double_claims
        || candidate.missed_triggers > baseline.missed_triggers
        || candidate.uncanonical_dispatched > baseline.uncanonical_dispatched
        || candidate.on_time_ratio < baseline.on_time_ratio;

    if is_degraded {
        let _ = router.ledger.record_decision(serde_json::json!({
            "event": "POLICY_DEGRADATION_ROLLBACK",
            "baseline": baseline,
            "candidate": candidate,
            "status": "ROLLED_BACK_TO_BASELINE",
        }));
        PolicyUpdateResult {
            status: "ROLLED_BACK".to_string(),
            policy_accepted: false,
            baseline_metrics: baseline,
            candidate_metrics: candidate,
            reason: "Candidate degraded metrics compared to baseline; automatic rollback executed.".to_string(),
        }
    } else {
        let _ = router.ledger.record_decision(serde_json::json!({
            "event": "POLICY_UPDATE_ACCEPTED",
            "baseline": baseline,
            "candidate": candidate,
            "status": "ACCEPTED",
        }));
        PolicyUpdateResult {
            status: "ACCEPTED".to_string(),
            policy_accepted: true,
            baseline_metrics: baseline,
            candidate_metrics: candidate,
            reason: "Candidate policy verified and accepted".to_string(),
        }
    }
}
