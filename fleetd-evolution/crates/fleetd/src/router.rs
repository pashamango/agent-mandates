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
            let _ = self.ledger.record_decision(serde_json::json!({
                "task_id": task.id,
                "event": "CANONICAL_GATE_REJECTED",
                "reason": verdict.reason,
                "rework_comment": verdict.rework_comment,
                "tier": task.tier,
            }));
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
                let _ = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": "DOUBLE_CLAIM_REJECTED",
                    "slot": existing.slot,
                    "commit_sha": existing.commit_sha,
                }));
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
        let suggested_slot = self.simulate_local_model_decision(task, model_timeout);
        if suggested_slot.is_none() {
            let _ = self.ledger.record_decision(serde_json::json!({
                "task_id": task.id,
                "event": "MODEL_ROUTER_UNAVAILABLE",
                "fallback": "deterministic_rules",
            }));
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
        let fallback_count = self.lease_manager.count_active_fallback().unwrap_or(0);
        let pilot_ok = self.is_pilot_passed();

        let time_trigger = task.wait_time_secs > 7200 && status.eq_ignore_ascii_case("Todo");
        let quota_trigger = agy_quota != "OK";
        let is_candidate_by_policy = (tier == "ВС" || tier == "ВП")
            && (time_trigger || quota_trigger)
            && codex_health == "HEALTH_OK"
            && codex_quota == "OK"
            && fallback_count < 1;

        let is_fallback_candidate = is_candidate_by_policy && pilot_ok;

        // 4. Resolve target slot
        let target_slot: String;
        if suggested_slot.as_deref() == Some("codex-local") {
            if is_fallback_candidate {
                target_slot = "codex-local".to_string();
            } else if is_candidate_by_policy && !pilot_ok {
                let _ = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": "PILOT_GATE_BLOCKED",
                    "slot": "codex-local",
                    "reason": "PILOT_NOT_PASSED",
                }));
                return RoutingDecision {
                    success: false,
                    slot: None,
                    reason: "PILOT_GATE_BLOCKED_FALLBACK_DISABLED".to_string(),
                    rework_comment: None,
                };
            } else {
                if agy_quota != "OK" {
                    let reason = "SLOT_QUOTA_EXHAUSTED_AGY".to_string();
                    let _ = self.ledger.record_decision(serde_json::json!({
                        "task_id": task.id,
                        "event": "QUOTA_FAIL_CLOSED",
                        "slot": "agy",
                        "reason": &reason,
                    }));
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
                    let _ = self.ledger.record_decision(serde_json::json!({
                        "task_id": task.id,
                        "event": "PILOT_GATE_BLOCKED",
                        "slot": "codex-local",
                        "reason": "PILOT_NOT_PASSED",
                    }));
                    return RoutingDecision {
                        success: false,
                        slot: None,
                        reason: "PILOT_GATE_BLOCKED_FALLBACK_DISABLED".to_string(),
                        rework_comment: None,
                    };
                } else {
                    let reason = "SLOT_QUOTA_EXHAUSTED_AGY".to_string();
                    let _ = self.ledger.record_decision(serde_json::json!({
                        "task_id": task.id,
                        "event": "QUOTA_FAIL_CLOSED",
                        "slot": "agy",
                        "reason": &reason,
                    }));
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
            let _ = self.ledger.record_decision(serde_json::json!({
                "task_id": task.id,
                "event": "QUOTA_FAIL_CLOSED",
                "slot": target_slot,
                "reason": &reason,
            }));
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
            let _ = self.ledger.record_decision(serde_json::json!({
                "task_id": task.id,
                "event": "PACING_BACKOFF",
                "slot": "agy",
            }));
            return RoutingDecision {
                success: false,
                slot: None,
                reason: "PACING_BACKOFF".to_string(),
                rework_comment: None,
            };
        }

        // 7. Acquire durable lease
        match self.lease_manager.acquire_lease(&task.id, &target_slot, lease_ttl_secs, commit_sha) {
            Ok(true) => {
                if target_slot == "agy" {
                    if let Ok(mut g) = self.last_agy_start.lock() {
                        *g = now;
                    }
                }
                let _ = self.ledger.record_decision(serde_json::json!({
                    "task_id": task.id,
                    "event": "TASK_CLAIMED",
                    "slot": target_slot,
                    "commit_sha": commit_sha,
                }));
                RoutingDecision {
                    success: true,
                    slot: Some(target_slot),
                    reason: if suggested_slot.is_none() { "deterministic_fallback".to_string() } else { "CLAIM_GRANTED".to_string() },
                    rework_comment: None,
                }
            }
            Ok(false) => {
                RoutingDecision {
                    success: false,
                    slot: None,
                    reason: "DOUBLE_CLAIM_REJECTED".to_string(),
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
