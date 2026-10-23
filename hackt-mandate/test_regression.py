#!/usr/bin/env python3
"""
Comprehensive Regression Test Suite for Flash Settlement Protocol
MIN-147 Rework: G1-G4 Edge Cases & Security Hardening
"""

import os
import sys
import json
import math
import time
import uuid
import tempfile
import unittest
import subprocess

# Ensure we import local flash_settlement
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from flash_settlement import (
    BudgetGuard,
    create_challenge,
    sign_payment,
    settle_payment,
    issue_badge,
    verify_badge,
    validate_amount,
    compute_hmac,
    verify_hmac,
    DEFAULT_PER_TASK_CAP,
    DEFAULT_WEEKLY_CAP,
    CRYPTO_SCHEME,
)


class TestChallengeBindingRegression(unittest.TestCase):
    """
    G1 Regression: Payment must be strictly bound to challenge.
    Every signed payment field (challenge_id, deal_id, payer, recipient,
    amount, currency, nonce) must match the challenge.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = os.path.join(self.tmp_dir.name, "test_ledger.json")
        self.guard = BudgetGuard(ledger_path=self.ledger_path)
        self.payer_key = "test-wallet-sim-key-1234"
        self.facilitator_key = "test-fac-key-5678"
        self.issuer_key = "test-issuer-key-9012"

        self.valid_challenge = create_challenge(
            deal_id="deal-bind-001",
            payer="did:key:z6Mkpayer001",
            recipient="did:key:z6Mkrecipient001",
            amount=50.0,
            currency="GRACE",
            ttl_sec=3600,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _sign(self, challenge, payer_id=None):
        return sign_payment(challenge, payer_key=self.payer_key, payer_id=payer_id)

    def test_challenge_binding_deal_id_mismatch(self):
        payment = self._sign(self.valid_challenge)
        tampered_ch = dict(self.valid_challenge)
        tampered_ch["deal_id"] = "deal-malicious-diverted"

        with self.assertRaises(ValueError) as ctx:
            settle_payment(payment, tampered_ch, self.payer_key, self.facilitator_key, self.guard)
        self.assertIn("deal_id does not match challenge", str(ctx.exception))

    def test_challenge_binding_payer_mismatch(self):
        payment = self._sign(self.valid_challenge)
        tampered_ch = dict(self.valid_challenge)
        tampered_ch["payer"] = "did:key:z6Mkimpersonator"

        with self.assertRaises(ValueError) as ctx:
            settle_payment(payment, tampered_ch, self.payer_key, self.facilitator_key, self.guard)
        self.assertIn("payer does not match challenge", str(ctx.exception))

    def test_challenge_binding_recipient_mismatch(self):
        payment = self._sign(self.valid_challenge)
        tampered_ch = dict(self.valid_challenge)
        tampered_ch["recipient"] = "did:key:z6Mkthief999"

        with self.assertRaises(ValueError) as ctx:
            settle_payment(payment, tampered_ch, self.payer_key, self.facilitator_key, self.guard)
        self.assertIn("recipient does not match challenge", str(ctx.exception))

    def test_challenge_binding_amount_mismatch(self):
        payment = self._sign(self.valid_challenge)
        tampered_ch = dict(self.valid_challenge)
        tampered_ch["amount"] = 1.0  # Challenge says 1, but payment authorized 50

        with self.assertRaises(ValueError) as ctx:
            settle_payment(payment, tampered_ch, self.payer_key, self.facilitator_key, self.guard)
        self.assertIn("amount does not match challenge", str(ctx.exception))

    def test_challenge_binding_currency_mismatch(self):
        payment = self._sign(self.valid_challenge)
        tampered_ch = dict(self.valid_challenge)
        tampered_ch["currency"] = "USDC"  # Currency substitution attack

        with self.assertRaises(ValueError) as ctx:
            settle_payment(payment, tampered_ch, self.payer_key, self.facilitator_key, self.guard)
        self.assertIn("currency does not match challenge", str(ctx.exception))

    def test_challenge_binding_nonce_mismatch(self):
        payment = self._sign(self.valid_challenge)
        tampered_ch = dict(self.valid_challenge)
        tampered_ch["nonce"] = uuid.uuid4().hex  # Nonce desync / replay

        with self.assertRaises(ValueError) as ctx:
            settle_payment(payment, tampered_ch, self.payer_key, self.facilitator_key, self.guard)
        self.assertIn("nonce does not match challenge", str(ctx.exception))

    def test_challenge_binding_challenge_id_mismatch(self):
        payment = self._sign(self.valid_challenge)
        tampered_ch = dict(self.valid_challenge)
        tampered_ch["challenge_id"] = "x402-ch-different-id"

        with self.assertRaises(ValueError) as ctx:
            settle_payment(payment, tampered_ch, self.payer_key, self.facilitator_key, self.guard)
        self.assertIn("challenge_id does not match challenge", str(ctx.exception))

    def test_adversarial_tampered_signed_payment_fields(self):
        """
        Tests when an attacker tampers with the payment body after signing,
        or re-signs with tampered fields: settlement MUST fail!
        """
        # Case A: Attacker re-signs with modified recipient
        tampered_payment_body = {
            "payment_id": f"x402-pay-{uuid.uuid4().hex[:16]}",
            "challenge_id": self.valid_challenge["challenge_id"],
            "deal_id": self.valid_challenge["deal_id"],
            "payer": self.valid_challenge["payer"],
            "recipient": "did:key:z6Mkdiverted-recipient",
            "amount": self.valid_challenge["amount"],
            "currency": self.valid_challenge["currency"],
            "nonce": self.valid_challenge["nonce"],
            "timestamp": int(time.time()),
            "simulation": True,
            "crypto_scheme": CRYPTO_SCHEME,
        }
        tampered_payment_body["signature"] = compute_hmac(self.payer_key, tampered_payment_body)

        with self.assertRaises(ValueError) as ctx:
            settle_payment(tampered_payment_body, self.valid_challenge, self.payer_key, self.facilitator_key, self.guard)
        self.assertIn("recipient does not match challenge", str(ctx.exception))


class TestAmountValidationRegression(unittest.TestCase):
    """
    G3 Regression: Strict finite positive amount validation.
    Amounts must satisfy math.isfinite(amount) and amount > 0.
    Negative, zero, NaN, and +/- Infinity MUST be rejected.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = os.path.join(self.tmp_dir.name, "amount_test_ledger.json")
        self.guard = BudgetGuard(ledger_path=self.ledger_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_validate_amount_rejects_non_finite_and_non_positive(self):
        invalid_amounts = [
            -1000.0,
            -1000,
            -0.0001,
            -1e-9,
            0,
            0.0,
            float("nan"),
            float("inf"),
            float("-inf"),
            "not-a-number",
            None,
        ]
        for val in invalid_amounts:
            with self.subTest(val=val):
                with self.assertRaises(ValueError):
                    validate_amount(val)

    def test_validate_amount_accepts_valid_positive_finite(self):
        valid_amounts = [0.01, 1.0, 50.0, 100.0, 499.99, 100000.0]
        for val in valid_amounts:
            with self.subTest(val=val):
                res = validate_amount(val)
                self.assertAlmostEqual(res, float(val))
                self.assertTrue(math.isfinite(res) and res > 0)

    def test_budget_guard_evaluate_rejects_negative_amount(self):
        allowed, reason, info = self.guard.evaluate_request(-1000.0, "GRACE", "deal-neg-1")
        self.assertFalse(allowed)
        self.assertEqual(reason, "INVALID_AMOUNT")
        self.assertIn("error", info)

    def test_budget_guard_evaluate_rejects_zero_amount(self):
        allowed, reason, info = self.guard.evaluate_request(0.0, "GRACE", "deal-zero-1")
        self.assertFalse(allowed)
        self.assertEqual(reason, "INVALID_AMOUNT")

    def test_budget_guard_evaluate_rejects_nan_amount(self):
        allowed, reason, info = self.guard.evaluate_request(float("nan"), "GRACE", "deal-nan-1")
        self.assertFalse(allowed)
        self.assertEqual(reason, "INVALID_AMOUNT")

    def test_budget_guard_evaluate_rejects_infinity_amount(self):
        allowed, reason, info = self.guard.evaluate_request(float("inf"), "GRACE", "deal-inf-1")
        self.assertFalse(allowed)
        self.assertEqual(reason, "INVALID_AMOUNT")

    def test_negative_amount_cannot_reduce_weekly_spent(self):
        # First add a valid approved transaction
        self.guard.record_entry(
            deal_id="deal-seed-1",
            amount=50.0,
            currency="GRACE",
            recipient="did:key:seed",
            status="APPROVED",
            reason="SEED",
        )
        self.assertAlmostEqual(self.guard.get_weekly_spent(), 50.0)

        # Attempting to record an invalid approved entry should raise ValueError
        with self.assertRaises(ValueError):
            self.guard.record_entry(
                deal_id="deal-neg-bypass",
                amount=-1000.0,
                currency="GRACE",
                recipient="did:key:attacker",
                status="APPROVED",
                reason="MALICIOUS",
            )

        # Weekly spent must remain exactly 50.0
        self.assertAlmostEqual(self.guard.get_weekly_spent(), 50.0)

    def test_create_challenge_rejects_negative_and_zero_amount(self):
        for bad_amt in (-1000.0, 0.0, float("nan"), float("inf")):
            with self.subTest(bad_amt=bad_amt):
                with self.assertRaises(ValueError):
                    create_challenge(
                        deal_id="deal-bad-amt",
                        payer="did:key:payer",
                        recipient="did:key:recip",
                        amount=bad_amt,
                    )


class TestWeeklyCapExceededRegression(unittest.TestCase):
    """
    G3 Regression: Full sequential rolling weekly cap exhaustion and rejection.
    Ensures that a sequence of transactions each within per-task cap
    successfully exhausts the weekly cap, and subsequent transactions fail
    with WEEKLY_CAP_EXCEEDED and exit code / status.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = os.path.join(self.tmp_dir.name, "weekly_cap_ledger.json")
        # Per task cap = 100.0, Weekly cap = 300.0 for deterministic test
        self.guard = BudgetGuard(
            ledger_path=self.ledger_path,
            per_task_cap=100.0,
            weekly_cap=300.0,
        )
        self.payer_key = "wallet-sim-key-wc"
        self.facilitator_key = "fac-sim-key-wc"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_sequential_weekly_cap_exhaustion_and_rejection(self):
        # 1. First transaction: 100.0 GRACE (passes per-task cap 100, weekly total 100 <= 300)
        ch1 = create_challenge("deal-wc-1", "did:key:p", "did:key:r", 100.0)
        pay1 = sign_payment(ch1, self.payer_key)
        res1 = settle_payment(pay1, ch1, self.payer_key, self.facilitator_key, self.guard)
        self.assertEqual(res1.get("status"), "SETTLED")
        self.assertAlmostEqual(self.guard.get_weekly_spent(), 100.0)

        # 2. Second transaction: 100.0 GRACE (weekly total 200 <= 300)
        ch2 = create_challenge("deal-wc-2", "did:key:p", "did:key:r", 100.0)
        pay2 = sign_payment(ch2, self.payer_key)
        res2 = settle_payment(pay2, ch2, self.payer_key, self.facilitator_key, self.guard)
        self.assertEqual(res2.get("status"), "SETTLED")
        self.assertAlmostEqual(self.guard.get_weekly_spent(), 200.0)

        # 3. Third transaction: 80.0 GRACE (weekly total 280 <= 300)
        ch3 = create_challenge("deal-wc-3", "did:key:p", "did:key:r", 80.0)
        pay3 = sign_payment(ch3, self.payer_key)
        res3 = settle_payment(pay3, ch3, self.payer_key, self.facilitator_key, self.guard)
        self.assertEqual(res3.get("status"), "SETTLED")
        self.assertAlmostEqual(self.guard.get_weekly_spent(), 280.0)

        # 4. Fourth transaction: 40.0 GRACE (within per-task cap 100, but 280 + 40 = 320 > 300)
        # MUST BE REJECTED with WEEKLY_CAP_EXCEEDED!
        ch4 = create_challenge("deal-wc-4", "did:key:p", "did:key:r", 40.0)
        pay4 = sign_payment(ch4, self.payer_key)
        res4 = settle_payment(pay4, ch4, self.payer_key, self.facilitator_key, self.guard)

        self.assertEqual(res4.get("status"), "REJECTED_BUDGET_GUARD")
        self.assertEqual(res4.get("reason"), "WEEKLY_CAP_EXCEEDED")
        self.assertFalse(res4.get("allowed"))

        # Guard info details check
        guard_info = res4.get("guard_info", {})
        self.assertAlmostEqual(guard_info.get("weekly_spent"), 280.0)
        self.assertAlmostEqual(guard_info.get("weekly_cap"), 300.0)
        self.assertAlmostEqual(guard_info.get("projected_total"), 320.0)

        # Verify weekly spent was NOT incremented
        self.assertAlmostEqual(self.guard.get_weekly_spent(), 280.0)

        # Verify ledger audit summary
        summary = self.guard.summary()
        self.assertEqual(summary["total_transactions"], 4)
        self.assertEqual(summary["approved_count"], 3)
        self.assertEqual(summary["rejected_count"], 1)
        self.assertAlmostEqual(summary["total_settled_amount"], 280.0)


class TestGitCoordinatesRegression(unittest.TestCase):
    """
    G4 Regression: Delivery metadata and git coordinates conformance.
    Ensures evidence/EVIDENCE.json correctly references current branch,
    canonical repo URL, and that checksum manifest is intact.
    """

    def setUp(self):
        self.repo_root = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
        self.evidence_path = os.path.join(self.repo_root, "evidence", "EVIDENCE.json")
        self.manifest_path = os.path.join(self.repo_root, "evidence", "MANIFEST.sha256")

    def test_evidence_file_exists_and_is_valid_json(self):
        self.assertTrue(os.path.exists(self.evidence_path), f"Missing {self.evidence_path}")
        with open(self.evidence_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("ticket"), "MIN-147")
        self.assertTrue(data.get("simulation"))

    def test_git_branch_matches_delivery(self):
        with open(self.evidence_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        declared_branch = data.get("branch") or data.get("git", {}).get("branch")
        expected_branch = "minutta/min-147-min-116-build-flash-settlement-x402-micropayment-badge"
        self.assertEqual(declared_branch, expected_branch)

    def test_git_repo_url_matches_canonical(self):
        with open(self.evidence_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        declared_repo = data.get("repo_url") or data.get("git", {}).get("repo_url")
        self.assertEqual(declared_repo, "https://github.com/pashamango/agent-mandates.git")


if __name__ == "__main__":
    unittest.main(verbosity=2)
