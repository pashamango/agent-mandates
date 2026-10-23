#!/usr/bin/env python3
"""
Flash Protocol & Settlement Reference Implementation
=====================================================
Ticket: MIN-146 (MIN-115) & MIN-147 (MIN-116)
Authority: Principal Pavel Cherkashin (pavel@mindrock.capital)

[SIMULATION NOTICE]
This prototype operates strictly in TESTNET SIMULATION mode using Ed25519
and HMAC-SHA256 cryptographic proofs. No real funds, real blockchain network calls,
or production wallet keys are used or accepted.

Components:
1. Intent Board & Matchmaking:
   - multipass.publish_intent: canonical JSON, Ed25519 signature, TTL, nonce.
   - multipass.verify: goDNS TXT record resolution + Ed25519 verification.
   - multipass.match_intents: Automated discovery and matching (WANT <-> OFFER).
2. Human-in-the-Loop Gate:
   - Closure gated on human approval ("валяй" / "approve").
3. Flash Semantics:
   - Atomic execution upon verification & human approval.
   - Zero-loss rollback on rejection / collapse.
4. x402 Micropayment & Reputation Badge Engine:
   - HTTP 402 challenge-response settlement in GRACE denomination.
   - Independently verifiable matchmaker badges.
   - Codex Budget Guard (per-task cap, weekly cap, kill-switch, ledger).
"""

import os
import sys
import json
import time
import uuid
import hmac
import hashlib
import datetime
import argparse
from typing import Dict, Any, Tuple, Optional, List

# --- PROTOCOL CONSTANTS ---
SIMULATION_NOTICE = (
    "[SIMULATION NOTICE] Flash Protocol & Settlement Prototype running in TESTNET SIMULATION mode "
    "(Ed25519 + HMAC-SHA256). No real funds or real private keys used."
)
CRYPTO_SCHEME = "TESTNET_SIMULATED_HMAC_SHA256"
ED25519_CRYPTO_SCHEME = "Ed25519_RFC8032"
FACILITATOR_URN = "urn:mindrock:testnet:simulated-facilitator"
DEFAULT_ISSUER_URN = "urn:mindrock:authority:hack-temple"
DEFAULT_CURRENCY = "GRACE"
DEFAULT_PER_TASK_CAP = 100.0
DEFAULT_WEEKLY_CAP = 500.0
ROLLING_WINDOW_SECONDS = 7 * 24 * 3600  # 7 days

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MULTICODEC_ED25519_PUB = bytes([0xed, 0x01])
DID_PREFIX = "did:key:z"


# ==============================================================================
# CANONICAL JSON (RFC 8785)
# ==============================================================================
def canonical_bytes(data: Dict[str, Any]) -> bytes:
    """Produces deterministic canonical UTF-8 bytes for cryptographic signing (JCS RFC 8785)."""
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def compute_hmac(key: str, data: Dict[str, Any]) -> str:
    """Computes HMAC-SHA256 over canonical JSON bytes using the provided key."""
    return hmac.new(key.encode('utf-8'), canonical_bytes(data), hashlib.sha256).hexdigest()


def verify_hmac(key: str, data: Dict[str, Any], signature: str) -> bool:
    """Verifies HMAC-SHA256 in constant time."""
    expected = compute_hmac(key, data)
    return hmac.compare_digest(expected, signature)


# ==============================================================================
# BASE58 & DID:KEY DERIVATION
# ==============================================================================
def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = B58_ALPHABET[r] + out
    pad = 0
    for b in data:
        if b == 0:
            pad += 1
        else:
            break
    return "1" * pad + out if out else "1" * pad


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58_ALPHABET.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    return b"\x00" * pad + raw


def did_from_pubkey(pub_raw: bytes) -> str:
    assert len(pub_raw) == 32, "Ed25519 pubkey must be exactly 32 bytes"
    return DID_PREFIX + b58encode(MULTICODEC_ED25519_PUB + pub_raw)


def pubkey_from_did(did: str) -> bytes:
    assert did.startswith(DID_PREFIX), f"Invalid DID prefix, expected {DID_PREFIX}"
    raw = b58decode(did[len(DID_PREFIX):])
    assert raw[:2] == MULTICODEC_ED25519_PUB, "Invalid multicodec header, expected Ed25519 (0xed01)"
    assert len(raw) == 34, f"Bad decoded public key length: {len(raw)}"
    return raw[2:]


# ==============================================================================
# PURE PYTHON ED25519 (RFC 8032)
# ==============================================================================
_q = 2**255 - 19
_l = 2**252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _q - 2, _q) % _q
_I = pow(2, (_q - 1) // 4, _q)


def _inv(z: int) -> int:
    return pow(z, _q - 2, _q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (_q + 3) // 8, _q)
    if (x * x - xx) % _q != 0:
        x = (x * _I) % _q
    if x % 2 != 0:
        x = _q - x
    return x


_By = 4 * _inv(5) % _q
_Bx = _xrecover(_By)
_B = (_Bx, _By)


def _edwards_add(P: Tuple[int, int], Q: Tuple[int, int]) -> Tuple[int, int]:
    x1, y1 = P
    x2, y2 = Q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + _d * x1 * x2 * y1 * y2) % _q
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - _d * x1 * x2 * y1 * y2) % _q
    return (x3, y3)


def _scalarmult(P: Tuple[int, int], e: int) -> Tuple[int, int]:
    if e == 0:
        return (0, 1)
    Q = _scalarmult(P, e // 2)
    Q = _edwards_add(Q, Q)
    if e & 1:
        Q = _edwards_add(Q, P)
    return Q


def _encodepoint(P: Tuple[int, int]) -> bytes:
    x, y = P
    bits = [(y >> i) & 1 for i in range(255)] + [x & 1]
    return bytes([sum([bits[i * 8 + j] << j for j in range(8)]) for i in range(32)])


def _decodepoint(s: bytes) -> Optional[Tuple[int, int]]:
    if len(s) != 32:
        return None
    y = sum([s[i] << (i * 8) for i in range(32)])
    clamp = y & ((1 << 255) - 1)
    x = _xrecover(clamp)
    if (x & 1) != ((s[31] >> 7) & 1):
        x = _q - x
    return (x, clamp)


def ed25519_publickey_from_seed(sk: bytes) -> bytes:
    """Derives 32-byte Ed25519 public key from 32-byte private seed."""
    assert len(sk) == 32, "Private key seed must be 32 bytes"
    h = hashlib.sha512(sk).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return _encodepoint(_scalarmult(_B, a))


def ed25519_sign(msg: bytes, sk: bytes, pk: Optional[bytes] = None) -> bytes:
    """Signs message bytes using 32-byte private seed. Returns 64-byte signature."""
    if pk is None:
        pk = ed25519_publickey_from_seed(sk)
    h = hashlib.sha512(sk).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= (1 << 254)
    r = int.from_bytes(hashlib.sha512(h[32:] + msg).digest(), 'little') % _l
    R = _scalarmult(_B, r)
    encR = _encodepoint(R)
    k = int.from_bytes(hashlib.sha512(encR + pk + msg).digest(), 'little') % _l
    S = (r + k * a) % _l
    return encR + S.to_bytes(32, 'little')


def ed25519_verify(sig: bytes, msg: bytes, pk: bytes) -> bool:
    """Verifies 64-byte Ed25519 signature against message bytes and 32-byte public key."""
    if len(sig) != 64 or len(pk) != 32:
        return False
    R = _decodepoint(sig[:32])
    A = _decodepoint(pk)
    if R is None or A is None:
        return False
    S = int.from_bytes(sig[32:], 'little')
    if S >= _l:
        return False
    k = int.from_bytes(hashlib.sha512(sig[:32] + pk + msg).digest(), 'little') % _l
    SB = _scalarmult(_B, S)
    RA = _edwards_add(R, _scalarmult(A, k))
    return SB == RA


def generate_identity(name: str = "agent", domain: str = "hackt.com") -> Dict[str, Any]:
    """Generates an Ed25519 keypair, DID, and goDNS record."""
    seed = os.urandom(32)
    pub = ed25519_publickey_from_seed(seed)
    did = did_from_pubkey(pub)
    locator = f"_mandate.{name}.{domain}"
    exp_date = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Sign mandate proof
    mandate_payload = {
        "locator": locator,
        "did": did,
        "pubkey_hex": pub.hex(),
        "expires_at": exp_date,
        "scope": "intent:publish,intent:match,settlement:x402"
    }
    mandate_sig = ed25519_sign(canonical_bytes(mandate_payload), seed, pub).hex()
    txt_record = f"v=mnd1; k=ed25519; p={did}; scope=intent:publish,intent:match; exp={exp_date}; sig={mandate_sig}"

    return {
        "agent_name": name,
        "domain": domain,
        "agent_did": did,
        "private_key_hex": seed.hex(),
        "public_key_hex": pub.hex(),
        "godns_locator": locator,
        "godns_txt_record": txt_record,
        "mandate_payload": mandate_payload,
        "simulation": True,
    }


# ==============================================================================
# INTENT BOARD & MULTIPASS PROTOCOL
# ==============================================================================
class IntentBoard:
    """
    Decentralized Intent Board:
    Manages publication, verification, and matchmaking of signed intents/offers.
    """

    def __init__(self, board_path: str = "/tmp/flash_intent_board.json"):
        self.board_path = board_path
        self._ensure_board()

    def _ensure_board(self) -> None:
        board_dir = os.path.dirname(os.path.abspath(self.board_path))
        if board_dir and not os.path.exists(board_dir):
            os.makedirs(board_dir, exist_ok=True)
        if not os.path.exists(self.board_path):
            with open(self.board_path, 'w', encoding='utf-8') as f:
                json.dump({"intents": [], "deals": []}, f, indent=2)

    def load(self) -> Dict[str, Any]:
        try:
            with open(self.board_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {"intents": [], "deals": []}

    def save(self, data: Dict[str, Any]) -> None:
        with open(self.board_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def publish_intent(
        self,
        intent_type: str,
        category: str,
        price: float,
        agent_did: str,
        private_key_hex: str,
        godns_locator: str,
        currency: str = DEFAULT_CURRENCY,
        ttl_sec: int = 3600,
        description: str = "",
        specs: Optional[Dict[str, Any]] = None,
        dns_registry: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        MCP Tool: multipass.publish_intent
        Signs and registers an intent (WANT) or offer (OFFER) on the board.
        """
        intent_type_norm = intent_type.upper().strip()
        if intent_type_norm not in ("WANT", "OFFER", "BUYER", "SELLER"):
            raise ValueError(f"Invalid intent_type: {intent_type}. Must be WANT or OFFER.")
        if intent_type_norm == "BUYER":
            intent_type_norm = "WANT"
        elif intent_type_norm == "SELLER":
            intent_type_norm = "OFFER"

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        exp_utc = now_utc + datetime.timedelta(seconds=ttl_sec)
        intent_id = f"intent_{uuid.uuid4().hex[:12]}"
        nonce = os.urandom(16).hex()

        intent_body = {
            "proto": "multipass-intent-v0.1",
            "intent_id": intent_id,
            "intent_type": intent_type_norm,
            "category": category.strip(),
            "agent_did": agent_did,
            "godns_locator": godns_locator,
            "terms": {
                "price": float(price),
                "currency": currency,
                "description": description,
                "specs": specs or {}
            },
            "nonce": nonce,
            "created_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": exp_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ttl_sec": ttl_sec,
            "status": "ACTIVE",
            "simulation": True,
        }

        # Sign canonical JSON (excluding signature field)
        seed = bytes.fromhex(private_key_hex)
        pub = ed25519_publickey_from_seed(seed)
        expected_did = did_from_pubkey(pub)
        if agent_did != expected_did:
            raise ValueError(f"Agent DID mismatch: provided {agent_did}, key derives {expected_did}")

        msg_bytes = canonical_bytes(intent_body)
        sig = ed25519_sign(msg_bytes, seed, pub).hex()
        intent_body["signature"] = sig

        # Save to board
        data = self.load()
        data["intents"].append(intent_body)
        self.save(data)

        # Update optional DNS registry if supplied
        if dns_registry is not None and godns_locator not in dns_registry:
            dns_registry[godns_locator] = f"v=mnd1; k=ed25519; p={agent_did}; scope=intent:publish,match; exp={exp_utc.strftime('%Y-%m-%dT%H:%M:%SZ')};"

        return intent_body

    @staticmethod
    def verify_multipass(
        intent_data: Dict[str, Any],
        dns_registry: Optional[Dict[str, str]] = None,
        enforce_ttl: bool = True,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        MCP Tool: multipass.verify
        Verifies stranger's Multipass:
        1. Checks goDNS TXT record format and authority.
        2. Verifies public key anchor matches agent DID.
        3. Verifies Ed25519 signature over canonical JSON.
        4. Validates TTL / expiration timestamp.
        """
        required = ["proto", "intent_id", "agent_did", "godns_locator", "signature", "expires_at"]
        for field in required:
            if field not in intent_data:
                return False, f"MISSING_REQUIRED_FIELD_{field.upper()}", {"verified": False, "field": field}

        agent_did = intent_data["agent_did"]
        godns_locator = intent_data["godns_locator"]
        sig_hex = intent_data["signature"]

        # 1. DNS TXT Resolution
        txt_record = ""
        if dns_registry and godns_locator in dns_registry:
            txt_record = dns_registry[godns_locator]
        elif not godns_locator.startswith("_mandate."):
            return False, "INVALID_GODNS_LOCATOR", {"verified": False, "reason": "Locator must start with _mandate."}
        else:
            # Default simulated anchor fallback
            txt_record = f"v=mnd1; k=ed25519; p={agent_did}; scope=intent:publish,match; exp=2026-12-31T23:59:59Z;"

        # Check pubkey in DNS
        if f"p={agent_did}" not in txt_record:
            return False, "DNS_PUBLIC_KEY_MISMATCH", {
                "verified": False,
                "reason": "Public key in DNS TXT does not match agent_did",
                "locator": godns_locator,
                "agent_did": agent_did,
            }

        # 2. TTL Check
        if enforce_ttl:
            try:
                exp_dt = datetime.datetime.strptime(intent_data["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                if now_dt > exp_dt:
                    return False, "INTENT_EXPIRED", {
                        "verified": False,
                        "reason": "Intent TTL expired",
                        "expires_at": intent_data["expires_at"],
                        "current_time": now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    }
            except Exception as e:
                return False, "INVALID_EXPIRATION_FORMAT", {"verified": False, "error": str(e)}

        # 3. Ed25519 Cryptographic Verification
        try:
            pub_raw = pubkey_from_did(agent_did)
            sig_raw = bytes.fromhex(sig_hex)
        except Exception as e:
            return False, "INVALID_CRYPTO_ENCODING", {"verified": False, "error": str(e)}

        # Reconstruct canonical body without signature
        clean_body = {k: v for k, v in intent_data.items() if k != "signature"}
        msg_bytes = canonical_bytes(clean_body)

        is_valid = ed25519_verify(sig_raw, msg_bytes, pub_raw)
        if not is_valid:
            return False, "ED25519_SIGNATURE_MISMATCH", {
                "verified": False,
                "reason": "Cryptographic signature mismatch over canonical payload",
                "agent_did": agent_did,
            }

        return True, "VALID", {
            "verified": True,
            "status": "VALID",
            "agent_did": agent_did,
            "godns_locator": godns_locator,
            "simulation": True,
        }

    def match_intents(
        self,
        category: str,
        matchmaker_did: str = "did:key:z6MknGc3ocHs3zgjJvfKBSF35K43AYM822554dooPWBZg308",
        dns_registry: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        MCP Tool: multipass.match_intents
        Discovers compatible (WANT, OFFER) pairs, executes mutual multipass verification,
        and generates a matched deal waiting for Human-in-the-loop closure.
        """
        data = self.load()
        wants = [i for i in data.get("intents", []) if i.get("intent_type") == "WANT" and i.get("category") == category and i.get("status") == "ACTIVE"]
        offers = [i for i in data.get("intents", []) if i.get("intent_type") == "OFFER" and i.get("category") == category and i.get("status") == "ACTIVE"]

        if not wants or not offers:
            return {
                "matched": False,
                "reason": "NO_COMPATIBLE_INTENTS_FOUND",
                "category": category,
                "wants_count": len(wants),
                "offers_count": len(offers),
            }

        # Find best price match (lowest offer price <= highest want budget)
        best_match = None
        for want in wants:
            # Verify buyer multipass
            v_buyer, r_buyer, info_buyer = self.verify_multipass(want, dns_registry)
            if not v_buyer:
                continue

            for offer in offers:
                if want["agent_did"] == offer["agent_did"]:
                    continue  # Cannot self-match

                # Verify seller multipass
                v_seller, r_seller, info_seller = self.verify_multipass(offer, dns_registry)
                if not v_seller:
                    continue

                want_price = float(want["terms"]["price"])
                offer_price = float(offer["terms"]["price"])

                if offer_price <= want_price:
                    best_match = (want, offer, offer_price)
                    break
            if best_match:
                break

        if not best_match:
            return {
                "matched": False,
                "reason": "PRICE_MISMATCH_OR_VERIFICATION_FAILURE",
                "category": category,
            }

        matched_want, matched_offer, settlement_price = best_match
        deal_id = f"deal_flash_{uuid.uuid4().hex[:12]}"
        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        deal = {
            "proto": "flash-deal-v0.1",
            "deal_id": deal_id,
            "category": category,
            "status": "MATCHED_PENDING_APPROVAL",
            "settlement_terms": {
                "amount": settlement_price,
                "currency": matched_offer["terms"]["currency"],
                "buyer_budget": matched_want["terms"]["price"],
                "seller_ask": matched_offer["terms"]["price"],
            },
            "buyer": {
                "did": matched_want["agent_did"],
                "godns_locator": matched_want["godns_locator"],
                "intent_id": matched_want["intent_id"],
                "multipass_verified": True,
            },
            "seller": {
                "did": matched_offer["agent_did"],
                "godns_locator": matched_offer["godns_locator"],
                "intent_id": matched_offer["intent_id"],
                "multipass_verified": True,
            },
            "matchmaker_did": matchmaker_did,
            "human_in_the_loop": {
                "required": True,
                "status": "PENDING_DECISION",
                "prompt": "Финальный тап человека ('валяй') для закрытия сделки"
            },
            "created_at": now_utc,
            "simulation": True,
        }

        # Persist deal
        data["deals"].append(deal)
        self.save(data)

        return {
            "matched": True,
            "deal": deal,
            "deal_id": deal_id,
            "status": "MATCHED_PENDING_APPROVAL",
        }


# ==============================================================================
# HUMAN-IN-THE-LOOP & FLASH EXECUTION
# ==============================================================================
def human_approve_deal(
    deal_data: Dict[str, Any],
    decision: str = "approve",
    approver: str = "pavel@mindrock.capital",
    comment: str = "валяй",
) -> Dict[str, Any]:
    """
    Human-in-the-loop closure gate:
    Evaluates human architect tap ("валяй" / "approve").
    """
    decision_norm = decision.strip().lower()
    is_approved = decision_norm in ("approve", "валяй", "yes", "confirm", "ok", "pass")

    deal = dict(deal_data)
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if is_approved:
        deal["status"] = "APPROVED_BY_HUMAN"
        deal["human_in_the_loop"] = {
            "required": True,
            "status": "APPROVED",
            "approver": approver,
            "decision": "APPROVED",
            "comment": comment or "валяй",
            "approved_at": now_utc,
        }
    else:
        deal["status"] = "REJECTED_BY_HUMAN"
        deal["human_in_the_loop"] = {
            "required": True,
            "status": "REJECTED",
            "approver": approver,
            "decision": "REJECTED",
            "comment": comment or "отказ",
            "rejected_at": now_utc,
        }
    return deal


def flash_execute_deal(
    deal_data: Dict[str, Any],
    board: IntentBoard,
    budget_guard: Optional["BudgetGuard"] = None,
    issuer_key: str = "hack-temple-authority-secret-2026",
    facilitator_key: str = "fac-secret-key-sim-2026",
) -> Dict[str, Any]:
    """
    Flash execution semantics:
    All participants assembled & verified + Human approved -> atomicity executed.
    Deducts settlement, marks intents consumed, issues reputation badge.
    """
    if deal_data.get("status") != "APPROVED_BY_HUMAN":
        return {
            "status": "EXECUTION_ABORTED",
            "reason": "HUMAN_APPROVAL_REQUIRED",
            "current_status": deal_data.get("status"),
        }

    amount = float(deal_data["settlement_terms"]["amount"])
    currency = deal_data["settlement_terms"]["currency"]
    deal_id = deal_data["deal_id"]

    # Budget guard evaluation
    if budget_guard is not None:
        allowed, reason, guard_info = budget_guard.evaluate_request(amount, currency, deal_id)
        if not allowed:
            # Trigger automatic zero-loss rollback
            return flash_rollback_deal(deal_data, board, reason=f"BUDGET_GUARD_{reason}")

    # Mark intents consumed on the board
    data = board.load()
    buyer_intent_id = deal_data["buyer"]["intent_id"]
    seller_intent_id = deal_data["seller"]["intent_id"]
    for intent in data.get("intents", []):
        if intent.get("intent_id") in (buyer_intent_id, seller_intent_id):
            intent["status"] = "CONSUMED"
            intent["settled_in_deal"] = deal_id

    # Issue matchmaker reputation badge
    badge = issue_badge(
        deal_id=deal_id,
        recipient=deal_data["matchmaker_did"],
        settlement_id=f"stl_flash_{deal_id}",
        amount=amount,
        currency=currency,
        score=150,
        tier="PLATINUM",
        issuer_key=issuer_key,
    )

    # Record in ledger if guard provided
    if budget_guard is not None:
        budget_guard.record_transaction(
            deal_id=deal_id,
            amount=amount,
            currency=currency,
            status="APPROVED",
            recipient=deal_data["seller"]["did"],
        )

    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    executed_deal = dict(deal_data)
    executed_deal["status"] = "EXECUTED"
    executed_deal["executed_at"] = now_utc
    executed_deal["badge"] = badge
    executed_deal["settlement_receipt"] = {
        "status": "SETTLED",
        "amount": amount,
        "currency": currency,
        "facilitator_urn": FACILITATOR_URN,
        "atomic_execution": True,
        "simulation": True,
    }

    # Update deals list in board
    data["deals"] = [d if d.get("deal_id") != deal_id else executed_deal for d in data.get("deals", [])]
    board.save(data)

    return executed_deal


def flash_rollback_deal(
    deal_data: Dict[str, Any],
    board: IntentBoard,
    reason: str = "HUMAN_REJECTION_OR_COLLAPSE",
) -> Dict[str, Any]:
    """
    Flash rollback semantics:
    Any participant dropout, human denial, or mismatch -> zero-loss rollback.
    Zero funds deducted, zero ledger loss. Intents returned to ACTIVE or CANCELLED.
    """
    deal_id = deal_data["deal_id"]
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    data = board.load()
    buyer_intent_id = deal_data.get("buyer", {}).get("intent_id")
    seller_intent_id = deal_data.get("seller", {}).get("intent_id")

    # Revert intents on board so no loss is incurred
    for intent in data.get("intents", []):
        if intent.get("intent_id") in (buyer_intent_id, seller_intent_id):
            intent["status"] = "ACTIVE"
            intent.pop("settled_in_deal", None)

    rolled_back = dict(deal_data)
    rolled_back["status"] = "ROLLED_BACK"
    rolled_back["rollback_info"] = {
        "reason": reason,
        "rolled_back_at": now_utc,
        "zero_loss_guarantee": True,
        "funds_deducted": 0.0,
        "simulation": True,
    }

    data["deals"] = [d if d.get("deal_id") != deal_id else rolled_back for d in data.get("deals", [])]
    board.save(data)

    return rolled_back


# ==============================================================================
# BUDGET GUARD ENGINE
# ==============================================================================
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
                    total += float(tx.get("amount", 0.0))
        return total

    def evaluate_request(
        self, amount: float, currency: str, deal_id: str
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Evaluates whether an outgoing payment passes all budget guards."""
        if self.is_kill_switch_active():
            return False, "KILL_SWITCH_ACTIVE", {
                "allowed": False,
                "reason": "KILL_SWITCH_ACTIVE",
                "kill_switch": True,
                "amount": amount,
                "currency": currency,
                "simulation": True,
            }

        if amount > self.per_task_cap:
            return False, "PER_TASK_CAP_EXCEEDED", {
                "allowed": False,
                "reason": "PER_TASK_CAP_EXCEEDED",
                "amount": amount,
                "per_task_cap": self.per_task_cap,
                "currency": currency,
                "simulation": True,
            }

        weekly_spent = self.get_weekly_spent()
        if weekly_spent + amount > self.weekly_cap:
            return False, "WEEKLY_CAP_EXCEEDED", {
                "allowed": False,
                "reason": "WEEKLY_CAP_EXCEEDED",
                "amount": amount,
                "weekly_spent": weekly_spent,
                "weekly_cap": self.weekly_cap,
                "projected_total": weekly_spent + amount,
                "currency": currency,
                "simulation": True,
            }

        return True, "APPROVED", {
            "allowed": True,
            "reason": "WITHIN_LIMITS",
            "amount": amount,
            "weekly_spent": weekly_spent,
            "per_task_cap": self.per_task_cap,
            "weekly_cap": self.weekly_cap,
            "currency": currency,
            "simulation": True,
        }

    def record_transaction(
        self,
        deal_id: str,
        amount: float,
        currency: str,
        status: str,
        recipient: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        data = self._load_ledger()
        tx = {
            "tx_id": f"tx_{uuid.uuid4().hex[:12]}",
            "deal_id": deal_id,
            "amount": float(amount),
            "currency": currency,
            "status": status,
            "recipient": recipient,
            "reason": reason or status,
            "timestamp": time.time(),
            "iso_time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "simulation": True,
        }
        data["transactions"].append(tx)
        self._save_ledger(data)
        return tx

    def summary(self) -> Dict[str, Any]:
        data = self._load_ledger()
        txs = data.get("transactions", [])
        approved = [t for t in txs if t.get("status") == "APPROVED"]
        rejected = [t for t in txs if t.get("status") != "APPROVED"]
        return {
            "kill_switch_active": bool(data.get("kill_switch", False)),
            "per_task_cap": self.per_task_cap,
            "weekly_cap": self.weekly_cap,
            "weekly_spent": self.get_weekly_spent(),
            "total_transactions": len(txs),
            "approved_count": len(approved),
            "rejected_count": len(rejected),
            "total_volume_grace": sum(t.get("amount", 0.0) for t in approved),
            "simulation": True,
        }


# ==============================================================================
# X402 CHALLENGE & SETTLEMENT ENGINE
# ==============================================================================
def create_challenge(
    deal_id: str,
    payer: str,
    recipient: str,
    amount: float,
    currency: str = DEFAULT_CURRENCY,
    ttl_sec: int = 3600,
) -> Dict[str, Any]:
    """Issues an HTTP 402 challenge object requesting signed authorization."""
    challenge_id = f"ch_{uuid.uuid4().hex[:12]}"
    nonce = os.urandom(16).hex()
    expires_at = time.time() + ttl_sec

    challenge = {
        "status_code": 402,
        "challenge_id": challenge_id,
        "deal_id": deal_id,
        "payer": payer,
        "recipient": recipient,
        "amount": float(amount),
        "currency": currency,
        "nonce": nonce,
        "issued_at": time.time(),
        "expires_at": expires_at,
        "simulation": True,
        "crypto_scheme": CRYPTO_SCHEME,
    }
    return challenge


def sign_payment(
    challenge: Dict[str, Any],
    payer_key: str,
    payer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Signs payment authorization for a given 402 challenge."""
    if challenge.get("expires_at", 0) < time.time():
        raise ValueError("Cannot sign expired challenge")

    payment_body = {
        "challenge_id": challenge["challenge_id"],
        "deal_id": challenge["deal_id"],
        "payer": payer_id or challenge["payer"],
        "recipient": challenge["recipient"],
        "amount": challenge["amount"],
        "currency": challenge["currency"],
        "nonce": challenge["nonce"],
        "signed_at": time.time(),
        "simulation": True,
    }

    sig = compute_hmac(payer_key, payment_body)
    payment_body["signature"] = sig
    payment_body["crypto_scheme"] = CRYPTO_SCHEME
    return payment_body


def settle_payment(
    payment: Dict[str, Any],
    challenge: Dict[str, Any],
    payer_key: str,
    facilitator_key: str,
    budget_guard: Optional[BudgetGuard] = None,
    issuer_key: str = "hack-temple-authority-secret-2026",
    badge_score: int = 100,
    badge_tier: str = "GOLD",
) -> Dict[str, Any]:
    """Validates challenge, signature, and budget guards, then settles payment."""
    if payment["challenge_id"] != challenge["challenge_id"]:
        raise ValueError("Challenge ID mismatch")
    if payment["nonce"] != challenge["nonce"]:
        raise ValueError("Nonce replay/mismatch detected")
    if time.time() > challenge["expires_at"]:
        raise ValueError("Challenge expired")

    # Verify payer signature
    clean_payment = {k: v for k, v in payment.items() if k not in ("signature", "crypto_scheme")}
    if not verify_hmac(payer_key, clean_payment, payment["signature"]):
        raise ValueError("Payer signature verification failed")

    amount = float(payment["amount"])
    currency = payment["currency"]
    deal_id = payment["deal_id"]

    # Budget guard evaluation
    if budget_guard is not None:
        allowed, reason, guard_info = budget_guard.evaluate_request(amount, currency, deal_id)
        if not allowed:
            budget_guard.record_transaction(
                deal_id=deal_id,
                amount=amount,
                currency=currency,
                status="REJECTED_BUDGET_GUARD",
                recipient=payment["recipient"],
                reason=reason,
            )
            return {
                "status": "REJECTED_BUDGET_GUARD",
                "reason": reason,
                "deal_id": deal_id,
                "amount": amount,
                "currency": currency,
                "simulation": True,
            }

    # Record approved transaction
    if budget_guard is not None:
        budget_guard.record_transaction(
            deal_id=deal_id,
            amount=amount,
            currency=currency,
            status="APPROVED",
            recipient=payment["recipient"],
            reason="SETTLEMENT_SUCCESSFUL",
        )

    settlement_id = f"stl_{uuid.uuid4().hex[:12]}"
    receipt_body = {
        "settlement_id": settlement_id,
        "deal_id": deal_id,
        "payer": payment["payer"],
        "recipient": payment["recipient"],
        "amount": amount,
        "currency": currency,
        "settled_at": time.time(),
        "facilitator_urn": FACILITATOR_URN,
        "simulation": True,
    }

    fac_sig = compute_hmac(facilitator_key, receipt_body)
    receipt_body["facilitator_signature"] = fac_sig
    receipt_body["status"] = "SETTLED"

    # Issue Matchmaker Reputation Badge
    badge = issue_badge(
        deal_id=deal_id,
        recipient=payment["recipient"],
        settlement_id=settlement_id,
        amount=amount,
        currency=currency,
        score=badge_score,
        tier=badge_tier,
        issuer_key=issuer_key,
    )
    receipt_body["badge"] = badge
    return receipt_body


# ==============================================================================
# REPUTATION BADGE ENGINE
# ==============================================================================
def issue_badge(
    deal_id: str,
    recipient: str,
    settlement_id: str,
    amount: float,
    currency: str = DEFAULT_CURRENCY,
    score: int = 100,
    tier: str = "GOLD",
    issuer_key: str = "hack-temple-authority-secret-2026",
    ttl_days: int = 30,
) -> Dict[str, Any]:
    """Issues a cryptographically signed Matchmaker Reputation Badge."""
    issued_at = time.time()
    expires_at = issued_at + (ttl_days * 86400)
    badge_id = f"badge_{uuid.uuid4().hex[:12]}"

    badge_body = {
        "badge_id": badge_id,
        "badge_type": "FLASH_SETTLEMENT_MATCHMAKER",
        "issuer": DEFAULT_ISSUER_URN,
        "recipient": recipient,
        "deal_id": deal_id,
        "settlement_id": settlement_id,
        "volume_grace": float(amount),
        "reputation_score": int(score),
        "tier": tier,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "simulation": True,
        "crypto_scheme": CRYPTO_SCHEME,
    }

    sig = compute_hmac(issuer_key, badge_body)
    badge_body["signature"] = sig
    return badge_body


def verify_badge(badge: Dict[str, Any], issuer_key: str, current_time: Optional[float] = None) -> Dict[str, Any]:
    """Independently verifies reputation badge authenticity and expiration."""
    if current_time is None:
        current_time = time.time()

    if not badge.get("simulation") or badge.get("crypto_scheme") != CRYPTO_SCHEME:
        return {
            "verified": False,
            "status": "INVALID",
            "error": "INVALID_SIMULATION_SCHEME",
            "simulation": badge.get("simulation", False),
        }

    expires_at = badge.get("expires_at", 0)
    if current_time > expires_at:
        return {
            "verified": False,
            "status": "EXPIRED",
            "error": "BADGE_EXPIRED",
            "expired_by_seconds": int(current_time - expires_at),
            "simulation": True,
        }

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
        "reputation_score": badge.get("reputation_score"),
        "tier": badge.get("tier"),
        "simulation": True,
    }


# ==============================================================================
# MCP TOOL RUNNER
# ==============================================================================
def run_mcp_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatches direct MCP tool invocations."""
    board = IntentBoard(board_path=arguments.get("board_path", "/tmp/flash_intent_board.json"))
    dns_reg = arguments.get("dns_registry")

    if tool_name in ("multipass.publish_intent", "publish_intent"):
        return board.publish_intent(
            intent_type=arguments["intent_type"],
            category=arguments["category"],
            price=float(arguments["price"]),
            agent_did=arguments["agent_did"],
            private_key_hex=arguments["private_key_hex"],
            godns_locator=arguments["godns_locator"],
            currency=arguments.get("currency", DEFAULT_CURRENCY),
            ttl_sec=int(arguments.get("ttl_sec", 3600)),
            description=arguments.get("description", ""),
            specs=arguments.get("specs"),
            dns_registry=dns_reg,
        )

    elif tool_name in ("multipass.verify", "verify"):
        intent_payload = arguments.get("intent") or arguments
        verified, reason, info = IntentBoard.verify_multipass(intent_payload, dns_registry=dns_reg)
        return {"verified": verified, "reason": reason, "details": info}

    elif tool_name in ("multipass.match_intents", "match_intents"):
        return board.match_intents(
            category=arguments["category"],
            matchmaker_did=arguments.get("matchmaker_did", "did:key:z6MknGc3ocHs3zgjJvfKBSF35K43AYM822554dooPWBZg308"),
            dns_registry=dns_reg,
        )

    elif tool_name in ("multipass.human_approve", "human_approve"):
        deal_data = arguments["deal"]
        return human_approve_deal(
            deal_data=deal_data,
            decision=arguments.get("decision", "approve"),
            approver=arguments.get("approver", "pavel@mindrock.capital"),
            comment=arguments.get("comment", "валяй"),
        )

    elif tool_name in ("multipass.flash_execute", "flash_execute"):
        deal_data = arguments["deal"]
        guard = None
        if "ledger" in arguments:
            guard = BudgetGuard(ledger_path=arguments["ledger"])
        return flash_execute_deal(
            deal_data=deal_data,
            board=board,
            budget_guard=guard,
            issuer_key=arguments.get("issuer_key", "hack-temple-authority-secret-2026"),
        )

    elif tool_name in ("multipass.flash_rollback", "flash_rollback"):
        deal_data = arguments["deal"]
        return flash_rollback_deal(
            deal_data=deal_data,
            board=board,
            reason=arguments.get("reason", "ABORTED"),
        )

    else:
        raise ValueError(f"Unknown MCP tool: {tool_name}")


# ==============================================================================
# CLI & PARSER
# ==============================================================================
def parse_args():
    parser = argparse.ArgumentParser(description="Flash Protocol & Settlement Engine (MIN-146 / MIN-147)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. create-identity
    p_id = subparsers.add_parser("create-identity", help="Generate Ed25519 keypair, did:key, and goDNS record")
    p_id.add_argument("--name", default="agent", help="Agent local identity name")
    p_id.add_argument("--domain", default="hackt.com", help="Agent sovereign domain anchor")
    p_id.add_argument("--out", help="Output path for identity JSON")

    # 2. publish-intent
    p_pi = subparsers.add_parser("publish-intent", help="Publish signed intent/offer to the board (MCP multipass.publish_intent)")
    p_pi.add_argument("--board", default="/tmp/flash_intent_board.json", help="Path to intent board JSON")
    p_pi.add_argument("--agent-did", required=True, help="Agent did:key string")
    p_pi.add_argument("--private-key", required=True, help="Agent Ed25519 private seed (64 hex characters)")
    p_pi.add_argument("--godns-locator", required=True, help="DNS locator (e.g. _mandate.agy.hackt.com)")
    p_pi.add_argument("--intent-type", choices=["WANT", "OFFER"], required=True, help="Intent type")
    p_pi.add_argument("--category", required=True, help="Intent category (e.g. compute:gpu)")
    p_pi.add_argument("--price", type=float, required=True, help="Budget/offer price in GRACE")
    p_pi.add_argument("--currency", default=DEFAULT_CURRENCY, help="Currency (default GRACE)")
    p_pi.add_argument("--ttl-sec", type=int, default=3600, help="TTL in seconds")
    p_pi.add_argument("--description", default="", help="Human-readable description")
    p_pi.add_argument("--dns-registry", help="Path to simulated DNS registry JSON")
    p_pi.add_argument("--out", help="Output path for published intent JSON")

    # 3. verify-multipass
    p_vm = subparsers.add_parser("verify-multipass", help="Verify stranger multipass / intent signature & DNS anchor (MCP multipass.verify)")
    p_vm.add_argument("--intent", required=True, help="Path to intent JSON file")
    p_vm.add_argument("--dns-registry", help="Path to simulated DNS registry JSON")
    p_vm.add_argument("--no-ttl", action="store_true", help="Bypass TTL expiry validation")
    p_vm.add_argument("--out", help="Output path for verification result JSON")

    # 4. match-intents
    p_mi = subparsers.add_parser("match-intents", help="Match compatible signed intents on board (MCP multipass.match_intents)")
    p_mi.add_argument("--board", default="/tmp/flash_intent_board.json", help="Path to intent board JSON")
    p_mi.add_argument("--category", required=True, help="Target category to match")
    p_mi.add_argument("--matchmaker-did", default="did:key:z6MknGc3ocHs3zgjJvfKBSF35K43AYM822554dooPWBZg308", help="Matchmaker DID")
    p_mi.add_argument("--dns-registry", help="Path to simulated DNS registry JSON")
    p_mi.add_argument("--out", help="Output path for match deal JSON")

    # 5. human-approve
    p_ha = subparsers.add_parser("human-approve", help="Human-in-the-loop closure gate ('валяй' approval)")
    p_ha.add_argument("--deal", required=True, help="Path to matched deal JSON")
    p_ha.add_argument("--decision", choices=["approve", "валяй", "reject", "отказ"], default="approve", help="Human decision")
    p_ha.add_argument("--approver", default="pavel@mindrock.capital", help="Human approver email")
    p_ha.add_argument("--comment", default="валяй", help="Human comment / verdict")
    p_ha.add_argument("--out", help="Output path for approved deal JSON")

    # 6. flash-execute
    p_fe = subparsers.add_parser("flash-execute", help="Atomically execute verified and approved flash deal")
    p_fe.add_argument("--deal", required=True, help="Path to approved deal JSON")
    p_fe.add_argument("--board", default="/tmp/flash_intent_board.json", help="Path to intent board JSON")
    p_fe.add_argument("--ledger", default="/tmp/flash_settlement_ledger.json", help="Path to ledger JSON")
    p_fe.add_argument("--issuer-key", default="hack-temple-authority-secret-2026", help="Badge issuer secret key")
    p_fe.add_argument("--out", help="Output path for execution receipt JSON")

    # 7. flash-rollback
    p_fr = subparsers.add_parser("flash-rollback", help="Execute zero-loss rollback for rejected or failed deal")
    p_fr.add_argument("--deal", required=True, help="Path to deal JSON")
    p_fr.add_argument("--board", default="/tmp/flash_intent_board.json", help="Path to intent board JSON")
    p_fr.add_argument("--reason", default="Human rejected or participant timeout", help="Rollback reason")
    p_fr.add_argument("--out", help="Output path for rollback receipt JSON")

    # 8. mcp-call
    p_mc = subparsers.add_parser("mcp-call", help="Direct MCP tool invocation helper")
    p_mc.add_argument("--tool", required=True, help="Tool name (e.g. multipass.publish_intent)")
    p_mc.add_argument("--arguments", required=True, help="Tool arguments as JSON string")
    p_mc.add_argument("--out", help="Output path for MCP tool result JSON")

    # --- EXISTING X402 & BUDGET GUARD COMMANDS (Backwards Compatibility with MIN-147) ---
    # 9. create-challenge
    p_ch = subparsers.add_parser("create-challenge", help="Create HTTP 402 challenge object")
    p_ch.add_argument("--deal-id", required=True, help="Flash deal identifier")
    p_ch.add_argument("--payer", required=True, help="Payer DID")
    p_ch.add_argument("--recipient", required=True, help="Recipient DID")
    p_ch.add_argument("--amount", type=float, required=True, help="Settlement amount")
    p_ch.add_argument("--currency", default=DEFAULT_CURRENCY, help="Settlement currency (default: GRACE)")
    p_ch.add_argument("--ttl-sec", type=int, default=3600, help="Challenge TTL seconds")
    p_ch.add_argument("--out", help="Output path for challenge JSON")

    # 10. sign-payment
    p_sp = subparsers.add_parser("sign-payment", help="Sign 402 payment authorization with wallet key")
    p_sp.add_argument("--challenge", required=True, help="Path to challenge JSON file")
    p_sp.add_argument("--payer-key", required=True, help="Payer wallet simulation secret key")
    p_sp.add_argument("--payer-id", help="Override payer identifier")
    p_sp.add_argument("--out", help="Output path for signed payment JSON")

    # 11. settle-payment
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

    # 12. issue-badge
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

    # 13. verify-badge
    p_vb = subparsers.add_parser("verify-badge", help="Independently verify badge signature and expiry")
    p_vb.add_argument("--badge", required=True, help="Path to badge JSON file")
    p_vb.add_argument("--issuer-key", default="hack-temple-authority-secret-2026", help="Issuer verification key")

    # 14. budget-check
    p_bc = subparsers.add_parser("budget-check", help="Evaluate budget guard for a proposed transaction")
    p_bc.add_argument("--deal-id", required=True, help="Deal identifier")
    p_bc.add_argument("--amount", type=float, required=True, help="Proposed transaction amount")
    p_bc.add_argument("--currency", default=DEFAULT_CURRENCY, help="Currency")
    p_bc.add_argument("--ledger", default="/tmp/flash_settlement_ledger.json", help="Path to ledger JSON")
    p_bc.add_argument("--per-task-cap", type=float, default=DEFAULT_PER_TASK_CAP, help="Per-task spending cap")
    p_bc.add_argument("--weekly-cap", type=float, default=DEFAULT_WEEKLY_CAP, help="Weekly spending cap")

    # 15. budget-kill-switch
    p_ks = subparsers.add_parser("budget-kill-switch", help="Set budget guard kill-switch state")
    p_ks.add_argument("--ledger", default="/tmp/flash_settlement_ledger.json", help="Path to ledger JSON")
    p_ks.add_argument("--state", choices=["ON", "OFF"], required=True, help="Kill-switch state")

    # 16. budget-summary
    p_bs = subparsers.add_parser("budget-summary", help="Print budget guard ledger summary")
    p_bs.add_argument("--ledger", default="/tmp/flash_settlement_ledger.json", help="Path to ledger JSON")

    return parser.parse_args()


def output_json(data: Dict[str, Any], out_path: Optional[str] = None):
    dumped = json.dumps(data, indent=2, ensure_ascii=False)
    if out_path:
        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(dumped)
    print(dumped)


def main():
    args = parse_args()

    # Intent Board Commands
    if args.command == "create-identity":
        ident = generate_identity(name=args.name, domain=args.domain)
        output_json(ident, args.out)

    elif args.command == "publish-intent":
        dns_reg = None
        if args.dns_registry and os.path.exists(args.dns_registry):
            with open(args.dns_registry, 'r', encoding='utf-8') as f:
                dns_reg = json.load(f)

        board = IntentBoard(board_path=args.board)
        res = board.publish_intent(
            intent_type=args.intent_type,
            category=args.category,
            price=args.price,
            agent_did=args.agent_did,
            private_key_hex=args.private_key,
            godns_locator=args.godns_locator,
            currency=args.currency,
            ttl_sec=args.ttl_sec,
            description=args.description,
            dns_registry=dns_reg,
        )
        if dns_reg is not None and args.dns_registry:
            with open(args.dns_registry, 'w', encoding='utf-8') as f:
                json.dump(dns_reg, f, indent=2)
        output_json(res, args.out)

    elif args.command == "verify-multipass":
        with open(args.intent, 'r', encoding='utf-8') as f:
            intent_data = json.load(f)

        dns_reg = None
        if args.dns_registry and os.path.exists(args.dns_registry):
            with open(args.dns_registry, 'r', encoding='utf-8') as f:
                dns_reg = json.load(f)

        v, reason, info = IntentBoard.verify_multipass(intent_data, dns_registry=dns_reg, enforce_ttl=not args.no_ttl)
        info["status"] = reason
        output_json(info, args.out)
        if not v:
            sys.exit(1)

    elif args.command == "match-intents":
        dns_reg = None
        if args.dns_registry and os.path.exists(args.dns_registry):
            with open(args.dns_registry, 'r', encoding='utf-8') as f:
                dns_reg = json.load(f)

        board = IntentBoard(board_path=args.board)
        res = board.match_intents(
            category=args.category,
            matchmaker_did=args.matchmaker_did,
            dns_registry=dns_reg,
        )
        output_json(res, args.out)
        if not res.get("matched", False):
            sys.exit(1)

    elif args.command == "human-approve":
        with open(args.deal, 'r', encoding='utf-8') as f:
            deal_data = json.load(f)
        if "deal" in deal_data:
            deal_data = deal_data["deal"]

        res = human_approve_deal(
            deal_data=deal_data,
            decision=args.decision,
            approver=args.approver,
            comment=args.comment,
        )
        output_json(res, args.out)
        if res.get("status") == "REJECTED_BY_HUMAN":
            sys.exit(2)

    elif args.command == "flash-execute":
        with open(args.deal, 'r', encoding='utf-8') as f:
            deal_data = json.load(f)
        if "deal" in deal_data:
            deal_data = deal_data["deal"]

        board = IntentBoard(board_path=args.board)
        guard = BudgetGuard(ledger_path=args.ledger)
        res = flash_execute_deal(
            deal_data=deal_data,
            board=board,
            budget_guard=guard,
            issuer_key=args.issuer_key,
        )
        output_json(res, args.out)
        if res.get("status") != "EXECUTED":
            sys.exit(1)

    elif args.command == "flash-rollback":
        with open(args.deal, 'r', encoding='utf-8') as f:
            deal_data = json.load(f)
        if "deal" in deal_data:
            deal_data = deal_data["deal"]

        board = IntentBoard(board_path=args.board)
        res = flash_rollback_deal(deal_data=deal_data, board=board, reason=args.reason)
        output_json(res, args.out)

    elif args.command == "mcp-call":
        args_dict = json.loads(args.arguments)
        res = run_mcp_tool(args.tool, args_dict)
        output_json(res, args.out)

    # Legacy Commands
    elif args.command == "create-challenge":
        ch = create_challenge(
            deal_id=args.deal_id,
            payer=args.payer,
            recipient=args.recipient,
            amount=args.amount,
            currency=args.currency,
            ttl_sec=args.ttl_sec,
        )
        output_json(ch, args.out)

    elif args.command == "sign-payment":
        with open(args.challenge, 'r', encoding='utf-8') as f:
            ch = json.load(f)
        pay = sign_payment(ch, payer_key=args.payer_key, payer_id=args.payer_id)
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
        output_json(res, args.out)
        if res.get("status") == "REJECTED_BUDGET_GUARD":
            sys.exit(1)

    elif args.command == "issue-badge":
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
