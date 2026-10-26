# Fleetd Handoff Gate Daemon Enforcement Architecture (Part B)
**Reference:** MIN-236  
**Status:** Implemented & Verified in Rust Dispatcher (`fleetd`) and Python Validator (`router/handoff_gate.py`)  
**Revision:** 1.1 (Defects 1-4 Resolution)

## 1. Overview
In the original `fleetd` Rust worker (`src/worker.rs`), every job was automatically advanced to `In Review` + `Role: QA-Needed` upon delivery:

```rust
// Previous unverified worker behavior:
let _ = linear.set_in_review_and_qa_label(&item.issue_id).await;
```

This created phantom `COMPLETED` issues and wasted QA review cycles whenever a builder:
- Did not push changes to origin;
- Failed to provide verifiable coordinates (`repo`, `branch`, `commit`, `path`);
- Provided local-only artifacts without repository coordinates.

## 2. Gate Preflight Architecture in Rust
Under MIN-236, the transition to `In Review` is strictly guarded by the Handoff Gate preflight check implemented in `fleetd::handoff_gate::validate_handoff_gate`:

```rust
pub async fn validate_handoff_gate(
    comment_body: &str,
    expected_branch: Option<&str>,
    builder: &str,
    verify_remote: bool,
) -> Result<HandoffCoordinates, GateError>
```

The preflight check enforces:
1. **Rule 3: Local-Only Blocker Check:** Detects local-only claims (`has_local_only_indicator`) without remote push coordinates. Returns `GateError::LocalOnlyBlocker` with `<!-- handoff-gate:blocker -->`.
2. **Rule 1: Coordinate Extraction:** Extracts `repo`, `branch`, `commit`, `paths`, and `verify_cmd` from either `<!-- phi-envelope:... -->` or Markdown Evidence sections. Missing coordinates return `GateError::MissingCoordinates`.
3. **Verification Command Integrity (Defect 2 Fix):** Confirms that `verify_cmd` explicitly fixes and contains the full declared `commit` (SHA). An unpinned command with branch name alone is rejected with `GateError::InvalidVerificationCommand`.
4. **Expected Branch Matching (Defect 3 Fix):** The worker dynamically resolves the issue's assigned `branchName` from Linear via GraphQL query `GetIssueBranch` and validates that the report's `branch` matches `expected_branch`. Mismatches return `GateError::BranchMismatch`.
5. **Builder Identification (Defect 4 Fix):** Persists `builder` in `outbox.builder` and resolves builder via `resolve_builder(item.builder, marker, comment_body)`, ensuring auto-REWORK comments correctly address `@codex`, `@agy`, or any active slot.
6. **Remote Verifiability (`verify_remote_git`) (Defect 1 Fix):**
   - Remote repository accessibility check via `git ls-remote --exit-code <repo>`.
   - Remote branch existence check via `git ls-remote --exit-code <repo> refs/heads/<branch>`.
   - Clean fetch into temporary directory (`/tmp/fleetd_gate_verify_<uuid>`).
   - Branch fetched into dedicated reference `refs/remotes/origin/handoff-branch` via `+refs/heads/<branch>:refs/remotes/origin/handoff-branch`.
   - Direct fetch of commit does NOT overwrite `FETCH_HEAD` for ancestry check; commit ancestry is verified strictly against `refs/remotes/origin/handoff-branch` via `git merge-base --is-ancestor <commit> refs/remotes/origin/handoff-branch`.
   - Presence of each artifact path in commit tree via `git cat-file -e <commit>:<path>`.

## 3. Worker Outbox Loop Integration
In `crates/fleetd/src/worker.rs`, the outbox processing pipeline is strictly gated before any status mutation:

```rust
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
```

## 4. Verification & Testing

### Rust Test Suites:
1. `crates/fleetd/tests/acceptance.rs`:
   - `test_rust_acceptance_11_handoff_gate_enforcement`: Tests all 5 failure modes (PRD-35, MIN-194, fake remote, missing commit, missing artifact path) and verified delivery against canonical git repositories. (11 passed).
2. `crates/fleetd/tests/handoff_gate_integration.rs`:
   - Full outbox pipeline integration tests against a mock Linear API verifying that on gate failure:
     - `MoveState` (In Review) is **never** called (`0` calls).
     - `AddLabel` (`Role: QA-Needed`) is **never** called (`0` calls).
     - An auto-REWORK comment is posted addressed to the correct builder (`@agy` or `@codex`).
     - Branch mismatch between Linear ticket and report coordinates triggers auto-REWORK and blocks transition.
     - Commit belonging to another branch is rejected via ancestry check against `refs/remotes/origin/handoff-branch`.
     - Verification command without explicit commit SHA fails gate.
     - Blocker semantics are enforced for local-only declarations. (10 passed).

### Python Test Suite:
- `fleetd-evolution/tests/test_handoff_gate.py`: 18 comprehensive tests covering syntax, local blockers, remote verifiability, branch ancestry, verification command pinning, and reviewer counterexamples. (18 passed).
