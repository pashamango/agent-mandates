# Fleetd Handoff Gate Daemon Enforcement Architecture (Part B)
**Reference:** MIN-236  
**Status:** Design & Reference Implementation

## 1. Overview
In the original `fleetd` Rust worker (`src/worker.rs`), every job completed with exit code 0 was automatically advanced to `In Review` + `Role: QA-Needed`:

```rust
// worker.rs:199
// 2. Advance workflow state to In Review + QA label
let _ = linear.set_in_review_and_qa_label(&item.issue_id).await;
```

This created phantom `COMPLETED` issues and wasted QA review cycles whenever a builder:
- Did not push changes to origin;
- Failed to provide verifiable coordinates (`repo`, `branch`, `commit`, `path`);
- Provided local-only artifacts without repository coordinates.

## 2. Gate Preflight Architecture
Under MIN-236, the transition to `In Review` is strictly guarded by the Handoff Gate preflight check:

```rust
pub fn validate_handoff_gate(comment_body: &str) -> Result<HandoffCoordinates, GateError> {
    // 1. Rule 3: Detect local-only unpushed declarations
    if is_local_only_claim(comment_body) {
        return Err(GateError::LocalOnlyBlocker);
    }
    
    // 2. Rule 1: Extract and verify 4 coordinates
    let repo = extract_repo(comment_body).ok_or(GateError::MissingRepo)?;
    let branch = extract_branch(comment_body).ok_or(GateError::MissingBranch)?;
    let commit = extract_commit(comment_body).ok_or(GateError::MissingCommit)?;
    let paths = extract_paths(comment_body);
    if paths.is_empty() {
        return Err(GateError::MissingPaths);
    }
    let verify_cmd = extract_verify_cmd(comment_body).ok_or(GateError::MissingVerifyCmd)?;

    Ok(HandoffCoordinates { repo, branch, commit, paths, verify_cmd })
}
```

## 3. Worker Outbox Loop Guard
In `run_outbox_loop`:

```rust
match validate_handoff_gate(&item.comment_body) {
    Ok(_coords) => {
        // Handoff Gate passed: advance to In Review + Role: QA-Needed
        let _ = linear.set_in_review_and_qa_label(&item.issue_id).await;
        info!("Issue {} passed Handoff Gate, transitioned to In Review", item.identifier);
    }
    Err(err) => {
        // Intercepted at Handoff Gate: DO NOT transition to In Review!
        warn!("Issue {} failed Handoff Gate ({:?}), issuing auto-REWORK", item.identifier, err);
        let rework_body = format!(
            "🤖 **Handoff Gate Intercept**\n<!-- handoff-gate:auto-rework -->\n\
            Сдача отклонена на гейте: {}\n\
            В соответствии с MIN-236 Rule 1 & 4 тикет НЕ переходит в In Review. QA-лейн не вызывается.",
            err.to_rework_message()
        );
        let _ = linear.post_comment(&item.issue_id, &rework_body).await;
    }
}
```

## 4. Verification & Testing
The complete logic and regression test suite is verified via `fleetd-evolution/router/handoff_gate.py` and `fleetd-evolution/tests/test_handoff_gate.py`.
