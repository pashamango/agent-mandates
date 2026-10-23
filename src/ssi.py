#!/usr/bin/env python3
"""SSI toolkit for the agent fleet (MIN-106).

did:key with Ed25519. No registry needed for verification — the DID string
is self-certifying. Task keys: ephemeral, TTL-bound, burn on expiry.
"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)
from cryptography.hazmat.primitives import serialization
import hashlib
import json
import os
import time

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MULTICODEC_ED25519_PUB = bytes([0xed, 0x01])
DID_PREFIX = "did:key:z"


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
    assert len(pub_raw) == 32, "Ed25519 pubkey must be 32 bytes"
    return DID_PREFIX + b58encode(MULTICODEC_ED25519_PUB + pub_raw)


def pubkey_from_did(did: str) -> bytes:
    assert did.startswith(DID_PREFIX), "not a did:key:z"
    raw = b58decode(did[len(DID_PREFIX):])
    assert raw[:2] == MULTICODEC_ED25519_PUB, "not an Ed25519 key"
    assert len(raw) == 34, "bad key length"
    return raw[2:]


def generate_identity():
    """Returns (did_string, private_bytes_32, public_bytes_32)."""
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return did_from_pubkey(pub_raw), priv_raw, pub_raw


def sign_message(priv_raw: bytes, message: bytes) -> bytes:
    priv = Ed25519PrivateKey.from_private_bytes(priv_raw)
    return priv.sign(message)


def verify_message(did: str, message: bytes, signature: bytes) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(pubkey_from_did(did))
        pub.verify(signature, message)
        return True
    except Exception:
        return False


def fingerprint(did: str) -> str:
    """Short human-checkable fingerprint for out-of-band verification."""
    return hashlib.sha256(did.encode()).hexdigest()[:16]


if __name__ == "__main__":
    assert b58encode(b"hello world") == "StV1DL6CwTryKyV", "base58 vector"
    did, priv, pub = generate_identity()
    assert did.startswith("did:key:z")
    assert pubkey_from_did(did) == pub, "roundtrip"
    msg = b"task_submit:MIN-80:run-001"
    sig = sign_message(priv, msg)
    assert verify_message(did, msg, sig), "verify"
    assert not verify_message(did, msg + b"x", sig), "tamper must fail"
    print("ssi self-test OK;", did[:24] + "...", "fp", fingerprint(did))
