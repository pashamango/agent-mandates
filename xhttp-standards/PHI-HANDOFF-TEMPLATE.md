# Protocol Phi Handoff Template (Φ-0.1)

Use this template when handing an artifact to an independent builder or QA
agent. The handoff is a coordinate receipt, not an authorization to execute,
publish, or deploy anything.

## 1. Intent and safety boundary

```yaml
proto: phi-0.1
intent: HANDOFF_FOR_QA
sender: did:key:<sender-key>#<agent>@<host>
recipient: did:key:<recipient-key>#<agent>@<host>
authority: <ticket-or-mandate-id>
execution_authorized: false
publication_authorized: false
```

The receiver must treat missing or ambiguous fields as `evidence-unavailable`.
Do not search unnamed repositories or infer authorization from a successful
local command.

## 2. Git coordinates

```yaml
git:
  repo_url: https://github.com/<owner>/<repo>.git
  branch: <remote-branch-name>
  commit_sha: <40-lowercase-hex>
  tree_sha: <40-lowercase-hex>
  manifest_sha256: <64-lowercase-hex>
  artifact_paths:
    - path/to/artifact
  verification_cmd: >-
    git clone --branch <remote-branch-name> --no-single-branch
    https://github.com/<owner>/<repo>.git verify &&
    cd verify && git rev-parse HEAD
```

The receiver must independently confirm that `commit_sha` exists on the named
remote branch, that every artifact path is present at that commit, and that
the verification command resolves to the same commit. A local-only commit is
not a handoff.

## 3. Verification receipt

```yaml
checks:
  - name: <check-name>
    command: <exact-command>
    result: PASS | FAIL | NOT_RUN
    evidence: <log-path-or-short-result>
limitations:
  - <known gap, if any>
```

Attach logs or stable artifact paths for each `PASS`. Keep credentials,
private keys, internal hostnames, and private URLs out of this public receipt.

## 4. Receiver verdict

The receiver should emit one machine-readable verdict in the ticket thread:

```yaml
verdict: PASS | REWORK
reason: <one-line reason>
missing_or_failed_checks:
  - <check-name>
```

`PASS` means only that the named artifact passed the named checks. It does not
mean merged, released, deployed, or publicly published.
