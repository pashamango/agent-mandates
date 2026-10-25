use std::sync::Arc;
use std::fs;

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

    // 7a: Active model offline inference
    let model_dec = router.infer_local_model_decision(&task, false);
    assert!(model_dec.is_some());
    let dec = model_dec.unwrap();
    assert_eq!(dec.recommended_slot, "codex-local");
    assert_eq!(dec.model_id, "mindrock-router-qwen2.5-0.5b-q4_k_m");
    assert!(dec.confidence > 0.5);

    // 7b: Timeout triggers deterministic fallback
    let dec_timeout = router.route_and_claim_task(&task, 3600, true, None);
    assert!(dec_timeout.success);
    assert_eq!(dec_timeout.reason, "deterministic_fallback");
}

#[test]
fn test_rust_acceptance_8_historical_cases() {
    let (lease_mgr, ledger, _dir) = setup_env("acc8");
    let router = SmartRouter::new(0, lease_mgr, ledger, true);

    // 8a: Verify all 5 historical cases via evaluate_policy_metrics (§5.3, §8.8)
    let evaluated_baseline = fleetd::router::evaluate_policy_metrics(&router, None, None);
    assert_eq!(evaluated_baseline.double_claims, 0);
    assert_eq!(evaluated_baseline.uncanonical_dispatched, 0);
    assert_eq!(evaluated_baseline.missed_triggers, 0);
    assert!(evaluated_baseline.on_time_ratio > 0.0);

    // 8b: Test automated rollback on degrading candidate policy (§5.3)
    let buggy_model = |_task: &TaskCandidate| -> Option<String> {
        Some("agy".to_string())
    };

    // Strict baseline requires on_time_ratio = 1.0 (matching Python reference test)
    let strict_baseline = fleetd::router::PolicyMetrics {
        double_claims: 0,
        missed_triggers: 0,
        uncanonical_dispatched: 0,
        on_time_ratio: 1.0,
    };

    router.set_slot_quota("agy", "QUOTA_EXHAUSTED_429");
    let rollback_result = fleetd::router::evaluate_and_apply_policy_update(
        &router,
        &buggy_model,
        Some(strict_baseline.clone()),
        None,
    );

    assert_eq!(rollback_result.status, "ROLLED_BACK");
    assert!(!rollback_result.policy_accepted);
    assert!(rollback_result.candidate_metrics.on_time_ratio < strict_baseline.on_time_ratio);
}

#[test]
fn test_rust_acceptance_9_zero_trust_lease_and_gate_properties() {
    let (lease_mgr, ledger, dir) = setup_env("acc9");
    let router = SmartRouter::new(0, lease_mgr.clone(), ledger, false);

    // 9a: Fail-closed health check of codex runner (binary missing)
    router.refresh_slot_health("/nonexistent/bin/codex");
    assert_eq!(
        router.slot_health_status.lock().unwrap().get("codex-local").unwrap(),
        "HEALTH_FAILED"
    );

    // 9a-2: Active probe verification with mock codex compliant with §6.1
    let probe_script = format!("{}/mock_codex.sh", dir);
    fs::write(
        &probe_script,
        r#"#!/bin/bash
if [ "$1" = "--version" ]; then
    echo "codex-local 0.153.4"
    exit 0
fi
if [ "$1" = "--health-probe" ]; then
    TARGET_DIR="$2"
    cd "$TARGET_DIR" || exit 1
    mkdir -p evidence
    echo '{"probe": true}' > task.json
    git -c user.name=Codex -c user.email=codex@mindrock.capital add task.json
    git -c user.name=Codex -c user.email=codex@mindrock.capital commit -m "probe commit" >/dev/null 2>&1
    SHA=$(git rev-parse HEAD)
    echo "{\"repo_url\": \"file://$TARGET_DIR\", \"branch\": \"probe\", \"commit_sha\": \"$SHA\"}" > evidence/HANDOFF.json
    exit 0
fi
exit 1
"#,
    ).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&probe_script, fs::Permissions::from_mode(0o755));
    }
    router.refresh_slot_health(&probe_script);
    assert_eq!(
        router.slot_health_status.lock().unwrap().get("codex-local").unwrap(),
        "HEALTH_OK"
    );

    // 9b: Pilot receipt verification from real file (§6.3)
    let receipt_path = if std::path::Path::new("fleetd-evolution/evidence/PILOT_MIN_221_RECEIPT.json").exists() {
        "fleetd-evolution/evidence/PILOT_MIN_221_RECEIPT.json"
    } else if std::path::Path::new("../../evidence/PILOT_MIN_221_RECEIPT.json").exists() {
        "../../evidence/PILOT_MIN_221_RECEIPT.json"
    } else {
        "../../fleetd-evolution/evidence/PILOT_MIN_221_RECEIPT.json"
    };
    let (passed, reason) = fleetd::router::verify_pilot_receipt(receipt_path);
    // Real MIN-221 receipt contains FAIL -> must fail closed
    assert!(!passed);
    assert!(reason.contains("FAIL"));

    // 9b-1: Unmanifested receipt fails closed (Defect 3)
    let unmanifested_receipt = format!("{}/unmanifested_pass_receipt.json", dir);
    fs::write(
        &unmanifested_receipt,
        serde_json::json!({
            "pilot_task_id": "MIN-221",
            "tier": "ВП",
            "git_coordinates": {
                "repo_url": "https://github.com/pashamango/math.git",
                "branch": "collatz-v0.1.0-m3",
                "commit_sha": "16590a7574b188225896cdf99268bc476cbe18a3"
            },
            "independent_qa_audit": {
                "reviewer": "Codex (Independent QA)",
                "reviewer_id": "3bc09df8-b112-4d8d-8d0c-519043c77030",
                "verdict": "PASS"
            }
        }).to_string(),
    ).unwrap();
    let (unman_ok, unman_reason) = fleetd::router::verify_pilot_receipt(&unmanifested_receipt);
    assert!(!unman_ok);
    assert!(unman_reason.contains("RECEIPT_NOT_IN_MANIFEST"));

    // 9b-2: Modifying PILOT_MIN_221_RECEIPT.json fails checksum against manifest
    let tampered_receipt = format!("{}/PILOT_MIN_221_RECEIPT.json", dir);
    fs::write(
        &tampered_receipt,
        serde_json::json!({
            "pilot_task_id": "MIN-221",
            "tier": "ВП",
            "git_coordinates": {
                "repo_url": "https://github.com/pashamango/math.git",
                "branch": "collatz-v0.1.0-m3",
                "commit_sha": "16590a7574b188225896cdf99268bc476cbe18a3"
            },
            "independent_qa_audit": {
                "reviewer": "Codex (Independent QA)",
                "reviewer_id": "3bc09df8-b112-4d8d-8d0c-519043c77030",
                "verdict": "PASS"
            }
        }).to_string(),
    ).unwrap();
    let (tampered_ok, tampered_reason) = fleetd::router::verify_pilot_receipt(&tampered_receipt);
    assert!(!tampered_ok);
    assert!(tampered_reason.contains("RECEIPT_CHECKSUM_MISMATCH"));

    // 9b-3: Manifest root authentication failure fails closed (Defect 4)
    std::env::set_var("FLEETD_TRUSTED_MANIFEST_SHA256", "0000000000000000000000000000000000000000000000000000000000000000");
    let (root_ok, root_reason) = fleetd::router::verify_pilot_receipt(receipt_path);
    assert!(!root_ok);
    assert!(root_reason.contains("MANIFEST_ROOT_UNAUTHENTICATED"));
    std::env::remove_var("FLEETD_TRUSTED_MANIFEST_SHA256");

    // 9c: Commit SHA tracking in lease
    let ok = lease_mgr.acquire_lease("TASK-COMMIT", "agy", 3600, Some("deadbeef12345678901234567890123456789012")).unwrap();
    assert!(ok);
    let lease = lease_mgr.get_lease("TASK-COMMIT").unwrap().unwrap();
    assert_eq!(lease.commit_sha.as_deref(), Some("deadbeef12345678901234567890123456789012"));

    // 9d: Manifest SHA256 integrity verification in Rust
    let manifest_path = if std::path::Path::new("fleetd-evolution/evidence/MANIFEST.sha256").exists() {
        "fleetd-evolution/evidence/MANIFEST.sha256"
    } else if std::path::Path::new("../../evidence/MANIFEST.sha256").exists() {
        "../../evidence/MANIFEST.sha256"
    } else {
        "../../fleetd-evolution/evidence/MANIFEST.sha256"
    };
    if std::path::Path::new(manifest_path).exists() {
        let content = fs::read_to_string(manifest_path).unwrap();
        use sha2::{Digest, Sha256};
        for line in content.lines() {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() == 2 {
                let expected_sha = parts[0];
                let path = parts[1];
                let resolved_file = if std::path::Path::new(path).exists() {
                    path.to_string()
                } else {
                    format!("../../{}", path)
                };
                if std::path::Path::new(&resolved_file).exists() {
                    let bytes = fs::read(&resolved_file).unwrap();
                    let mut hasher = Sha256::new();
                    hasher.update(&bytes);
                    let actual_sha = hex::encode(hasher.finalize());
                    assert_eq!(actual_sha, expected_sha, "Manifest mismatch for {}", resolved_file);
                }
            }
        }
    }
}

#[tokio::test]
async fn test_rust_acceptance_10_daemon_integration_webhook_and_vacuum() {
    use axum::http::{HeaderMap, StatusCode};

    let (lease_mgr, ledger, dir) = setup_env("acc10");
    let router = Arc::new(SmartRouter::new(0, lease_mgr, ledger.clone(), true));
    let db_path = format!("{}/test.db", dir);
    let db = fleetd::db::Database::new(std::path::Path::new(&db_path)).unwrap();
    let mut config = fleetd::config::Config::load();
    config.linear_webhook_secret = None;
    config.allowed_teams = vec!["MIN".to_string(), "PRD".to_string()];

    let app_state = fleetd::server::AppState {
        config: config.clone(),
        db: db.clone(),
        router: router.clone(),
        linear: None,
    };

    // 10a: Webhook path with uncanonical issue (missing project) -> REJECTED, not enqueued!
    let uncanonical_payload = serde_json::json!({
        "type": "Issue",
        "action": "create",
        "data": {
            "id": "ISSUE-NOPROJ",
            "identifier": "MIN-991",
            "title": "Uncanonical task",
            "description": "Builder: agy\nTier: ВП\nMissing project link",
            "team": { "key": "MIN" },
            "state": { "name": "Todo" },
            "labels": [{ "name": "Builder: agy" }]
        }
    });

    let mut headers1 = HeaderMap::new();
    headers1.insert("Linear-Delivery", "deliv-1".parse().unwrap());
    let (status, body) = fleetd::server::handle_webhook(
        app_state.clone(),
        headers1,
        axum::body::Bytes::from(uncanonical_payload.to_string()),
    ).await;

    assert_eq!(status, StatusCode::OK);
    assert_eq!(body["status"], "rejected");
    assert_eq!(body["reason"], "MISSING_PROJECT");
    // Verify NOT enqueued in SQLite
    assert!(!db.is_issue_already_enqueued("ISSUE-NOPROJ"));

    // 10b: Valid webhook -> CLAIMED with handoff commit, enqueued!
    let valid_payload = serde_json::json!({
        "type": "Issue",
        "action": "create",
        "data": {
            "id": "ISSUE-VALID",
            "identifier": "MIN-992",
            "title": "Valid task",
            "description": "Builder: agy\nTier: ВП\nProject: fleetd self-evolution\ncommit_sha: 16590a7574b188225896cdf99268bc476cbe18a3",
            "projectName": "fleetd self-evolution",
            "team": { "key": "MIN" },
            "state": { "name": "Todo" },
            "labels": [{ "name": "Builder: agy" }]
        }
    });

    let mut headers2 = HeaderMap::new();
    headers2.insert("Linear-Delivery", "deliv-2".parse().unwrap());
    let (status_valid, body_valid) = fleetd::server::handle_webhook(
        app_state.clone(),
        headers2,
        axum::body::Bytes::from(valid_payload.to_string()),
    ).await;

    assert_eq!(status_valid, StatusCode::OK);
    assert_eq!(body_valid["status"], "queued");
    assert!(db.is_issue_already_enqueued("ISSUE-VALID"));

    // 10c: Second webhook for same issue -> duplicate rejected!
    let mut headers3 = HeaderMap::new();
    headers3.insert("Linear-Delivery", "deliv-3".parse().unwrap());
    let (status_dup, body_dup) = fleetd::server::handle_webhook(
        app_state.clone(),
        headers3,
        axum::body::Bytes::from(valid_payload.to_string()),
    ).await;

    assert_eq!(status_dup, StatusCode::OK);
    assert_eq!(body_dup["status"], "already_queued");

    // 10d: Vacuum candidate conversion & canonical rejection test
    let cand_no_proj = fleetd::linear::LinearIssueCandidate {
        id: "VAC-NOPROJ".to_string(),
        identifier: "MIN-993".to_string(),
        title: "No project vacuum candidate".to_string(),
        description: "Builder: agy\nTier: ВС\nNo project anywhere".to_string(),
        labels: vec![],
        project_name: None,
        state_name: Some("Todo".to_string()),
        state_type: Some("unstarted".to_string()),
        assignee_name: None,
        created_at: None,
        updated_at: None,
    };
    let verdict = fleetd::canonical::validate_canonical_rules(
        cand_no_proj.project_name.as_deref(),
        &cand_no_proj.title,
        &cand_no_proj.description,
        cand_no_proj.state_name.as_deref(),
        None,
        cand_no_proj.assignee_name.as_deref(),
    );
    assert!(!verdict.passed);
    assert_eq!(verdict.reason, "MISSING_PROJECT");

    // 10e: Webhook path with missing status -> REJECTED with MISSING_STATUS
    let missing_status_payload = serde_json::json!({
        "type": "Issue",
        "action": "create",
        "data": {
            "id": "ISSUE-NOSTATUS",
            "identifier": "MIN-994",
            "title": "Missing status task",
            "description": "Builder: agy\nTier: ВП\nProject: fleetd self-evolution",
            "projectName": "fleetd self-evolution",
            "team": { "key": "MIN" },
            "labels": [{ "name": "Builder: agy" }]
        }
    });
    let mut headers4 = HeaderMap::new();
    headers4.insert("Linear-Delivery", "deliv-4".parse().unwrap());
    let (status_nostat, body_nostat) = fleetd::server::handle_webhook(
        app_state.clone(),
        headers4,
        axum::body::Bytes::from(missing_status_payload.to_string()),
    ).await;
    assert_eq!(status_nostat, StatusCode::OK);
    assert_eq!(body_nostat["status"], "rejected");
    assert_eq!(body_nostat["reason"], "MISSING_STATUS");

    // 10f: Webhook lease rollback upon enqueue failure (prevent lost tasks)
    let (fail_lease_mgr, fail_ledger, fail_dir) = setup_env("acc10_fail");
    let fail_router = Arc::new(SmartRouter::new(0, fail_lease_mgr.clone(), fail_ledger, true));
    let bad_db_path = format!("{}/bad.db", fail_dir);
    let bad_db = fleetd::db::Database::new(std::path::Path::new(&bad_db_path)).unwrap();
    bad_db.execute_raw("DROP TABLE jobs").unwrap();

    let fail_state = fleetd::server::AppState {
        config: config.clone(),
        db: bad_db,
        router: fail_router.clone(),
        linear: None,
    };
    let enqueue_fail_payload = serde_json::json!({
        "type": "Issue",
        "action": "create",
        "data": {
            "id": "ISSUE-ENQFAIL",
            "identifier": "MIN-995",
            "title": "Enqueue fail task",
            "description": "Builder: agy\nTier: ВП\nProject: fleetd self-evolution\ncommit_sha: 16590a7574b188225896cdf99268bc476cbe18a3",
            "projectName": "fleetd self-evolution",
            "team": { "key": "MIN" },
            "state": { "name": "Todo" },
            "labels": [{ "name": "Builder: agy" }]
        }
    });
    let mut headers5 = HeaderMap::new();
    headers5.insert("Linear-Delivery", "deliv-5".parse().unwrap());
    let (status_enq_fail, _) = fleetd::server::handle_webhook(
        fail_state,
        headers5,
        axum::body::Bytes::from(enqueue_fail_payload.to_string()),
    ).await;
    assert_eq!(status_enq_fail, StatusCode::INTERNAL_SERVER_ERROR);
    // Verify lease was released on failure so task is NOT lost!
    assert!(fail_lease_mgr.get_lease("ISSUE-ENQFAIL").unwrap().is_none());

    // 10g: Preservation of pacing for old tasks (> 7200s wait)
    let (pacing_lease_mgr, pacing_ledger, _) = setup_env("acc10_pacing");
    let pacing_router = SmartRouter::new(300, pacing_lease_mgr, pacing_ledger, false);
    *pacing_router.last_agy_start.lock().unwrap() = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let old_task = TaskCandidate {
        id: "TASK-OLD".to_string(),
        title: "Old task wait 3h".to_string(),
        description: "Builder: agy\nTier: МС".to_string(),
        project: Some("fleetd self-evolution".to_string()),
        tier: Some("МС".to_string()),
        status: Some("Todo".to_string()),
        assignee: None,
        wait_time_secs: 10800,
    };
    let dec_old = pacing_router.route_and_claim_task(&old_task, 3600, false, None);
    assert!(!dec_old.success);
    assert_eq!(dec_old.reason, "PACING_BACKOFF");

    // 10h: Fail-closed quota ingestion and cloud QA pool protection
    let (q_lease_mgr, q_ledger, q_dir) = setup_env("acc10_quota");
    let q_router = SmartRouter::new(0, q_lease_mgr, q_ledger.clone(), false);
    q_router.refresh_quota_signals("/nonexistent/quota.json");
    assert_eq!(q_router.slot_quota_status.lock().unwrap().get("agy").unwrap(), "QUOTA_SOURCE_UNAVAILABLE");
    let q_records = q_ledger.read_records().unwrap();
    assert!(q_records.iter().any(|r| r["event"] == "QUOTA_SIGNALS_FILE_MISSING"));

    let corrupt_q = format!("{}/corrupt.json", q_dir);
    fs::write(&corrupt_q, b"INVALID_JSON{{").unwrap();
    q_router.refresh_quota_signals(&corrupt_q);
    assert_eq!(q_router.slot_quota_status.lock().unwrap().get("agy").unwrap(), "QUOTA_SOURCE_UNAVAILABLE");
    let q_records2 = q_ledger.read_records().unwrap();
    assert!(q_records2.iter().any(|r| r["event"] == "QUOTA_SIGNALS_PARSE_ERROR"));

    let low_pool_q = format!("{}/low_pool.json", q_dir);
    fs::write(&low_pool_q, serde_json::json!({
        "agy": "OK",
        "codex-local": "OK",
        "chatgpt_pool_remaining": 10,
        "cloud_qa_starvation_risk": true
    }).to_string()).unwrap();
    q_router.refresh_quota_signals(&low_pool_q);
    assert_eq!(q_router.slot_quota_status.lock().unwrap().get("codex-local").unwrap(), "EXHAUSTED_TO_PROTECT_QA");

    // 10h-2: Incomplete schema (empty {} or missing required fields) fails closed
    let empty_q = format!("{}/empty.json", q_dir);
    fs::write(&empty_q, b"{}").unwrap();
    q_router.refresh_quota_signals(&empty_q);
    assert_eq!(q_router.slot_quota_status.lock().unwrap().get("agy").unwrap(), "QUOTA_SOURCE_UNAVAILABLE");
    assert_eq!(q_router.slot_quota_status.lock().unwrap().get("codex-local").unwrap(), "QUOTA_SOURCE_UNAVAILABLE");
    let q_records3 = q_ledger.read_records().unwrap();
    assert!(q_records3.iter().any(|r| r["event"] == "QUOTA_SIGNALS_SCHEMA_INCOMPLETE"));

    let partial_q = format!("{}/partial.json", q_dir);
    fs::write(&partial_q, serde_json::json!({ "agy": "OK" }).to_string()).unwrap();
    q_router.refresh_quota_signals(&partial_q);
    assert_eq!(q_router.slot_quota_status.lock().unwrap().get("agy").unwrap(), "QUOTA_SOURCE_UNAVAILABLE");
    assert_eq!(q_router.slot_quota_status.lock().unwrap().get("codex-local").unwrap(), "QUOTA_SOURCE_UNAVAILABLE");
}
