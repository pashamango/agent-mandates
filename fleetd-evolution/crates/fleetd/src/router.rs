//! Smart Router (fleetd vNext - Sections 3, 5 & 6)
//! Multi-lane routing: agy (primary) vs codex-local (fallback) vs codex-cloud (isolated external).
//! Single-lease atomic protection against double claims.
//! Fail-closed quota guards and pilot gate.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::canonical::validate_canonical_rules;
use crate::lease::DurableLeaseManager;
use crate::ledger::RoutingLedger;

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

        Self {
            pacing_secs,
            last_agy_start: Mutex::new(0.0),
            slot_quota_status: Mutex::new(quotas),
            slot_health_status: Mutex::new(healths),
            pilot_passed: Mutex::new(pilot_passed),
            lease_manager,
            ledger,
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

    pub fn refresh_pilot_gate<P: AsRef<std::path::Path>>(&self, receipt_path: P) -> bool {
        let (passed, _) = verify_pilot_receipt(receipt_path);
        self.set_pilot_passed(passed);
        passed
    }

    pub fn refresh_slot_health<P: AsRef<std::path::Path>>(&self, codex_bin: P) {
        let p = codex_bin.as_ref();
        if !p.exists() {
            self.set_slot_health("codex-local", "HEALTH_FAILED");
            return;
        }
        let output = std::process::Command::new(p)
            .arg("--version")
            .output();
        match output {
            Ok(o) if o.status.success() => {
                self.set_slot_health("codex-local", "HEALTH_OK");
            }
            _ => {
                self.set_slot_health("codex-local", "HEALTH_FAILED");
            }
        }
    }

    pub fn refresh_quota_signals<P: AsRef<std::path::Path>>(&self, signals_path: P) {
        let p = signals_path.as_ref();
        if p.exists() {
            if let Ok(content) = std::fs::read_to_string(p) {
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&content) {
                    if let Some(agy) = val.get("agy").and_then(|v| v.as_str()) {
                        self.set_slot_quota("agy", agy);
                    }
                    if let Some(codex) = val.get("codex-local").and_then(|v| v.as_str()) {
                        self.set_slot_quota("codex-local", codex);
                    }
                }
            }
        }
    }

    pub fn is_pilot_passed(&self) -> bool {
        self.pilot_passed.lock().map(|p| *p).unwrap_or(false)
    }

    pub fn simulate_local_model_decision(&self, task: &TaskCandidate, timeout_simulate: bool) -> Option<String> {
        if timeout_simulate {
            return None;
        }

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
        model_timeout: bool,
        commit_sha: Option<&str>,
    ) -> RoutingDecision {
        self.route_and_claim_task_with_model(
            task,
            lease_ttl_secs,
            model_timeout,
            commit_sha,
            &|t| self.simulate_local_model_decision(t, model_timeout),
        )
    }

    pub fn route_and_claim_task_with_model(
        &self,
        task: &TaskCandidate,
        lease_ttl_secs: u64,
        model_timeout: bool,
        commit_sha: Option<&str>,
        model_fn: &dyn Fn(&TaskCandidate) -> Option<String>,
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
        let suggested_slot = if model_timeout { None } else { model_fn(task) };
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

        // 6. Pacing check for agy
        let now = Self::now_secs();
        let last_start = self.last_agy_start.lock().map(|g| *g).unwrap_or(0.0);
        if target_slot == "agy" && (now - last_start) < self.pacing_secs as f64 && task.wait_time_secs < 7200 {
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
                RoutingDecision {
                    success: true,
                    slot: Some(target_slot),
                    reason: if suggested_slot.is_none() { "deterministic_fallback".to_string() } else { "CLAIM_GRANTED".to_string() },
                    rework_comment: None,
                }
            }
            Ok(Err(reason)) => {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": &reason,
                    "slot": target_slot,
                }));
                RoutingDecision {
                    success: false,
                    slot: None,
                    reason,
                    rework_comment: None,
                }
            }
            Err(e) => {
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

pub fn verify_pilot_receipt<P: AsRef<std::path::Path>>(receipt_path: P) -> (bool, String) {
    let p = receipt_path.as_ref();
    if !p.exists() {
        return (false, format!("Receipt file not found: {:?}", p));
    }
    let content = match std::fs::read_to_string(p) {
        Ok(c) => c,
        Err(e) => return (false, format!("Failed to read receipt: {}", e)),
    };
    let val: serde_json::Value = match serde_json::from_str(&content) {
        Ok(v) => v,
        Err(e) => return (false, format!("Corrupted receipt JSON: {}", e)),
    };
    let pilot_id = val.get("pilot_task_id").and_then(|v| v.as_str()).unwrap_or("");
    if pilot_id != "MIN-221" {
        return (false, format!("Unexpected pilot task ID: {}", pilot_id));
    }
    let verdict = val.pointer("/independent_qa_audit/verdict").and_then(|v| v.as_str()).unwrap_or("");
    if verdict.eq_ignore_ascii_case("PASS") {
        (true, "PILOT_QA_PASS".to_string())
    } else {
        (false, format!("Pilot independent QA verdict is '{}' (not PASS)", verdict))
    }
}

pub fn resolve_handoff_commit(cwd: Option<&std::path::Path>, issue_text: &str) -> Option<String> {
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

    let path = cwd.unwrap_or_else(|| std::path::Path::new("."));
    if let Ok(output) = std::process::Command::new("git")
        .args(["-C", path.to_str().unwrap_or("."), "rev-parse", "HEAD"])
        .output()
    {
        if output.status.success() {
            let sha = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if sha.len() == 40 && sha.chars().all(|c| c.is_ascii_hexdigit()) {
                return Some(sha);
            }
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
            router.route_and_claim_task_with_model(&task_cand, 3600, false, None, custom_fn)
        } else {
            router.route_and_claim_task(&task_cand, 3600, false, None)
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
            let second_decision = router.route_and_claim_task(&task_cand, 3600, false, None);
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
