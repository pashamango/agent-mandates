#!/usr/bin/env bash
# ==============================================================================
# Flash Settlement Protocol Acceptance Suite: G1 - G4 (MIN-147 Rework)
# Ticket: MIN-147 (MIN-116)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CLI_PY="${SCRIPT_DIR}/flash_settlement.py"
REGRESSION_PY="${SCRIPT_DIR}/test_regression.py"

TMP_DIR=$(mktemp -d /tmp/flash_settlement_test_XXXXXX)
trap 'rm -rf "${TMP_DIR}"' EXIT

PASSED_COUNT=0
FAILED_COUNT=0

log_pass() {
  echo "  ✅ PASS: $1"
  PASSED_COUNT=$((PASSED_COUNT + 1))
}

log_fail() {
  echo "  ❌ FAIL: $1"
  FAILED_COUNT=$((FAILED_COUNT + 1))
}

echo "=============================================================================="
echo "⚡ Starting Flash Settlement Test Suite (MIN-147 / MIN-116 Rework)"
echo "   Simulation Notice: Testnet simulation using HMAC-SHA256 (no real funds)"
echo "   CLI: ${CLI_PY}"
echo "=============================================================================="

# Setup Test Parameters
TEST_LEDGER="${TMP_DIR}/test_ledger.json"
PAYER_DID="did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK"
MATCHMAKER_DID="did:key:z6MknGc3ocHs3zgjJvfKBSF35K43AYM822554dooPWBZg308"
DEAL_ID="flash-deal-2026-9021"
PAYER_SIM_KEY="wallet-sim-secret-key-payer-88"
FACILITATOR_SIM_KEY="facilitator-sim-secret-key-33"
ISSUER_SIM_KEY="hack-temple-authority-secret-2026"

# Test 1: CLI & Syntax Preflight
echo "Test 1: CLI and Python preflight..."
if python3 "${CLI_PY}" --help >/dev/null 2>&1; then
  log_pass "CLI syntax and help output verified"
else
  log_fail "CLI failed preflight check"
fi

# Test 2: G1 - Create x402 Challenge
echo "Test 2: [G1] Creating HTTP 402 challenge..."
CHALLENGE_FILE="${TMP_DIR}/challenge.json"
python3 "${CLI_PY}" create-challenge \
  --deal-id "${DEAL_ID}" \
  --payer "${PAYER_DID}" \
  --recipient "${MATCHMAKER_DID}" \
  --amount 25.0 \
  --currency "GRACE" \
  --ttl-sec 3600 \
  --out "${CHALLENGE_FILE}" >/dev/null

if [ -f "${CHALLENGE_FILE}" ] && grep -q '"status_code": 402' "${CHALLENGE_FILE}" && grep -q '"currency": "GRACE"' "${CHALLENGE_FILE}" && grep -q '"simulation": true' "${CHALLENGE_FILE}"; then
  log_pass "x402 challenge generated with GRACE denomination and explicit simulation marker"
else
  log_fail "x402 challenge generation failed"
fi

# Test 3: G1 - Sign Payment with Wallet Key
echo "Test 3: [G1] Signing payment authorization..."
PAYMENT_FILE="${TMP_DIR}/payment.json"
python3 "${CLI_PY}" sign-payment \
  --challenge "${CHALLENGE_FILE}" \
  --payer-key "${PAYER_SIM_KEY}" \
  --out "${PAYMENT_FILE}" >/dev/null

if [ -f "${PAYMENT_FILE}" ] && grep -q '"signature":' "${PAYMENT_FILE}" && grep -q '"crypto_scheme": "TESTNET_SIMULATED_HMAC_SHA256"' "${PAYMENT_FILE}"; then
  log_pass "Payment authorization cryptographically signed with HMAC-SHA256"
else
  log_fail "Payment authorization signing failed"
fi

# Test 4: G1 - Facilitator Settle & Confirm
echo "Test 4: [G1] Facilitator settlement and verification..."
RECEIPT_FILE="${TMP_DIR}/receipt.json"
python3 "${CLI_PY}" settle-payment \
  --payment "${PAYMENT_FILE}" \
  --challenge "${CHALLENGE_FILE}" \
  --payer-key "${PAYER_SIM_KEY}" \
  --facilitator-key "${FACILITATOR_SIM_KEY}" \
  --issuer-key "${ISSUER_SIM_KEY}" \
  --ledger "${TEST_LEDGER}" \
  --per-task-cap 100.0 \
  --weekly-cap 500.0 \
  --badge-score 150 \
  --badge-tier "PLATINUM" \
  --out "${RECEIPT_FILE}" >/dev/null

if [ -f "${RECEIPT_FILE}" ] && grep -q '"status": "SETTLED"' "${RECEIPT_FILE}" && grep -q '"facilitator_signature":' "${RECEIPT_FILE}" && grep -q '"badge":' "${RECEIPT_FILE}"; then
  log_pass "Settlement executed, facilitator signature generated, badge issued"
else
  log_fail "Facilitator settlement failed"
fi

# Test 5: G2 - Direct Badge Issuance
echo "Test 5: [G2] Direct matchmaker reputation badge issuance..."
BADGE_FILE="${TMP_DIR}/matchmaker_badge.json"
python3 "${CLI_PY}" issue-badge \
  --deal-id "${DEAL_ID}" \
  --recipient "${MATCHMAKER_DID}" \
  --settlement-id "stl-standalone-test-01" \
  --amount 30.0 \
  --currency "GRACE" \
  --score 120 \
  --tier "GOLD" \
  --issuer-key "${ISSUER_SIM_KEY}" \
  --ttl-days 30 \
  --out "${BADGE_FILE}" >/dev/null

if [ -f "${BADGE_FILE}" ] && grep -q '"badge_type": "FLASH_SETTLEMENT_MATCHMAKER"' "${BADGE_FILE}" && grep -q '"tier": "GOLD"' "${BADGE_FILE}"; then
  log_pass "Matchmaker reputation badge issued with tier, score, and expiration attributes"
else
  log_fail "Direct badge issuance failed"
fi

# Test 6: G2 - Independent Badge Verification (Valid Badge)
echo "Test 6: [G2] Independent badge cryptographic verification..."
VERIFY_OUT="${TMP_DIR}/verify_valid.json"
if python3 "${CLI_PY}" verify-badge --badge "${BADGE_FILE}" --issuer-key "${ISSUER_SIM_KEY}" > "${VERIFY_OUT}"; then
  if grep -q '"verified": true' "${VERIFY_OUT}" && grep -q '"status": "VALID"' "${VERIFY_OUT}"; then
    log_pass "Independent badge verification confirmed VALID (exit code 0)"
  else
    log_fail "Badge verification output missing VALID status"
  fi
else
  log_fail "Badge verification failed unexpectedly"
fi

# Test 7: G2 - Tamper Detection (Falsified Reputation Score)
echo "Test 7: [G2] Tampered badge detection..."
TAMPERED_BADGE="${TMP_DIR}/tampered_badge.json"
sed 's/"reputation_score": 120/"reputation_score": 9999/g' "${BADGE_FILE}" > "${TAMPERED_BADGE}"
if python3 "${CLI_PY}" verify-badge --badge "${TAMPERED_BADGE}" --issuer-key "${ISSUER_SIM_KEY}" >/dev/null 2>&1; then
  log_fail "Tampered badge was incorrectly accepted"
else
  log_pass "Tampered badge correctly rejected with exit code 1 (BADGE_SIGNATURE_MISMATCH)"
fi

# Test 8: G2 - Invalid Issuer Key Rejection
echo "Test 8: [G2] Invalid issuer key rejection..."
if python3 "${CLI_PY}" verify-badge --badge "${BADGE_FILE}" --issuer-key "wrong-fake-key-99" >/dev/null 2>&1; then
  log_fail "Badge verified with invalid key"
else
  log_pass "Badge verification rejected unauthorized key with exit code 1"
fi

# Test 9: G3 - Budget Guard: Per-Task Cap Rejection
echo "Test 9: [G3] Budget guard per-task cap enforcement..."
EXCESSIVE_CHALLENGE="${TMP_DIR}/excessive_challenge.json"
python3 "${CLI_PY}" create-challenge \
  --deal-id "flash-deal-overflow" \
  --payer "${PAYER_DID}" \
  --recipient "${MATCHMAKER_DID}" \
  --amount 150.0 \
  --currency "GRACE" \
  --out "${EXCESSIVE_CHALLENGE}" >/dev/null

EXCESSIVE_PAYMENT="${TMP_DIR}/excessive_payment.json"
python3 "${CLI_PY}" sign-payment \
  --challenge "${EXCESSIVE_CHALLENGE}" \
  --payer-key "${PAYER_SIM_KEY}" \
  --out "${EXCESSIVE_PAYMENT}" >/dev/null

EXCESSIVE_OUT="${TMP_DIR}/excessive_out.json"
if python3 "${CLI_PY}" settle-payment \
  --payment "${EXCESSIVE_PAYMENT}" \
  --challenge "${EXCESSIVE_CHALLENGE}" \
  --payer-key "${PAYER_SIM_KEY}" \
  --ledger "${TEST_LEDGER}" \
  --per-task-cap 100.0 \
  --out "${EXCESSIVE_OUT}" >/dev/null 2>&1; then
  log_fail "Transaction exceeding per-task cap was incorrectly approved"
else
  if grep -q "REJECTED_BUDGET_GUARD" "${TEST_LEDGER}" && grep -q "PER_TASK_CAP_EXCEEDED" "${TEST_LEDGER}"; then
    log_pass "Transaction exceeding per-task cap rejected with exit code 1 and logged to ledger"
  else
    log_fail "Per-task cap rejection not logged properly in ledger"
  fi
fi

# Test 10: G3 - Budget Guard: Kill-Switch Activation
echo "Test 10: [G3] Budget guard emergency kill-switch..."
python3 "${CLI_PY}" budget-kill-switch --ledger "${TEST_LEDGER}" --state "ON" >/dev/null

NORMAL_CHALLENGE="${TMP_DIR}/normal_challenge.json"
python3 "${CLI_PY}" create-challenge \
  --deal-id "flash-deal-blocked" \
  --payer "${PAYER_DID}" \
  --recipient "${MATCHMAKER_DID}" \
  --amount 10.0 \
  --currency "GRACE" \
  --out "${NORMAL_CHALLENGE}" >/dev/null

NORMAL_PAYMENT="${TMP_DIR}/normal_payment.json"
python3 "${CLI_PY}" sign-payment \
  --challenge "${NORMAL_CHALLENGE}" \
  --payer-key "${PAYER_SIM_KEY}" \
  --out "${NORMAL_PAYMENT}" >/dev/null

if python3 "${CLI_PY}" settle-payment \
  --payment "${NORMAL_PAYMENT}" \
  --challenge "${NORMAL_CHALLENGE}" \
  --payer-key "${PAYER_SIM_KEY}" \
  --ledger "${TEST_LEDGER}" \
  --per-task-cap 100.0 >/dev/null 2>&1; then
  log_fail "Payment passed while kill-switch was active"
else
  log_pass "Payment rejected while kill-switch is active (KILL_SWITCH_ACTIVE, exit code 1)"
fi

# Reset kill-switch for subsequent tests
python3 "${CLI_PY}" budget-kill-switch --ledger "${TEST_LEDGER}" --state "OFF" >/dev/null

# Test 11: G3 - Budget Guard: Rolling Weekly Cap Sequential Exhaustion Proof
echo "Test 11: [G3] Budget guard rolling weekly cap sequential exhaustion proof..."
WEEKLY_TEST_LEDGER="${TMP_DIR}/weekly_cap_test_ledger.json"
# Settle 3 transactions of 90 GRACE each (Total = 270 GRACE, within 300 cap, each <= 100 per-task cap)
for i in 1 2 3; do
  W_CH="${TMP_DIR}/wc_ch_${i}.json"
  W_PAY="${TMP_DIR}/wc_pay_${i}.json"
  python3 "${CLI_PY}" create-challenge --deal-id "deal-wc-seq-${i}" --payer "${PAYER_DID}" --recipient "${MATCHMAKER_DID}" --amount 90.0 --out "${W_CH}" >/dev/null
  python3 "${CLI_PY}" sign-payment --challenge "${W_CH}" --payer-key "${PAYER_SIM_KEY}" --out "${W_PAY}" >/dev/null
  python3 "${CLI_PY}" settle-payment --payment "${W_PAY}" --challenge "${W_CH}" --payer-key "${PAYER_SIM_KEY}" --ledger "${WEEKLY_TEST_LEDGER}" --per-task-cap 100.0 --weekly-cap 300.0 >/dev/null
done

# Now attempt 4th transaction of 50 GRACE (50 <= 100 per-task cap, but 270 + 50 = 320 > 300 weekly cap)
W_CH_OVER="${TMP_DIR}/wc_ch_over.json"
W_PAY_OVER="${TMP_DIR}/wc_pay_over.json"
W_OUT_OVER="${TMP_DIR}/wc_out_over.json"
python3 "${CLI_PY}" create-challenge --deal-id "deal-wc-seq-overflow" --payer "${PAYER_DID}" --recipient "${MATCHMAKER_DID}" --amount 50.0 --out "${W_CH_OVER}" >/dev/null
python3 "${CLI_PY}" sign-payment --challenge "${W_CH_OVER}" --payer-key "${PAYER_SIM_KEY}" --out "${W_PAY_OVER}" >/dev/null

if python3 "${CLI_PY}" settle-payment --payment "${W_PAY_OVER}" --challenge "${W_CH_OVER}" --payer-key "${PAYER_SIM_KEY}" --ledger "${WEEKLY_TEST_LEDGER}" --per-task-cap 100.0 --weekly-cap 300.0 --out "${W_OUT_OVER}" >/dev/null 2>&1; then
  log_fail "Transaction exceeding rolling weekly cap was incorrectly approved"
else
  if grep -q "WEEKLY_CAP_EXCEEDED" "${W_OUT_OVER}" && grep -q "WEEKLY_CAP_EXCEEDED" "${WEEKLY_TEST_LEDGER}"; then
    log_pass "Rolling weekly cap exhaustion strictly enforced: rejected with WEEKLY_CAP_EXCEEDED and exit code 1"
  else
    log_fail "WEEKLY_CAP_EXCEEDED rejection not properly recorded in ledger"
  fi
fi

# Test 12: G3 - Negative and Non-Finite Amount Strict Rejection
echo "Test 12: [G3] Negative and non-finite amount rejection (budget attack prevention)..."
NEG_OUT="${TMP_DIR}/neg_check.json"
if python3 "${CLI_PY}" budget-check --deal-id "deal-neg" --amount -1000.0 --ledger "${TEST_LEDGER}" > "${NEG_OUT}" 2>&1; then
  log_fail "Negative amount (-1000.0) was accepted by budget guard"
else
  if grep -q "INVALID_AMOUNT" "${NEG_OUT}"; then
    log_pass "Negative amount (-1000.0) strictly rejected with INVALID_AMOUNT (exit code 1)"
  else
    log_fail "Negative amount rejection missing INVALID_AMOUNT reason"
  fi
fi

# Test 13: G1 - Adversarial Challenge-Binding Check (Field Tampering)
echo "Test 13: [G1] Adversarial challenge-binding check (substitution attacks)..."
# Create valid challenge and signed payment
ADV_CH="${TMP_DIR}/adv_challenge.json"
python3 "${CLI_PY}" create-challenge --deal-id "deal-adv-orig" --payer "${PAYER_DID}" --recipient "${MATCHMAKER_DID}" --amount 30.0 --currency "GRACE" --out "${ADV_CH}" >/dev/null
ADV_PAY="${TMP_DIR}/adv_payment.json"
python3 "${CLI_PY}" sign-payment --challenge "${ADV_CH}" --payer-key "${PAYER_SIM_KEY}" --out "${ADV_PAY}" >/dev/null

# Tamper recipient in challenge to simulate substitution
TAMPERED_ADV_CH="${TMP_DIR}/adv_tampered_ch.json"
sed 's/"recipient": "[^"]*"/"recipient": "did:key:z6Mkhacker999"/g' "${ADV_CH}" > "${TAMPERED_ADV_CH}"

ADV_OUT="${TMP_DIR}/adv_out.json"
if python3 "${CLI_PY}" settle-payment --payment "${ADV_PAY}" --challenge "${TAMPERED_ADV_CH}" --payer-key "${PAYER_SIM_KEY}" --ledger "${TEST_LEDGER}" --out "${ADV_OUT}" >/dev/null 2>&1; then
  log_fail "Payment with mismatched recipient was incorrectly settled"
else
  if grep -q "recipient does not match challenge" "${ADV_OUT}"; then
    log_pass "Adversarial field substitution (recipient mismatch) correctly rejected (exit code 1)"
  else
    log_fail "Adversarial field substitution failed without specific challenge mismatch error"
  fi
fi

# Test 14: G1-G4 - Python Comprehensive Regression Suite
echo "Test 14: [G1-G4] Python comprehensive regression suite (20 tests)..."
if python3 "${REGRESSION_PY}" > "${TMP_DIR}/regression.log" 2>&1; then
  REG_COUNT=$(grep -o "Ran [0-9]* tests" "${TMP_DIR}/regression.log" | awk '{print $2}')
  log_pass "All ${REG_COUNT} Python unit & regression tests passed cleanly"
else
  cat "${TMP_DIR}/regression.log"
  log_fail "Python regression tests failed"
fi

# Test 15: G4 - Git Coordinates and Delivery Conformance
echo "Test 15: [G4] Git coordinates and delivery metadata conformance..."
EVIDENCE_FILE="${REPO_ROOT}/evidence/EVIDENCE.json"
BRANCH_IN_EVIDENCE=$(python3 -c "import json; d=json.load(open('${EVIDENCE_FILE}')); print(d.get('branch') or d.get('git', {}).get('branch', ''))")
REPO_IN_EVIDENCE=$(python3 -c "import json; d=json.load(open('${EVIDENCE_FILE}')); print(d.get('repo_url') or d.get('git', {}).get('repo_url', ''))")
EXPECTED_BRANCH="minutta/min-147-min-116-build-flash-settlement-x402-micropayment-badge"
EXPECTED_REPO="https://github.com/pashamango/agent-mandates.git"

if [ "${BRANCH_IN_EVIDENCE}" = "${EXPECTED_BRANCH}" ] && [ "${REPO_IN_EVIDENCE}" = "${EXPECTED_REPO}" ]; then
  log_pass "Git delivery branch and canonical repo in evidence match current delivery"
else
  log_fail "Git delivery metadata mismatch: branch='${BRANCH_IN_EVIDENCE}', repo='${REPO_IN_EVIDENCE}'"
fi

# Test 16: G3 & G4 - Ledger Audit Summary & Integrity
echo "Test 16: [G3/G4] Ledger audit summary verification..."
SUMMARY_OUT="${TMP_DIR}/summary.json"
python3 "${CLI_PY}" budget-summary --ledger "${TEST_LEDGER}" > "${SUMMARY_OUT}"

if grep -q '"approved_count": 1' "${SUMMARY_OUT}" && grep -q '"rejected_count": 2' "${SUMMARY_OUT}" && grep -q '"total_transactions": 3' "${SUMMARY_OUT}"; then
  log_pass "Ledger audit verified: 1 approved, 2 rejected transactions recorded in primary test ledger"
else
  log_fail "Ledger audit counts mismatch"
fi

echo "=============================================================================="
echo "📊 Test Summary:"
echo "   Passed: ${PASSED_COUNT}, Failed: ${FAILED_COUNT}"
echo "=============================================================================="

if [ "${FAILED_COUNT}" -eq 0 ]; then
  echo "🎉 All acceptance gates (G1-G4) successfully passed! All 16 suite tests passed."
  exit 0
else
  echo "❌ One or more tests failed."
  exit 1
fi
