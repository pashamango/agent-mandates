# Flash Protocol: Intent Board & Settlement Reference Implementation

**Linear Tickets:** `MIN-146` (MIN-115) & `MIN-147` (MIN-116)
**Authority:** Principal Pavel Cherkashin (`pavel@mindrock.capital`)
**Specification:** `PRD-A2A-REFERRAL-FLASH.md` (A2A Referral Flash Protocol — trust without centralized validator)
**Agent Role:** `Builder: agy` on Wolfram
**QA Auditor:** `Codex`
**Cryptographic Schemes:** `Ed25519_RFC8032` (goDNS + Multipass) & `TESTNET_SIMULATED_HMAC_SHA256` (x402 Micropayments)
**Settlement Currency:** `GRACE`

---

## 1. Simulation Notice & Zero-Trust Protocol Architecture

> [!NOTE]
> **SIMULATION NOTICE:** This repository operates under an explicit **Testnet Simulation** model. No real funds, real blockchain network calls, or production wallet keys are used or accepted.
> 
> All cryptographic operations (intent signatures, multipass verifications, and reputation badges) use deterministic Ed25519 (RFC 8032) and HMAC-SHA256 over canonical JSON payloads (RFC 8785). Every artifact is tagged with `"simulation": true`.

---

## 2. Flash Protocol: Intent Board Architecture (MIN-146 / MIN-115)

```
[Agent Alpha (WANT)]             [Intent Board & Matchmaker]            [Agent Beta (OFFER)]
         │                                   │                                    │
         │ 1. multipass.publish_intent       │                                    │
         │    (Canonical JSON, Ed25519, TTL) │                                    │
         ├──────────────────────────────────►│                                    │
         │                                   │ 2. multipass.publish_intent        │
         │                                   │    (Canonical JSON, Ed25519, TTL)  │
         │                                   │◄───────────────────────────────────┤
         │                                   │                                    │
         │                                   │ 3. multipass.match_intents         │
         │                                   │    - Discover complementary pairs  │
         │                                   │    - multipass.verify (Alpha goDNS)│
         │                                   │    - multipass.verify (Beta goDNS) │
         │                                   │                                    │
         │                                   ▼                                    │
         │                     [Human-in-the-Loop Gate]                           │
         │                     "валяй" / Architect Closure                        │
         │                                   │                                    │
         │                      ┌────────────┴────────────┐                       │
         │                      │                         │                       │
         │             [Human Approved "валяй"]   [Human Denied / Fail]           │
         │                      │                         │                       │
         │                      ▼                         ▼                       │
         │             [Flash Execution]          [Flash Rollback]                │
         │             - Status: EXECUTED         - Status: ROLLED_BACK           │
         │             - Atomic Settlement (x402) - Zero funds deducted           │
         │             - Matchmaker Badge Issued  - Intents restored to ACTIVE    │
```

### Acceptance Gates (END != DONE):
- **G1: Intent Schema & Cryptographic Signing**:
  - `publish-intent`: Publishes canonical JSON intent (`WANT` or `OFFER`) signed with Ed25519 private seed, bound by TTL, nonce, and `goDNS` anchor (`_mandate.<agent>.<domain>`).
- **G2: End-to-End Matchmaking**:
  - `match-intents`: Automatically pairs complementary intents (`WANT` budget >= `OFFER` ask) within the target category (e.g. `compute:gpu`).
  - Verifies both parties' multipass credentials against DNS anchors.
- **G3: Stranger Multipass Verification (Negative Tests Enforced)**:
  - `verify-multipass`: Validates stranger's Multipass using public key anchored in `goDNS` TXT record and checks Ed25519 signature over canonical payload.
  - Negative tests verify rejection of:
    - Tampered payload (signature mismatch, exit code 1).
    - Spoofed DID / DNS anchor mismatch (exit code 1).
    - Expired TTL (exit code 1).
- **G4: Evidence Bundle & Flash Semantics**:
  - Human-in-the-loop closure gate: human tap (`"валяй"` approval).
  - Flash execution: atomic execution and badge issuance upon approval.
  - Flash rollback: zero-loss guarantee (`funds_deducted: 0.0`) on denial or collapse.
  - Machine-readable evidence bundle in `evidence/`.

---

## 3. Directory Layout

```
.
├── AGENTS.md                          # Mindrock Network Constitution
├── LICENSE                            # MIT License
├── README.md                          # Architecture & Verification Guide
├── flash_settlement.py                # Root CLI convenience wrapper
├── test_intent.sh                     # Intent Board test runner (MIN-146)
├── test_flash.sh                      # Settlement test runner (MIN-147)
├── hackt-mandate/
│   ├── flash_settlement.py            # Canonical reference implementation
│   ├── test_intent.sh                 # Comprehensive 14-test Intent Board suite (G1-G4)
│   └── test_flash.sh                  # Comprehensive 11-test Settlement suite (G1-G4)
└── evidence/
    ├── EVIDENCE_INTENT.json           # Intent board evidence envelope
    ├── G1_INTENT_SCHEMA.json          # Gate 1 canonical intent artifact
    ├── G2_MATCHMAKING.json            # Gate 2 matched deal artifact
    ├── G3_MULTIPASS_VERIFY.json       # Gate 3 stranger verification artifact
    ├── G4_FLASH_SEMANTICS.json        # Gate 4 flash execution artifact
    ├── FLASH_ROLLBACK_ZERO_LOSS.json  # Zero-loss rollback proof
    ├── MANIFEST.sha256                # Cryptographic checksum manifest
    └── test_intent.log                # 14/14 PASS test output
```

---

## 4. Verification & QA Commands

### 1. Intent Board Test Suite (MIN-146 / MIN-115)
```bash
./hackt-mandate/test_intent.sh
# Expected output: 14 passed, 0 failed
```

### 2. Flash Settlement Suite (MIN-147 / MIN-116)
```bash
./hackt-mandate/test_flash.sh
# Expected output: 11 passed, 0 failed
```

### 3. QA Preflight Validation
```bash
test -f hackt-mandate/flash_settlement.py && \
test -f hackt-mandate/test_intent.sh && \
test -f hackt-mandate/test_flash.sh && \
(cd hackt-mandate && ./test_intent.sh) && \
(cd hackt-mandate && ./test_flash.sh)
```
