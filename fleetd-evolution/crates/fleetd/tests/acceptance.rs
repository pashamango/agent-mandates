use std::sync::Arc;
use std::fs;

use fleetd::canonical::validate_canonical_rules;
use fleetd::lease::DurableLeaseManager;
use fleetd::ledger::RoutingLedger;
use fleetd::router::{SmartRouter, TaskCandidate};

fn setup_env(test_name: &str) -> (Arc<DurableLeaseManager>, Arc<RoutingLedger>, String) {
    let dir = format!("/tmp/fleetd_test_{}_{}", test_name, std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos());
    let _ = fs::create_dir_all(&dir);
    let lease_file = format!("{}/active_leases.json", dir);
    let ledger_file = format!("{}/routing_ledger.jsonl", dir);

    let lease_mgr = Arc::new(DurableLeaseManager::new(lease_file));
    let ledger = Arc::new(RoutingLedger::new(ledger_file));
    (lease_mgr, ledger, dir)
}

#[test]
fn test_rust_acceptance_1_task_without_project() {
    let (lease_mgr, ledger, _dir) = setup_env("acc1");
    let router = SmartRouter::new(0, lease_mgr, ledger.clone(), false);

    let task = TaskCandidate {
        id: "TASK-NOPROJ".to_string(),
        title: "Task with no project".to_string(),
        description: "Builder: agy\nTier: ВП\nWork".to_string(),
        project: None,
        tier: Some("ВП".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 0,
    };

    let decision = router.route_and_claim_task(&task, 3600, false, None);
    assert!(!decision.success);
    assert_eq!(decision.reason, "MISSING_PROJECT");
    assert!(decision.rework_comment.unwrap().contains("@PM REWORK"));

    let records = ledger.read_records().unwrap();
    assert_eq!(records.len(), 1);
    assert_eq!(records[0]["event"], "CANONICAL_GATE_REJECTED");
}

#[test]
fn test_rust_acceptance_2_task_without_tier() {
    let (lease_mgr, ledger, _dir) = setup_env("acc2");
    let router = SmartRouter::new(0, lease_mgr, ledger, false);

    let task = TaskCandidate {
        id: "TASK-NOTIER".to_string(),
        title: "Task with no tier".to_string(),
        description: "Builder: agy\nDoing some work".to_string(),
        project: Some("Core".to_string()),
        tier: None,
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 0,
    };

    let decision = router.route_and_claim_task(&task, 3600, false, None);
    assert!(!decision.success);
    assert_eq!(decision.reason, "MISSING_TIER");
}

#[test]
fn test_rust_acceptance_3_submitted_frozen_and_status() {
    let (lease_mgr, ledger, _dir) = setup_env("acc3");
    let router = SmartRouter::new(0, lease_mgr, ledger, false);

    // 3a: Submitted MP frozen
    let task_mp = TaskCandidate {
        id: "TASK-MP".to_string(),
        title: "Job application".to_string(),
        description: "Builder: agy\nTier: МП\nApplication submitted awaiting-tap".to_string(),
        project: Some("Jobs".to_string()),
        tier: Some("МП".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 0,
    };
    let dec_mp = router.route_and_claim_task(&task_mp, 3600, false, None);
    assert!(!dec_mp.success);
    assert_eq!(dec_mp.reason, "SUBMITTED_FROZEN");

    // 3b: Status Done rejected
    let task_done = TaskCandidate {
        id: "TASK-DONE".to_string(),
        title: "Already done".to_string(),
        description: "Builder: agy\nTier: ВП\nDone".to_string(),
        project: Some("Core".to_string()),
        tier: Some("ВП".to_string()),
        status: Some("Done".to_string()),
        assignee: None,
        wait_time_secs: 10000,
    };
    let dec_done = router.route_and_claim_task(&task_done, 3600, false, None);
    assert!(!dec_done.success);
    assert_eq!(dec_done.reason, "ALREADY_DONE");

    // 3c: Tier mismatch
    let task_mismatch = TaskCandidate {
        id: "TASK-MISMATCH".to_string(),
        title: "Mismatch tier".to_string(),
        description: "Builder: agy\nTier: ВС\nDescription".to_string(),
        project: Some("Core".to_string()),
        tier: Some("МП".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 0,
    };
    let dec_mis = router.route_and_claim_task(&task_mismatch, 3600, false, None);
    assert!(!dec_mis.success);
    assert_eq!(dec_mis.reason, "TIER_MISMATCH");
}

#[test]
fn test_rust_acceptance_4_fallback_triggers() {
    let (lease_mgr, ledger, _dir) = setup_env("acc4");
    // Fallback is permitted when pilot_passed is true
    let router = SmartRouter::new(300, lease_mgr.clone(), ledger, true);

    // 4a: Wait time > 7200s in Todo triggers codex-local
    let task_time = TaskCandidate {
        id: "TASK-TIME".to_string(),
        title: "Wait 2.5h".to_string(),
        description: "Builder: agy\nTier: ВП\nLong wait".to_string(),
        project: Some("MathLab".to_string()),
        tier: Some("ВП".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 9000,
    };
    let dec_time = router.route_and_claim_task(&task_time, 3600, false, None);
    assert!(dec_time.success);
    assert_eq!(dec_time.slot.as_deref(), Some("codex-local"));

    // 4b: Backlog does NOT trigger time fallback
    let _ = lease_mgr.release_lease("TASK-TIME");
    let task_backlog = TaskCandidate {
        id: "TASK-BACKLOG".to_string(),
        title: "Backlog wait".to_string(),
        description: "Builder: agy\nTier: ВП\nIn backlog".to_string(),
        project: Some("MathLab".to_string()),
        tier: Some("ВП".to_string()),
        status: Some("Backlog".to_string()),
        assignee: None,
        wait_time_secs: 9000,
    };
    let dec_backlog = router.route_and_claim_task(&task_backlog, 3600, false, None);
    assert!(dec_backlog.success);
    assert_eq!(dec_backlog.slot.as_deref(), Some("agy")); // Backlog stays with agy!
    let _ = lease_mgr.release_lease("TASK-BACKLOG");

    // 4c: Agy quota exhausted triggers codex-local fallback
    router.set_slot_quota("agy", "QUOTA_EXHAUSTED_429");
    let task_quota = TaskCandidate {
        id: "TASK-QUOTA".to_string(),
        title: "Quota fallback".to_string(),
        description: "Builder: agy\nTier: ВС\nUrgent".to_string(),
        project: Some("MathLab".to_string()),
        tier: Some("ВС".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 100,
    };
    let dec_quota = router.route_and_claim_task(&task_quota, 3600, false, None);
    assert!(dec_quota.success);
    assert_eq!(dec_quota.slot.as_deref(), Some("codex-local"));
}

#[test]
fn test_rust_acceptance_5_double_claim_and_durable_lease() {
    let (lease_mgr1, ledger, dir) = setup_env("acc5");
    let router1 = SmartRouter::new(0, lease_mgr1, ledger.clone(), false);

    let task = TaskCandidate {
        id: "TASK-DOUBLE".to_string(),
        title: "Double claim".to_string(),
        description: "Builder: agy\nTier: ВС\nWork".to_string(),
        project: Some("Core".to_string()),
        tier: Some("ВС".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 0,
    };

    let dec1 = router1.route_and_claim_task(&task, 3600, false, Some("sha-1234567890123456789012345678901234567890"));
    assert!(dec1.success);

    // Second router instance reading from the same lease file
    let lease_file = format!("{}/active_leases.json", dir);
    let lease_mgr2 = Arc::new(DurableLeaseManager::new(lease_file));
    let router2 = SmartRouter::new(0, lease_mgr2, ledger, false);

    let dec2 = router2.route_and_claim_task(&task, 3600, false, None);
    assert!(!dec2.success);
    assert_eq!(dec2.reason, "DOUBLE_CLAIM_REJECTED");
}

#[test]
fn test_rust_acceptance_6_quota_exhaustion_and_pilot_fail_closed() {
    let (lease_mgr, ledger, _dir) = setup_env("acc6");
    let router = SmartRouter::new(0, lease_mgr, ledger, false); // Default pilot_passed = false!

    // 6a: Non-fallback MC task fails closed when agy quota exhausted
    router.set_slot_quota("agy", "QUOTA_EXHAUSTED_429");
    let task_mc = TaskCandidate {
        id: "TASK-MC".to_string(),
        title: "Low priority".to_string(),
        description: "Builder: agy\nTier: МС\nDocs".to_string(),
        project: Some("Core".to_string()),
        tier: Some("МС".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 100,
    };
    let dec_mc = router.route_and_claim_task(&task_mc, 3600, false, None);
    assert!(!dec_mc.success);
    assert_eq!(dec_mc.reason, "SLOT_QUOTA_EXHAUSTED_AGY");

    // 6b: Fallback candidate task with unverified pilot fails closed
    let task_vp = TaskCandidate {
        id: "TASK-VP".to_string(),
        title: "VP task".to_string(),
        description: "Builder: agy\nTier: ВП\nUrgent".to_string(),
        project: Some("Core".to_string()),
        tier: Some("ВП".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 8000,
    };
    let dec_vp = router.route_and_claim_task(&task_vp, 3600, false, None);
    assert!(!dec_vp.success);
    assert_eq!(dec_vp.reason, "PILOT_GATE_BLOCKED_FALLBACK_DISABLED");
}

#[test]
fn test_rust_acceptance_7_model_decision_and_deterministic_fallback() {
    let (lease_mgr, ledger, _dir) = setup_env("acc7");
    let router = SmartRouter::new(0, lease_mgr, ledger, true);

    let task = TaskCandidate {
        id: "TASK-MODEL".to_string(),
        title: "Urgent math".to_string(),
        description: "Builder: agy\nTier: ВС\nUrgent work".to_string(),
        project: Some("MathLab".to_string()),
        tier: Some("ВС".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 8000,
    };

    // 7a: Active model suggestion
    let suggested = router.simulate_local_model_decision(&task, false);
    assert_eq!(suggested.as_deref(), Some("codex-local"));

    // 7b: Timeout triggers deterministic fallback
    let dec_timeout = router.route_and_claim_task(&task, 3600, true, None);
    assert!(dec_timeout.success);
    assert_eq!(dec_timeout.reason, "deterministic_fallback");
}

#[test]
fn test_rust_acceptance_8_historical_cases() {
    // MIN-124: Codex cloud lane with @Codex mention must not be claimed locally
    let v_min124 = validate_canonical_rules(
        Some("Research"),
        "InsightCodex: Riemann null",
        "Tier: ВС\nInsight investigation. @Codex handle this.",
        Some("Todo"),
        Some("ВС"),
        Some("Codex"),
    );
    assert!(!v_min124.passed);
    assert_eq!(v_min124.reason, "CODEX_CLOUD_LANE");

    // MIN-173: Ambiguous builder marker
    let v_min173 = validate_canonical_rules(
        Some("Infrastructure"),
        "Исследование dev-программ",
        "Builder: codex-custom-unknown\nTier: ВП\nTask details.",
        Some("Todo"),
        Some("ВП"),
        None,
    );
    assert!(!v_min173.passed);
    assert_eq!(v_min173.reason, "AMBIGUOUS_BUILDER_MARKER");

    // MIN-213: Submitted application frozen
    let v_min213 = validate_canonical_rules(
        Some("Поиск работы"),
        "Cartesia Software Engineer",
        "Builder: agy\nTier: МП\nStatus: submitted cv-ready awaiting-tap.",
        Some("Todo"),
        Some("МП"),
        Some("МП"),
    );
    assert!(!v_min213.passed);
    assert_eq!(v_min213.reason, "SUBMITTED_FROZEN");
}

#[test]
fn test_rust_acceptance_9_zero_trust_lease_and_gate_properties() {
    let (lease_mgr, _ledger, _dir) = setup_env("acc9");
    // Verify commit SHA tracking in lease
    let ok = lease_mgr.acquire_lease("TASK-COMMIT", "agy", 3600, Some("deadbeef12345678901234567890123456789012")).unwrap();
    assert!(ok);
    let lease = lease_mgr.get_lease("TASK-COMMIT").unwrap().unwrap();
    assert_eq!(lease.commit_sha.as_deref(), Some("deadbeef12345678901234567890123456789012"));
}
