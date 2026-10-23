# MIN-125: Survivable Security Architecture & Tabletop Recovery Experiment

## Overview

This repository contains the architecture, formal gated state machine specification, tabletop evaluation, and executable simulation test suite for **Survivable Security in Nonprofit Critical Systems** (Linear: `MIN-125`).

Classic security optimizes confidentiality while assuming institutions, devices, administrators, and key hierarchies survive intact. Catastrophe falsifies this assumption: primary vaults are destroyed, key custodians become unavailable, credentials are lost, and legitimate successors cannot be distinguished from adversaries. A system that remains impenetrable but completely unrecoverable after catastrophe is secure only in a fragile, pre-catastrophe sense.

The survivable security architecture replaces this failure mode with **Graceful Degradation and Constrained Recovery**:
1. **The Phalanx Capsule**: A minimal offline recovery artifact that is physically accessible, human-scale, and memorable, encoding minimum identity and entropy shards without ever functioning as an unrestricted master key.
2. **Gated State Machine**: An 8-gate transition sequence (`Identity`, `Liveness`, `Quorum Witness`, `Mandatory Delay`, `Freshness/Revocation`, `Bounded Bootstrap`, `Audit Trail`, and `Post-Recovery Key Rotation`) where every transition produces an immutable, cryptographically verifiable evidence receipt.
3. **Quarantined Bounded Bootstrap**: An epoch-gated security model where recovery yields only an ephemeral, auditable bootstrap environment. Historical secrets and legacy vault data remain cryptographically inaccessible to whoever holds the capsule alone.
4. **Tabletop Verification**: Rigorous scenario testing against **Loss**, **Theft**, **Coercion**, **Replay**, and **Partial Destruction**.

---

## Directory Layout

```
min125/
├── README.md                      # This directory guide
├── report/
│   └── MIN-125-report.md          # Canonical Design Document, State Machine Spec & Scenario Table
├── recovery_simulation.py         # Complete Python reference implementation of Capsule & 8-Gate State Machine
└── test_recovery.py               # Automated pytest suite verifying all gates and tabletop scenarios
```

---

## Running the Verification Suite

Run the automated test suite verifying all 8 gates, state transitions, evidence certificates, and tabletop attack scenarios:

```bash
# Run pytest directly on the min125 test suite
pytest min125/test_recovery.py -v

# Or run the reference simulation runner
python3 min125/recovery_simulation.py
```

All scenarios generate synthetic cryptographic evidence receipts adhering to Protocol Phi formatting and clean working tree standards.
