use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::sync::Mutex;
use fleetd::db::{Database, OutboxItem};
use fleetd::linear::LinearClient;
use fleetd::worker::process_outbox_item;

#[derive(Default, Clone)]
struct MockLinearCalls {
    pub move_state_calls: Arc<AtomicUsize>,
    pub add_label_calls: Arc<AtomicUsize>,
    pub posted_comments: Arc<Mutex<Vec<String>>>,
}

fn create_mock_linear(calls: MockLinearCalls) -> LinearClient {
    LinearClient::new_mock(move |query, variables| {
        if query.contains("CheckComment") {
            return Ok(serde_json::json!({
                "issue": {
                    "comments": { "nodes": [] }
                }
            }));
        }

        if query.contains("AddComment") || query.contains("commentCreate") {
            if let Some(body) = variables.pointer("/input/body").and_then(|b| b.as_str()) {
                calls.posted_comments.lock().unwrap().push(body.to_string());
            }
            return Ok(serde_json::json!({
                "commentCreate": {
                    "success": true,
                    "comment": { "id": "comment_123" }
                }
            }));
        }

        if query.contains("GetStates") {
            return Ok(serde_json::json!({
                "issue": {
                    "team": {
                        "states": {
                            "nodes": [
                                { "id": "state_in_review", "name": "In Review" }
                            ]
                        }
                    }
                }
            }));
        }

        if query.contains("MoveState") || query.contains("issueUpdate") {
            calls.move_state_calls.fetch_add(1, Ordering::SeqCst);
            return Ok(serde_json::json!({
                "issueUpdate": { "success": true }
            }));
        }

        if query.contains("GetLabel") || query.contains("issueLabels") {
            return Ok(serde_json::json!({
                "issueLabels": {
                    "nodes": [
                        { "id": "label_qa_needed", "name": "Role: QA-Needed" }
                    ]
                }
            }));
        }

        if query.contains("AddLabel") || query.contains("issueAddLabel") {
            calls.add_label_calls.fetch_add(1, Ordering::SeqCst);
            return Ok(serde_json::json!({
                "issueAddLabel": { "success": true }
            }));
        }

        if query.contains("GetIssueBranch") {
            let issue_id = variables.pointer("/issueId").and_then(|i| i.as_str()).unwrap_or("");
            let branch = if issue_id == "issue-fake-remote" {
                "does-not-exist"
            } else {
                "minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno"
            };
            return Ok(serde_json::json!({
                "issue": {
                    "branchName": branch
                }
            }));
        }

        Ok(serde_json::Value::Null)
    })
}

fn setup_test_db(name: &str) -> (Database, String) {
    let dir = format!(
        "/tmp/fleetd_test_db_{}_{}",
        name,
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let _ = std::fs::create_dir_all(&dir);
    let db_path = format!("{}/test.db", dir);
    let db = Database::new(std::path::Path::new(&db_path)).unwrap();
    (db, dir)
}

#[tokio::test]
async fn test_gate_blocks_prd35_trace() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("prd35");

    let prd_35_report = r#"
### agy build report: `PRD-35`
<!-- agy-run:run-d40407bfd3e6 -->
**Run ID:** `run-d40407bfd3e6` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** PRD-35: Idea / PRD: Symbiotic Hiring Protocol (automated proof of JD fit)

#### Summary
Спецификация Symbiotic Hiring Protocol подготовлена. Файл сохранен в корне workspace-репозитория как spec-symbiotic-hiring-protocol-v0.1.md.

---
@Codex — проведи независимый QA этой задачи по критериям приёмки.
"#;

    let item = OutboxItem {
        id: 1,
        issue_id: "issue-prd-35".to_string(),
        identifier: "PRD-35".to_string(),
        comment_body: prd_35_report.to_string(),
        marker: "<!-- agy-run:run-d40407bfd3e6 -->".to_string(),
        builder: "agy".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, true).await.unwrap();
    assert!(!transitioned, "PRD-35 must NOT transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 0, "MoveState must not be called");
    assert_eq!(calls.add_label_calls.load(Ordering::SeqCst), 0, "AddLabel must not be called");

    let comments = calls.posted_comments.lock().unwrap();
    assert!(comments.iter().any(|c| c.contains("<!-- handoff-gate:auto-rework -->")));
    assert!(comments.iter().any(|c| c.contains("Локально — значит BLOCKER") || c.contains("evidence unavailable")));
}

#[tokio::test]
async fn test_gate_blocks_min194_trace_with_blocker_semantics() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("min194");

    let min_194_report = r#"
### codex build report: `MIN-194`
<!-- codex-run:run-min194-bad -->
**Run ID:** `run-min194-bad` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-194: spec fleetd self-evolution

#### Summary
Изменения внесены только локально.

#### Evidence / Artifacts
- `fleetd-self-evolution.md`

---
@agy — проведи независимый QA.
"#;

    let item = OutboxItem {
        id: 2,
        issue_id: "issue-min-194".to_string(),
        identifier: "MIN-194".to_string(),
        comment_body: min_194_report.to_string(),
        marker: "<!-- codex-run:run-min194-bad -->".to_string(),
        builder: "codex".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, true).await.unwrap();
    assert!(!transitioned, "MIN-194 must NOT transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 0, "MoveState must not be called");
    assert_eq!(calls.add_label_calls.load(Ordering::SeqCst), 0, "AddLabel must not be called");

    let comments = calls.posted_comments.lock().unwrap();
    let rework_comment = comments.iter().find(|c| c.contains("<!-- handoff-gate:blocker -->")).expect("Must contain blocker marker");
    assert!(rework_comment.contains("Локально — значит BLOCKER"));
    assert!(rework_comment.contains("нет пуша — нет сдачи"));
    assert!(rework_comment.contains("@codex REWORK:"), "Codex report must address @codex, got: {}", rework_comment);
    assert!(!rework_comment.contains("@agy REWORK:"), "Must not hardcode @agy for Codex delivery");
}

#[tokio::test]
async fn test_gate_blocks_fake_remote_trace() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("fake_remote");

    let fake_remote_report = r#"
### agy build report: `MIN-991`
<!-- agy-run:run-fake-remote -->
**Run ID:** `run-fake-remote` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-991: Test fake remote

#### Summary
Work done.

#### Evidence / Coordinates
- **Repository:** `https://example.invalid/nonexistent.git`
- **Branch:** `does-not-exist`
- **Commit:** `deadbee`
- **Artifact Paths:**
  - `nonexistent.md`
- **Independent Verification Command:**
  ```bash
  git clone https://example.invalid/nonexistent.git && cd nonexistent && git checkout deadbee
  ```
"#;

    let item = OutboxItem {
        id: 3,
        issue_id: "issue-fake-remote".to_string(),
        identifier: "MIN-991".to_string(),
        comment_body: fake_remote_report.to_string(),
        marker: "<!-- agy-run:run-fake-remote -->".to_string(),
        builder: "agy".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, true).await.unwrap();
    assert!(!transitioned, "Fake remote must NOT transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 0, "MoveState must not be called");
    assert_eq!(calls.add_label_calls.load(Ordering::SeqCst), 0, "AddLabel must not be called");

    let comments = calls.posted_comments.lock().unwrap();
    let rework = comments.iter().find(|c| c.contains("<!-- handoff-gate:auto-rework -->")).expect("Must post auto-rework");
    assert!(rework.contains("Удалённый репозиторий недоступен или не существует"));
}

#[tokio::test]
async fn test_gate_blocks_missing_commit_trace() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("fake_commit");

    let fake_commit_report = r#"
### agy build report: `MIN-992`
<!-- agy-run:run-fake-commit -->
**Run ID:** `run-fake-commit` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-992: Test missing commit

#### Summary
Work done.

#### Evidence / Coordinates
- **Repository:** `https://github.com/pashamango/agent-mandates.git`
- **Branch:** `minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno`
- **Commit:** `0000000000000000000000000000000000000000`
- **Artifact Paths:**
  - `HANDOFF-GATE-PROTOCOL.md`
- **Independent Verification Command:**
  ```bash
  git clone https://github.com/pashamango/agent-mandates.git && cd agent-mandates && git checkout 0000000000000000000000000000000000000000
  ```
"#;

    let item = OutboxItem {
        id: 4,
        issue_id: "issue-fake-commit".to_string(),
        identifier: "MIN-992".to_string(),
        comment_body: fake_commit_report.to_string(),
        marker: "<!-- agy-run:run-fake-commit -->".to_string(),
        builder: "agy".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, true).await.unwrap();
    assert!(!transitioned, "Missing commit must NOT transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 0, "MoveState must not be called");
    assert_eq!(calls.add_label_calls.load(Ordering::SeqCst), 0, "AddLabel must not be called");

    let comments = calls.posted_comments.lock().unwrap();
    let rework = comments.iter().find(|c| c.contains("<!-- handoff-gate:auto-rework -->")).expect("Must post auto-rework");
    assert!(rework.contains("не найден в ветке") || rework.contains("не принадлежит"));
}

#[tokio::test]
async fn test_gate_blocks_missing_artifact_path_trace() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("fake_path");

    let fake_path_report = r#"
### agy build report: `MIN-993`
<!-- agy-run:run-fake-path -->
**Run ID:** `run-fake-path` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-993: Test missing artifact path

#### Summary
Work done.

#### Evidence / Coordinates
- **Repository:** `https://github.com/pashamango/agent-mandates.git`
- **Branch:** `minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno`
- **Commit:** `5c98f6f59bfe13123e09b3664f3b07ce51445730`
- **Artifact Paths:**
  - `ghost_artifact_path_does_not_exist_xyz.md`
- **Independent Verification Command:**
  ```bash
  git clone https://github.com/pashamango/agent-mandates.git && cd agent-mandates && git checkout 5c98f6f59bfe13123e09b3664f3b07ce51445730
  ```
"#;

    let item = OutboxItem {
        id: 5,
        issue_id: "issue-fake-path".to_string(),
        identifier: "MIN-993".to_string(),
        comment_body: fake_path_report.to_string(),
        marker: "<!-- agy-run:run-fake-path -->".to_string(),
        builder: "agy".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, true).await.unwrap();
    assert!(!transitioned, "Missing artifact path must NOT transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 0, "MoveState must not be called");
    assert_eq!(calls.add_label_calls.load(Ordering::SeqCst), 0, "AddLabel must not be called");

    let comments = calls.posted_comments.lock().unwrap();
    let rework = comments.iter().find(|c| c.contains("<!-- handoff-gate:auto-rework -->")).expect("Must post auto-rework");
    assert!(rework.contains("не найден в коммите"));
}

#[tokio::test]
async fn test_gate_allows_valid_coordinates_trace() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("valid_coords");

    let valid_report = r#"
### agy build report: `MIN-236`
<!-- agy-run:run-valid-trace -->
**Run ID:** `run-valid-trace` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-236: Handoff Gate

#### Summary
Verified complete delivery.

#### Evidence / Coordinates
- **Repository:** `https://github.com/pashamango/agent-mandates.git`
- **Branch:** `minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno`
- **Commit:** `5c98f6f59bfe13123e09b3664f3b07ce51445730`
- **Artifact Paths:**
  - `HANDOFF-GATE-PROTOCOL.md`
- **Independent Verification Command:**
  ```bash
  git clone https://github.com/pashamango/agent-mandates.git && cd agent-mandates && git checkout 5c98f6f59bfe13123e09b3664f3b07ce51445730
  ```
"#;

    let item = OutboxItem {
        id: 6,
        issue_id: "issue-valid-236".to_string(),
        identifier: "MIN-236".to_string(),
        comment_body: valid_report.to_string(),
        marker: "<!-- agy-run:run-valid-trace -->".to_string(),
        builder: "agy".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, true).await.unwrap();
    let comments = calls.posted_comments.lock().unwrap();
    eprintln!("VALID TRACE COMMENTS: {:?}", *comments);
    assert!(transitioned, "Valid delivery MUST pass Handoff Gate and transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 1, "MoveState must be called exactly once");
    assert_eq!(calls.add_label_calls.load(Ordering::SeqCst), 1, "AddLabel must be called exactly once");
}

#[tokio::test]
async fn test_gate_blocks_commit_from_other_branch() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("other_branch_commit");

    // a670b9036dcc8d88cc362cddd22284a4b8d4d6f9 is from minutta/min-146 branch, NOT minutta/min-236
    let other_branch_report = r#"
### agy build report: `MIN-236`
<!-- agy-run:run-other-branch -->
**Run ID:** `run-other-branch` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-236: Test other branch commit

#### Summary
Work done.

#### Evidence / Coordinates
- **Repository:** `https://github.com/pashamango/agent-mandates.git`
- **Branch:** `minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno`
- **Commit:** `a670b9036dcc8d88cc362cddd22284a4b8d4d6f9`
- **Artifact Paths:**
  - `HANDOFF-GATE-PROTOCOL.md`
- **Independent Verification Command:**
  ```bash
  git clone https://github.com/pashamango/agent-mandates.git && cd agent-mandates && git checkout a670b9036dcc8d88cc362cddd22284a4b8d4d6f9
  ```
"#;

    let item = OutboxItem {
        id: 7,
        issue_id: "issue-other-branch".to_string(),
        identifier: "MIN-236".to_string(),
        comment_body: other_branch_report.to_string(),
        marker: "<!-- agy-run:run-other-branch -->".to_string(),
        builder: "agy".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, true).await.unwrap();
    assert!(!transitioned, "Commit from other branch must NOT transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 0, "MoveState must not be called");

    let comments = calls.posted_comments.lock().unwrap();
    let rework = comments.iter().find(|c| c.contains("<!-- handoff-gate:auto-rework -->")).expect("Must post auto-rework");
    assert!(rework.contains("не принадлежит истории ветки"), "Must state commit does not belong to branch: {}", rework);
}

#[tokio::test]
async fn test_gate_blocks_branch_mismatch_in_production_path() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("branch_mismatch");

    // Report claims Branch is minutta/some-unrelated-branch, while Linear issue expects min-236 branch
    let mismatch_report = r#"
### agy build report: `MIN-236`
<!-- agy-run:run-mismatch -->
**Run ID:** `run-mismatch` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-236: Test branch mismatch

#### Summary
Work done.

#### Evidence / Coordinates
- **Repository:** `https://github.com/pashamango/agent-mandates.git`
- **Branch:** `minutta/some-unrelated-branch`
- **Commit:** `5c98f6f59bfe13123e09b3664f3b07ce51445730`
- **Artifact Paths:**
  - `HANDOFF-GATE-PROTOCOL.md`
- **Independent Verification Command:**
  ```bash
  git clone https://github.com/pashamango/agent-mandates.git && cd agent-mandates && git checkout 5c98f6f59bfe13123e09b3664f3b07ce51445730
  ```
"#;

    let item = OutboxItem {
        id: 8,
        issue_id: "issue-mismatch".to_string(),
        identifier: "MIN-236".to_string(),
        comment_body: mismatch_report.to_string(),
        marker: "<!-- agy-run:run-mismatch -->".to_string(),
        builder: "agy".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, false).await.unwrap();
    assert!(!transitioned, "Branch mismatch must NOT transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 0, "MoveState must not be called");

    let comments = calls.posted_comments.lock().unwrap();
    let rework = comments.iter().find(|c| c.contains("<!-- handoff-gate:auto-rework -->")).expect("Must post auto-rework");
    assert!(rework.contains("не совпадает с закрепленной веткой тикета"), "Must contain branch mismatch text: {}", rework);
}

#[tokio::test]
async fn test_verification_command_without_commit_fails_gate() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("no_commit_cmd");

    // Verification command has branch only, no commit SHA
    let no_commit_cmd_report = r#"
### agy build report: `MIN-236`
<!-- agy-run:run-no-commit-cmd -->
**Run ID:** `run-no-commit-cmd` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-236: Test verify cmd without commit

#### Summary
Work done.

#### Evidence / Coordinates
- **Repository:** `https://github.com/pashamango/agent-mandates.git`
- **Branch:** `minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno`
- **Commit:** `5c98f6f59bfe13123e09b3664f3b07ce51445730`
- **Artifact Paths:**
  - `HANDOFF-GATE-PROTOCOL.md`
- **Independent Verification Command:**
  ```bash
  git clone --branch minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno https://github.com/pashamango/agent-mandates.git
  ```
"#;

    let item = OutboxItem {
        id: 9,
        issue_id: "issue-no-commit-cmd".to_string(),
        identifier: "MIN-236".to_string(),
        comment_body: no_commit_cmd_report.to_string(),
        marker: "<!-- agy-run:run-no-commit-cmd -->".to_string(),
        builder: "agy".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, true).await.unwrap();
    assert!(!transitioned, "Verification command without commit must NOT transition to In Review");
    assert_eq!(calls.move_state_calls.load(Ordering::SeqCst), 0, "MoveState must not be called");

    let comments = calls.posted_comments.lock().unwrap();
    let rework = comments.iter().find(|c| c.contains("<!-- handoff-gate:auto-rework -->")).expect("Must post auto-rework");
    assert!(rework.contains("Команда проверки не фиксирует точный заявленный commit"), "Must reject cmd: {}", rework);
}

#[tokio::test]
async fn test_codex_builder_addressed_in_auto_rework() {
    let calls = MockLinearCalls::default();
    let linear = create_mock_linear(calls.clone());
    let (db, _dir) = setup_test_db("codex_rework");

    // Incomplete Codex report (missing commit)
    let codex_report = r#"
### codex build report: `MIN-236`
<!-- codex-run:run-codex-123 -->
**Run ID:** `run-codex-123` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-236: Incomplete Codex report

#### Summary
Work done by codex.

#### Evidence / Coordinates
- **Repository:** `https://github.com/pashamango/agent-mandates.git`
- **Branch:** `minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno`
- **Artifact Paths:**
  - `HANDOFF-GATE-PROTOCOL.md`
"#;

    let item = OutboxItem {
        id: 10,
        issue_id: "issue-codex-123".to_string(),
        identifier: "MIN-236".to_string(),
        comment_body: codex_report.to_string(),
        marker: "<!-- codex-run:run-codex-123 -->".to_string(),
        builder: "codex".to_string(),
    };

    let transitioned = process_outbox_item(&db, &linear, &item, false).await.unwrap();
    assert!(!transitioned, "Incomplete report must NOT transition to In Review");

    let comments = calls.posted_comments.lock().unwrap();
    let rework = comments.iter().find(|c| c.contains("<!-- handoff-gate:auto-rework -->")).expect("Must post auto-rework");
    assert!(rework.contains("@codex REWORK:"), "Rework MUST be addressed to @codex, got: {}", rework);
    assert!(!rework.contains("@agy REWORK:"), "Must NOT address @agy for Codex job");
}
