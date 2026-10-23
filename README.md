# Flash Settlement: x402 Micropayment + Matchmaker Badge + Budget Guard

**Linear Ticket:** `MIN-147` (Original Spec: `MIN-116`)  
**Specification:** `PRD-A2A-REFERRAL-FLASH.md` (Flash settlement with matchmaker after deal close)  
**Agent Role:** `Builder: agy` on Wolfram  
**QA Auditor:** `Codex`  
**Protocol Scheme:** `TESTNET_SIMULATED_HMAC_SHA256` (100% offline-verifiable cryptographic testnet)  
**Default Currency:** `GRACE`  

---

## 1. Simulation Notice & Cryptographic Discipline

> [!NOTE]
> **SIMULATION NOTICE:** This repository and prototype operate strictly under an explicit **Testnet Simulation** model. Real money, real blockchain gas, and real private keys are strictly prohibited.
> 
> All cryptographic operations (payment authorizations, facilitator settlements, and reputation badges) use deterministic HMAC-SHA256 over canonical JSON payloads (`sort_keys=True, separators=(',', ':')`). Every artifact is explicitly tagged with `"simulation": true` and `"crypto_scheme": "TESTNET_SIMULATED_HMAC_SHA256"`.

---

## 2. Architecture & Acceptance Gates

The implementation fulfills all four acceptance gates required by `MIN-147` / `MIN-116`:

```
   [Client / Payer]                     [Facilitator]                   [Matchmaker]
          │                                   │                              │
          │ 1. Request Deal Closure           │                              │
          ├──────────────────────────────────►│                              │
          │                                   │                              │
          │ 2. HTTP 402 Payment Required      │                              │
          │    (challenge_id, nonce, GRACE)   │                              │
          │◄──────────────────────────────────┤                              │
          │                                   │                              │
          │ 3. Sign Payment with Wallet Key   │                              │
          │    (HMAC-SHA256 authorization)    │                              │
          ├──────────────────────────────────►│                              │
          │                                   │                              │
          │                                   │ 4. Evaluate Budget Guard     │
          │                                   │    (per-task, weekly, ledger)│
          │                                   │                              │
          │                                   │ 5. Execute Settlement        │
          │                                   │    & Issue Signed Badge      │
          │                                   ├─────────────────────────────►│
          │                                   │                              │
          │ 6. Settlement Receipt Confirmed   │                              │
          │◄──────────────────────────────────┤                              │
```

### Acceptance Gates (END != DONE):
- **G1: x402 Flow End-to-End**:
  - `create-challenge`: Generates an HTTP 402 challenge with nonce, TTL, amount, and `GRACE` denomination.
  - `sign-payment`: Payer wallet signs the challenge using HMAC-SHA256.
  - `settle-payment`: Facilitator cryptographically validates signature, applies budget guard, records ledger entry, and produces settlement receipt.
- **G2: Matchmaker Reputation Badge & Independent Verification**:
  - `issue-badge`: Generates a verifiable badge specifying recipient DID, deal ID, tier (e.g. `PLATINUM`, `GOLD`), score, issued time, and expiration.
  - `verify-badge`: Independent zero-trust verification tool. Recalculates HMAC signature from raw payload, verifies issuer authority, and enforces expiration dates.
  - Reject tampered badges (e.g. score altered) and expired badges with exit code 1.
- **G3: Codex Budget Guard**:
  - Mandatory per-task spending cap (default: `100.0 GRACE`).
  - Rolling weekly spending cap (default: `500.0 GRACE`).
  - Emergency kill-switch (`ON` / `OFF`).
  - Persistent JSON audit ledger tracking `APPROVED` and `REJECTED_BUDGET_GUARD` events.
  - Excessive transactions are rejected with exit code 1.
- **G4: Evidence Bundle**:
  - Complete artifacts in `evidence/` directory:
    - `G1_X402_FLOW.json`: Full request, challenge, payment, and settlement objects.
    - `G2_BADGE_VERIFICATION.json`: Issued badge, valid verification, and tamper detection proofs.
    - `G3_BUDGET_GUARD.json`: Guard config, cap violation results, and ledger audit.
    - `test_run.log`: Verifiable log of all 11 test assertions passing.
    - `MANIFEST.sha256`: SHA-256 integrity manifest.

---

## 3. Directory Layout

```
.
├── AGENTS.md                          # Mindrock Network Constitution
├── LICENSE                            # MIT License
├── README.md                          # This documentation
├── flash_settlement.py                # Root CLI convenience wrapper
├── test_flash.sh                      # Root test suite convenience wrapper
├── hackt-mandate/
│   ├── flash_settlement.py            # Canonical implementation CLI & library
│   └── test_flash.sh                  # Comprehensive 11-test acceptance suite
└── evidence/
    ├── EVIDENCE.json                  # Machine-readable delivery envelope
    ├── G1_X402_FLOW.json              # Gate 1 evidence
    ├── G2_BADGE_VERIFICATION.json     # Gate 2 evidence
    ├── G3_BUDGET_GUARD.json           # Gate 3 evidence
    ├── MANIFEST.sha256                # SHA-256 manifest
    └── test_run.log                   # 11/11 PASS test output
```

---

## 4. Quickstart & Verification Commands

### Run Acceptance Test Suite
```bash
bash hackt-mandate/test_flash.sh
# or from root:
bash test_flash.sh
```
Expected output:
```
==============================================================================
⚡ Starting Flash Settlement Test Suite (MIN-147 / MIN-116)
   Simulation Notice: Testnet simulation using HMAC-SHA256 (no real funds)
   CLI: .../hackt-mandate/flash_settlement.py
==============================================================================
Test 1: CLI and Python preflight...
  ✅ PASS: CLI syntax and help output verified
Test 2: [G1] Creating HTTP 402 challenge...
  ✅ PASS: x402 challenge generated with GRACE denomination and explicit simulation marker
Test 3: [G1] Signing payment authorization...
  ✅ PASS: Payment authorization cryptographically signed with HMAC-SHA256
Test 4: [G1] Facilitator settlement and verification...
  ✅ PASS: Settlement executed, facilitator signature generated, badge issued
Test 5: [G2] Direct matchmaker reputation badge issuance...
  ✅ PASS: Matchmaker reputation badge issued with tier, score, and expiration attributes
Test 6: [G2] Independent badge cryptographic verification...
  ✅ PASS: Independent badge verification confirmed VALID (exit code 0)
Test 7: [G2] Tampered badge detection...
  ✅ PASS: Tampered badge correctly rejected with exit code 1 (BADGE_SIGNATURE_MISMATCH)
Test 8: [G2] Invalid issuer key rejection...
  ✅ PASS: Badge verification rejected unauthorized key with exit code 1
Test 9: [G3] Budget guard per-task cap enforcement...
  ✅ PASS: Transaction exceeding per-task cap rejected with exit code 1 and logged to ledger
Test 10: [G3] Budget guard emergency kill-switch...
  ✅ PASS: Payment rejected while kill-switch is active (KILL_SWITCH_ACTIVE, exit code 1)
Test 11: [G3/G4] Ledger audit summary verification...
  ✅ PASS: Ledger audit verified: 1 approved, 2 rejected transactions recorded
==============================================================================
📊 Test Summary:
   Passed: 11, Failed: 0
==============================================================================
🎉 All acceptance gates (G1-G4) successfully passed!
```

### Clean Working Tree Guarantee
After running tests, verify repository cleanliness:
```bash
git status --porcelain=v1
# Output is clean (empty)
```
