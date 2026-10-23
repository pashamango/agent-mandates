#!/usr/bin/env python3
"""
Flash Settlement Protocol: x402 Micropayment + Reputation Badge + Budget Guard
=============================================================================

[SIMULATION NOTICE]
This prototype operates strictly in TESTNET SIMULATION mode using HMAC-SHA256
cryptographic proofs. No real funds, real blockchain network calls, or production
wallet keys are used or accepted.

Components:
1. x402 Micropayment Flow: HTTP 402 Challenge -> Wallet Signature -> Facilitator Settlement.
2. GRACE Currency Integration: Primary settlement denomination in GRACE tokens.
3. Reputation Badge Engine: Cryptographically signed, independently verifiable matchmaker badges.
4. Codex Budget Guard: Mandatory per-task cap, rolling weekly cap, and emergency kill-switch with ledger.
"""

import os
import sys
import json
import time
import math
import uuid
import hmac
import hashlib
import argparse
from typing import Dict, Any, Tuple, Optional

# --- PROTOCOL CONSTANTS ---
SIMULATION_NOTICE = (
    "[SIMULATION NOTICE] Flash Settlement Prototype running in TESTNET SIMULATION mode "
    "(HMAC-SHA256). No real funds or real private keys used."
)
CRYPTO_SCHEME = "TESTNET_SIMULATED_HMAC_SHA256"
FACILITATOR_URN = "urn:mindrock:testnet:simulated-facilitator"
DEFAULT_ISSUER_URN = "urn:mindrock:authority:hack-temple"
DEFAULT_CURRENCY = "GRACE"
DEFAULT_PER_TASK_CAP = 100.0
DEFAULT_WEEKLY_CAP = 500.0
ROLLING_WINDOW_SECONDS = 7 * 24 * 3600  # 7 days


def canonical_bytes(data: Dict[str, Any]) -> bytes:
    """Produces deterministic canonical UTF-8 bytes for cryptographic signing."""
    return json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')


def validate_amount(amount: Any) -> float:
    """Validates that amount is a finite, positive number (> 0). Raises ValueError otherwise."""
    try:
        val = float(amount)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid amount '{amount}': must be numeric")
    if not (math.isfinite(val) and val > 0):
        raise ValueError(f"Invalid amount {val}: must be a finite positive number (> 0)")
    return val


def compute_hmac(key: str, data: Dict[str, Any]) -> str:
    """Computes HMAC-SHA256 over canonical JSON bytes using the provided key."""
    return hmac.new(key.encode('utf-8'), canonical_bytes(data), hashlib.sha256).hexdigest()


def verify_hmac(key: str, data: Dict[str, Any], signature: str) -> bool:
    """Verifies HMAC-SHA256 in constant time."""
    expected = compute_hmac(key, data)
    return hmac.compare_digest(expected, signature)


# --- BUDGET GUARD ENGINE ---
class BudgetGuard:
    """
    Mandatory Codex Budget Guard:
    Enforces per-task spending caps, weekly rolling expenditure limits,
    emergency kill-switch, and audit ledger persistence.
    """

    def __init__(
        self,
        ledger_path: str,
        per_task_cap: float = DEFAULT_PER_TASK_CAP,
        weekly_cap: float = DEFAULT_WEEKLY_CAP,
    ):
        self.ledger_path = ledger_path
        self.per_task_cap = float(per_task_cap)
        self.weekly_cap = float(weekly_cap)
        self._ensure_ledger()

    def _ensure_ledger(self) -> None:
        ledger_dir = os.path.dirname(os.path.abspath(self.ledger_path))
        if ledger_dir and not os.path.exists(ledger_dir):
            os.makedirs(ledger_dir, exist_ok=True)
        if not os.path.exists(self.ledger_path):
            with open(self.ledger_path, 'w', encoding='utf-8') as f:
                json.dump({"kill_switch": False, "transactions": []}, f, indent=2)

    def _load_ledger(self) -> Dict[str, Any]:
        try:
            with open(self.ledger_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"kill_switch": False, "transactions": []}

    def _save_ledger(self, data: Dict[str, Any]) -> None:
        with open(self.ledger_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def is_kill_switch_active(self) -> bool:
        data = self._load_ledger()
        return bool(data.get("kill_switch", False))

    def set_kill_switch(self, state: bool) -> None:
        data = self._load_ledger()
        data["kill_switch"] = bool(state)
        self._save_ledger(data)

    def get_weekly_spent(self, current_time: Optional[float] = None) -> float:
        if current_time is None:
            current_time = time.time()
        window_start = current_time - ROLLING_WINDOW_SECONDS
        data = self._load_ledger()
        total = 0.0
        for tx in data.get("transactions", []):
            if tx.get("status") == "APPROVED":
                tx_time = tx.get("timestamp", 0)
                if tx_time >= window_start:
                    try:
                        amt = float(tx.get("amount", 0.0))
                        if math.isfinite(amt) and amt > 0:
                            total += amt
                    except (TypeError, ValueError):
                        pass
        return total

    def evaluate_request(
        self, amount: float, currency: str, deal_id: str
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Evaluates whether an outgoing payment passes all budget guards."""
        try:
            val = validate_amount(amount)
        except ValueError as e:
            return False, "INVALID_AMOUNT", {
                "allowed": False,
                "reason": "INVALID_AMOUNT",
                "error": str(e),
                "amount": amount,
                "currency": currency,
                "simulation": True,
            }

        if self.is_kill_switch_active():
            return False, "KILL_SWITCH_ACTIVE", {
                "allowed": False,
                "reason": "KILL_SWITCH_ACTIVE",
                "kill_switch": True,
                "amount": val,
                "currency": currency,
                "simulation": True,
            }

        if val > self.per_task_cap:
            return False, "PER_TASK_CAP_EXCEEDED", {
                "allowed": False,
                "reason": "PER_TASK_CAP_EXCEEDED",
                "amount": val,
                "per_task_cap": self.per_task_cap,
                "currency": currency,
                "simulation": True,
            }

        weekly_spent = self.get_weekly_spent()
        if weekly_spent + val > self.weekly_cap:
            return False, "WEEKLY_CAP_EXCEEDED", {
                "allowed": False,
                "reason": "WEEKLY_CAP_EXCEEDED",
                "amount": val,
                "weekly_spent": weekly_spent,
                "weekly_cap": self.weekly_cap,
                "projected_total": weekly_spent + val,
                "currency": currency,
                "simulation": True,
            }

        return True, "APPROVED", {
            "allowed": True,
            "reason": "WITHIN_LIMITS",
            "amount": val,
            "per_task_cap": self.per_task_cap,
            "weekly_spent": weekly_spent,
            "weekly_cap": self.weekly_cap,
            "remaining_weekly": self.weekly_cap - (weekly_spent + val),
            "currency": currency,
            "simulation": True,
        }

    def record_entry(
        self,
        deal_id: str,
        amount: float,
        currency: str,
        recipient: str,
        status: str,
        reason: str,
        tx_hash: Optional[str] = None,
    ) -> Dict[str, Any]:
        data = self._load_ledger()
        if status == "APPROVED":
            val = validate_amount(amount)
        else:
            try:
                val = float(amount)
                if not math.isfinite(val):
                    val = 0.0
            except (TypeError, ValueError):
                val = 0.0

        entry = {
            "entry_id": f"ledg-{uuid.uuid4().hex[:12]}",
            "timestamp": int(time.time()),
            "deal_id": deal_id,
            "amount": val,
            "currency": currency,
            "recipient": recipient,
            "status": status,
            "reason": reason,
            "tx_hash": tx_hash or "none",
            "simulation": True,
        }
        data.setdefault("transactions", []).append(entry)
        self._save_ledger(data)
        return entry

    def summary(self) -> Dict[str, Any]:
        data = self._load_ledger()
        txs = data.get("transactions", [])
        approved = [t for t in txs if t.get("status") == "APPROVED"]
        rejected = [t for t in txs if t.get("status") == "REJECTED_BUDGET_GUARD"]
        return {
            "simulation": True,
            "kill_switch_active": self.is_kill_switch_active(),
            "per_task_cap": self.per_task_cap,
            "weekly_cap": self.weekly_cap,
            "weekly_spent": self.get_weekly_spent(),
            "total_transactions": len(txs),
            "approved_count": len(approved),
            "rejected_count": len(rejected),
            "total_settled_amount": sum(float(t.get("amount", 0.0)) for t in approved),
        }


# --- X402 CHALLENGE & PAYMENT ---
def create_challenge(
    deal_id: str,
    payer: str,
    recipient: str,
    amount: float,
    currency: str = DEFAULT_CURRENCY,
    ttl_sec: int = 3600,
    facilitator: str = FACILITATOR_URN,
) -> Dict[str, Any]:
    """Generates an HTTP 402 Payment Required challenge payload."""
    val = validate_amount(amount)
    now = int(time.time())
    challenge = {
        "status_code": 402,
        "status_message": "Payment Required",
        "challenge_id": f"x402-ch-{uuid.uuid4().hex[:16]}",
        "deal_id": deal_id,
        "payer": payer,
        "recipient": recipient,
        "amount": val,
        "currency": currency,
        "nonce": uuid.uuid4().hex,
        "issued_at": now,
        "expires_at": now + ttl_sec,
        "facilitator": facilitator,
        "simulation": True,
        "crypto_scheme": CRYPTO_SCHEME,
        "simulation_notice": SIMULATION_NOTICE,
    }
    return challenge


def sign_payment(
    challenge: Dict[str, Any],
    payer_key: str,
    payer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Client/Payer signs payment authorization in response to a 402 challenge."""
    now = int(time.time())
    if now > challenge.get("expires_at", 0):
        raise ValueError("Challenge has expired")

    val = validate_amount(challenge["amount"])

    payment_body = {
        "payment_id": f"x402-pay-{uuid.uuid4().hex[:16]}",
        "challenge_id": challenge["challenge_id"],
        "deal_id": challenge["deal_id"],
        "payer": payer_id or challenge["payer"],
        "recipient": challenge["recipient"],
        "amount": val,
        "currency": challenge["currency"],
        "nonce": challenge["nonce"],
        "timestamp": now,
        "simulation": True,
        "crypto_scheme": CRYPTO_SCHEME,
    }
    signature = compute_hmac(payer_key, payment_body)
    payment_body["signature"] = signature
    return payment_body


def settle_payment(
    payment: Dict[str, Any],
    challenge: Dict[str, Any],
    payer_key: str,
    facilitator_key: str,
    budget_guard: BudgetGuard,
    issuer_key: Optional[str] = None,
    badge_score: int = 100,
    badge_tier: str = "GOLD",
) -> Dict[str, Any]:
    """
    Facilitator validates 402 payment, applies budget guard,
    persists ledger record, and issues settlement confirmation and reputation badge.
    """
    now = int(time.time())

    # 1. Challenge validation & cross-binding checks (G1)
    if challenge.get("challenge_id") != payment.get("challenge_id"):
        raise ValueError("Payment challenge_id does not match challenge")
    if challenge.get("deal_id") != payment.get("deal_id"):
        raise ValueError("Payment deal_id does not match challenge")
    if challenge.get("payer") != payment.get("payer"):
        raise ValueError("Payment payer does not match challenge")
    if challenge.get("recipient") != payment.get("recipient"):
        raise ValueError("Payment recipient does not match challenge")
    if challenge.get("currency") != payment.get("currency"):
        raise ValueError("Payment currency does not match challenge")
    if challenge.get("nonce") != payment.get("nonce"):
        raise ValueError("Payment nonce does not match challenge")

    try:
        ch_amount = validate_amount(challenge.get("amount"))
    except ValueError as e:
        raise ValueError(f"Invalid challenge amount: {e}")

    try:
        pay_amount = validate_amount(payment.get("amount"))
    except ValueError as e:
        raise ValueError(f"Invalid payment amount: {e}")

    if ch_amount != pay_amount:
        raise ValueError(f"Payment amount does not match challenge: payment={pay_amount}, challenge={ch_amount}")

    if now > challenge.get("expires_at", 0):
        raise ValueError("Challenge has expired")

    # 2. Signature verification
    sig = payment.get("signature")
    if not sig:
        raise ValueError("Payment signature missing")
    body_for_verify = {k: v for k, v in payment.items() if k != "signature"}
    if not verify_hmac(payer_key, body_for_verify, sig):
        raise ValueError("Invalid payer wallet signature")

    # 3. Budget guard evaluation (G3)
    amount = pay_amount
    currency = payment["currency"]
    deal_id = payment["deal_id"]
    recipient = payment["recipient"]

    allowed, reason, guard_info = budget_guard.evaluate_request(amount, currency, deal_id)
    if not allowed:
        budget_guard.record_entry(
            deal_id=deal_id,
            amount=amount,
            currency=currency,
            recipient=recipient,
            status="REJECTED_BUDGET_GUARD",
            reason=reason,
        )
        return {
            "status": "REJECTED_BUDGET_GUARD",
            "allowed": False,
            "reason": reason,
            "guard_info": guard_info,
            "simulation": True,
            "crypto_scheme": CRYPTO_SCHEME,
        }

    # 4. Generate settlement receipt
    settlement_id = f"stl-{uuid.uuid4().hex[:16]}"
    tx_hash = hashlib.sha256(
        f"{settlement_id}:{deal_id}:{amount}:{currency}:{now}".encode('utf-8')
    ).hexdigest()

    receipt_body = {
        "settlement_id": settlement_id,
        "deal_id": deal_id,
        "payment_id": payment["payment_id"],
        "challenge_id": challenge["challenge_id"],
        "status": "SETTLED",
        "amount": amount,
        "currency": currency,
        "payer": payment["payer"],
        "recipient": recipient,
        "tx_hash": tx_hash,
        "settled_at": now,
        "facilitator": FACILITATOR_URN,
        "simulation": True,
        "crypto_scheme": CRYPTO_SCHEME,
    }
    receipt_sig = compute_hmac(facilitator_key, receipt_body)
    receipt_body["facilitator_signature"] = receipt_sig

    # 5. Record approved transaction in ledger
    budget_guard.record_entry(
        deal_id=deal_id,
        amount=amount,
        currency=currency,
        recipient=recipient,
        status="APPROVED",
        reason="SETTLEMENT_SUCCESSFUL",
        tx_hash=tx_hash,
    )

    # 6. Issue reputation badge if issuer key provided
    badge = None
    if issuer_key:
        badge = issue_badge(
            deal_id=deal_id,
            recipient=recipient,
            settlement_id=settlement_id,
            amount=amount,
            currency=currency,
            score=badge_score,
            tier=badge_tier,
            issuer_key=issuer_key,
        )
        receipt_body["badge"] = badge

    return receipt_body


# --- REPUTATION BADGE ENGINE ---
def issue_badge(
    deal_id: str,
    recipient: str,
    settlement_id: str,
    amount: float,
    currency: str = DEFAULT_CURRENCY,
    score: int = 100,
    tier: str = "GOLD",
    issuer: str = DEFAULT_ISSUER_URN,
    issuer_key: str = "hack-temple-authority-secret-2026",
    ttl_days: int = 30,
) -> Dict[str, Any]:
    """Issues a cryptographically signed matchmaker reputation badge."""
    val = validate_amount(amount)
    now = int(time.time())
    expires_at = now + (ttl_days * 86400)
    badge_body = {
        "badge_version": "1.0.0",
        "badge_id": f"badge-{uuid.uuid4().hex[:16]}",
        "deal_id": deal_id,
        "settlement_id": settlement_id,
        "recipient": recipient,
        "badge_type": "FLASH_SETTLEMENT_MATCHMAKER",
        "tier": tier,
        "reputation_score": int(score),
        "settlement_amount": val,
        "currency": currency,
        "issued_at": now,
        "expires_at": expires_at,
        "issuer": issuer,
        "issuer_key_id": "key-hack-temple-testnet-01",
        "simulation": True,
        "crypto_scheme": CRYPTO_SCHEME,
        "simulation_notice": SIMULATION_NOTICE,
    }
    signature = compute_hmac(issuer_key, badge_body)
    badge_body["signature"] = signature
    return badge_body


def verify_badge(
    badge: Dict[str, Any],
    issuer_key: str,
    current_time: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Independently verifies matchmaker badge authenticity, integrity,
    cryptographic signature, simulation markers, and expiration.
    """
    if current_time is None:
        current_time = time.time()

    # Verify simulation markings
    if not badge.get("simulation") or badge.get("crypto_scheme") != CRYPTO_SCHEME:
        return {
            "verified": False,
            "status": "INVALID",
            "error": "INVALID_SIMULATION_SCHEME",
            "simulation": badge.get("simulation", False),
        }

    # Verify expiration
    expires_at = badge.get("expires_at", 0)
    if current_time > expires_at:
        return {
            "verified": False,
            "status": "EXPIRED",
            "error": "BADGE_EXPIRED",
            "expired_by_seconds": int(current_time - expires_at),
            "simulation": True,
        }

    # Verify signature
    signature = badge.get("signature")
    if not signature:
        return {
            "verified": False,
            "status": "INVALID",
            "error": "BADGE_SIGNATURE_MISSING",
            "simulation": True,
        }

    body_to_verify = {k: v for k, v in badge.items() if k != "signature"}
    if not verify_hmac(issuer_key, body_to_verify, signature):
        return {
            "verified": False,
            "status": "INVALID",
            "error": "BADGE_SIGNATURE_MISMATCH",
            "simulation": True,
        }

    return {
        "verified": True,
        "status": "VALID",
        "badge_id": badge.get("badge_id"),
        "recipient": badge.get("recipient"),
        "tier": badge.get("tier"),
        "reputation_score": badge.get("reputation_score"),
        "currency": badge.get("currency"),
        "expires_at": expires_at,
        "time_until_expiration_seconds": int(expires_at - current_time),
        "simulation": True,
        "crypto_scheme": CRYPTO_SCHEME,
    }


# --- CLI INTERFACE ---
def parse_args():
    parser = argparse.ArgumentParser(
        description="Flash Settlement: x402 Micropayments + Badges + Budget Guard"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. create-challenge
    p_ch = subparsers.add_parser("create-challenge", help="Create an x402 payment challenge")
    p_ch.add_argument("--deal-id", required=True, help="Flash deal identifier")
    p_ch.add_argument("--payer", required=True, help="Payer DID / address")
    p_ch.add_argument("--recipient", required=True, help="Recipient (matchmaker) DID / address")
    p_ch.add_argument("--amount", type=float, required=True, help="Payment amount")
    p_ch.add_argument("--currency", default=DEFAULT_CURRENCY, help="Settlement currency (default: GRACE)")
    p_ch.add_argument("--ttl-sec", type=int, default=3600, help="Challenge TTL seconds")
    p_ch.add_argument("--out", help="Output path for challenge JSON")

    # 2. sign-payment
    p_sp = subparsers.add_parser("sign-payment", help="Sign 402 payment authorization with wallet key")
    p_sp.add_argument("--challenge", required=True, help="Path to challenge JSON file")
    p_sp.add_argument("--payer-key", required=True, help="Payer wallet simulation secret key")
    p_sp.add_argument("--payer-id", help="Override payer identifier")
    p_sp.add_argument("--out", help="Output path for signed payment JSON")

    # 3. settle-payment
    p_set = subparsers.add_parser("settle-payment", help="Settle payment and issue badge")
    p_set.add_argument("--payment", required=True, help="Path to signed payment JSON file")
    p_set.add_argument("--challenge", required=True, help="Path to challenge JSON file")
    p_set.add_argument("--payer-key", required=True, help="Payer wallet simulation secret key")
    p_set.add_argument("--facilitator-key", default="fac-secret-key-sim-2026", help="Facilitator secret key")
    p_set.add_argument("--issuer-key", default="hack-temple-authority-secret-2026", help="Badge issuer secret key")
    p_set.add_argument("--ledger", default="/tmp/flash_settlement_ledger.json", help="Path to budget guard ledger")
    p_set.add_argument("--per-task-cap", type=float, default=DEFAULT_PER_TASK_CAP, help="Per-task spending cap")
    p_set.add_argument("--weekly-cap", type=float, default=DEFAULT_WEEKLY_CAP, help="Weekly spending cap")
    p_set.add_argument("--badge-score", type=int, default=100, help="Reputation badge score")
    p_set.add_argument("--badge-tier", default="GOLD", help="Reputation badge tier")
    p_set.add_argument("--out", help="Output path for settlement receipt JSON")

    # 4. issue-badge
    p_ib = subparsers.add_parser("issue-badge", help="Issue signed reputation badge")
    p_ib.add_argument("--deal-id", required=True, help="Flash deal identifier")
    p_ib.add_argument("--recipient", required=True, help="Recipient matchmaker DID")
    p_ib.add_argument("--settlement-id", required=True, help="Settlement identifier")
    p_ib.add_argument("--amount", type=float, required=True, help="Settlement amount")
    p_ib.add_argument("--currency", default=DEFAULT_CURRENCY, help="Settlement currency")
    p_ib.add_argument("--score", type=int, default=100, help="Reputation score")
    p_ib.add_argument("--tier", default="GOLD", help="Badge tier")
    p_ib.add_argument("--issuer-key", default="hack-temple-authority-secret-2026", help="Issuer secret key")
    p_ib.add_argument("--ttl-days", type=int, default=30, help="Badge validity days")
    p_ib.add_argument("--out", help="Output path for badge JSON")

    # 5. verify-badge
    p_vb = subparsers.add_parser("verify-badge", help="Independently verify badge signature and expiry")
    p_vb.add_argument("--badge", required=True, help="Path to badge JSON file")
    p_vb.add_argument("--issuer-key", default="hack-temple-authority-secret-2026", help="Issuer verification key")

    # 6. budget-check
    p_bc = subparsers.add_parser("budget-check", help="Evaluate budget guard for a proposed transaction")
    p_bc.add_argument("--deal-id", required=True, help="Deal identifier")
    p_bc.add_argument("--amount", type=float, required=True, help="Proposed transaction amount")
    p_bc.add_argument("--currency", default=DEFAULT_CURRENCY, help="Currency")
    p_bc.add_argument("--ledger", default="/tmp/flash_settlement_ledger.json", help="Path to ledger JSON")
    p_bc.add_argument("--per-task-cap", type=float, default=DEFAULT_PER_TASK_CAP, help="Per-task spending cap")
    p_bc.add_argument("--weekly-cap", type=float, default=DEFAULT_WEEKLY_CAP, help="Weekly spending cap")

    # 7. budget-kill-switch
    p_ks = subparsers.add_parser("budget-kill-switch", help="Set budget guard kill-switch state")
    p_ks.add_argument("--ledger", default="/tmp/flash_settlement_ledger.json", help="Path to ledger JSON")
    p_ks.add_argument("--state", choices=["ON", "OFF"], required=True, help="Kill-switch state")

    # 8. budget-summary
    p_bs = subparsers.add_parser("budget-summary", help="Print budget guard ledger summary")
    p_bs.add_argument("--ledger", default="/tmp/flash_settlement_ledger.json", help="Path to ledger JSON")

    return parser.parse_args()


def output_json(data: Dict[str, Any], out_path: Optional[str] = None):
    dumped = json.dumps(data, indent=2)
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(dumped)
    print(dumped)


def main():
    args = parse_args()

    if args.command == "create-challenge":
        try:
            ch = create_challenge(
                deal_id=args.deal_id,
                payer=args.payer,
                recipient=args.recipient,
                amount=args.amount,
                currency=args.currency,
                ttl_sec=args.ttl_sec,
            )
        except ValueError as e:
            err = {
                "status": "INVALID_CHALLENGE",
                "error": str(e),
                "simulation": True,
            }
            output_json(err, args.out)
            sys.exit(1)
        output_json(ch, args.out)

    elif args.command == "sign-payment":
        with open(args.challenge, 'r', encoding='utf-8') as f:
            ch = json.load(f)
        try:
            pay = sign_payment(ch, payer_key=args.payer_key, payer_id=args.payer_id)
        except ValueError as e:
            err = {
                "status": "INVALID_PAYMENT_SIGNING",
                "error": str(e),
                "simulation": True,
            }
            output_json(err, args.out)
            sys.exit(1)
        output_json(pay, args.out)

    elif args.command == "settle-payment":
        with open(args.payment, 'r', encoding='utf-8') as f:
            pay = json.load(f)
        with open(args.challenge, 'r', encoding='utf-8') as f:
            ch = json.load(f)

        guard = BudgetGuard(
            ledger_path=args.ledger,
            per_task_cap=args.per_task_cap,
            weekly_cap=args.weekly_cap,
        )
        try:
            res = settle_payment(
                payment=pay,
                challenge=ch,
                payer_key=args.payer_key,
                facilitator_key=args.facilitator_key,
                budget_guard=guard,
                issuer_key=args.issuer_key,
                badge_score=args.badge_score,
                badge_tier=args.badge_tier,
            )
        except ValueError as e:
            res = {
                "status": "REJECTED_INVALID_PAYMENT",
                "error": str(e),
                "simulation": True,
                "crypto_scheme": CRYPTO_SCHEME,
            }
            output_json(res, args.out)
            sys.exit(1)

        output_json(res, args.out)
        if res.get("status") == "REJECTED_BUDGET_GUARD":
            sys.exit(1)

    elif args.command == "issue-badge":
        try:
            badge = issue_badge(
                deal_id=args.deal_id,
                recipient=args.recipient,
                settlement_id=args.settlement_id,
                amount=args.amount,
                currency=args.currency,
                score=args.score,
                tier=args.tier,
                issuer_key=args.issuer_key,
                ttl_days=args.ttl_days,
            )
        except ValueError as e:
            err = {
                "status": "INVALID_BADGE_REQUEST",
                "error": str(e),
                "simulation": True,
            }
            output_json(err, args.out)
            sys.exit(1)
        output_json(badge, args.out)

    elif args.command == "verify-badge":
        with open(args.badge, 'r', encoding='utf-8') as f:
            badge = json.load(f)
        res = verify_badge(badge, issuer_key=args.issuer_key)
        output_json(res)
        if not res.get("verified", False):
            sys.exit(1)

    elif args.command == "budget-check":
        guard = BudgetGuard(
            ledger_path=args.ledger,
            per_task_cap=args.per_task_cap,
            weekly_cap=args.weekly_cap,
        )
        allowed, reason, info = guard.evaluate_request(args.amount, args.currency, args.deal_id)
        output_json(info)
        if not allowed:
            sys.exit(1)

    elif args.command == "budget-kill-switch":
        guard = BudgetGuard(ledger_path=args.ledger)
        guard.set_kill_switch(args.state == "ON")
        output_json({"kill_switch": args.state == "ON", "simulation": True})

    elif args.command == "budget-summary":
        guard = BudgetGuard(ledger_path=args.ledger)
        output_json(guard.summary())


if __name__ == "__main__":
    main()
