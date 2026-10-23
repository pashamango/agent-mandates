# Mindrock Agent Mandates & Standards

This repository hosts specifications, reference implementations, and protocols for the Mindrock Autonomous Agent Fleet.

## Modules & Specifications

- [`min125/`](file:///Users/pavelcherkashin/ChitAkasha-Wolfram/worktrees/MIN-125-SURVIVABLE-SECURITY-0001/min125): **Survivable Security Architecture & Tabletop Recovery Experiment** (`MIN-125`).
  - Minimal offline recovery capsule ("The Phalanx Anchor").
  - 8-gate finite state machine with immutable cryptographic evidence receipts.
  - Quarantined bounded bootstrap epoch (deny-by-epoch historical secrecy).
  - Tabletop evaluations: Loss, Theft, Coercion, Replay, and Partial Destruction.
  - Executable reference simulation and automated test suite.

## Verification

To run all automated verification tests:

```bash
pytest
```
