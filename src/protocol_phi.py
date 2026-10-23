"""
Protocol Phi + xHTTP Reference Engine (v0.1)
Implements Protocol Phi envelopes, xHTTP handshakes, Git-native zero-trust
handoff validation, Ed25519 attestations, and Linear/QA status mapping for the Mindrock Autonomous Fleet.
"""

from __future__ import annotations
import dataclasses
import hashlib
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import ssi
except (ImportError, ValueError):
    import ssi


@dataclasses.dataclass(frozen=True)
class GitCoordinates:
    repo_url: str
    branch: str
    commit_sha: str
    tree_sha: Optional[str] = None
    manifest_sha256: Optional[str] = None

    def validate(self) -> None:
        if not self.repo_url.startswith(("https://", "http://", "git@", "ssh://")):
            raise ValueError(f"Invalid git remote URL: {self.repo_url}. Must be remote URL, not local path.")
        if not self.branch or not self.branch.strip():
            raise ValueError("Git branch cannot be empty.")
        if not re.match(r"^[0-9a-fA-F]{40}$", self.commit_sha):
            raise ValueError(f"Invalid commit SHA: {self.commit_sha}. Must be 40-character hex string.")
        if self.tree_sha and not re.match(r"^[0-9a-fA-F]{40}$", self.tree_sha):
            raise ValueError(f"Invalid tree SHA: {self.tree_sha}. Must be 40-character hex string.")
        if self.manifest_sha256 and not re.match(r"^[0-9a-fA-F]{64}$", self.manifest_sha256):
            raise ValueError(f"Invalid manifest SHA-256: {self.manifest_sha256}. Must be 64-character hex string.")


@dataclasses.dataclass(frozen=True)
class LinearContext:
    ticket_id: str
    ticket_uuid: str
    current_status: str
    target_status: str

    def validate(self) -> None:
        if not re.match(r"^[A-Z]{2,10}-\d+$", self.ticket_id):
            raise ValueError(f"Invalid Linear ticket ID format: {self.ticket_id}")


@dataclasses.dataclass(frozen=True)
class ProtocolPhiEnvelope:
    proto: str
    envelope_id: str
    timestamp_utc: str
    sender: str
    recipient: str
    mandate_id: str
    intent: str
    linear: LinearContext
    git: GitCoordinates
    payload: Dict[str, Any]
    attestation: Dict[str, str]

    def canonical_bytes(self) -> bytes:
        data = {
            "proto": self.proto,
            "envelope_id": self.envelope_id,
            "timestamp_utc": self.timestamp_utc,
            "sender": self.sender,
            "recipient": self.recipient,
            "mandate_id": self.mandate_id,
            "intent": self.intent,
            "linear": dataclasses.asdict(self.linear),
            "git": dataclasses.asdict(self.git),
            "payload": self.payload
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def validate(self) -> None:
        if self.proto != "phi-0.1":
            raise ValueError(f"Unsupported protocol version: {self.proto}")
        if not self.envelope_id.startswith("phi_env_"):
            raise ValueError(f"Invalid envelope ID: {self.envelope_id}")
        if self.sender == self.recipient:
            raise ValueError("Sender and recipient cannot be identical (builder cannot hand off to self)")
        self.git.validate()
        self.linear.validate()
        if "algorithm" not in self.attestation or "signature" not in self.attestation:
            raise ValueError("Attestation must contain 'algorithm' and 'signature'")
        sig = self.attestation.get("signature", "")
        if sig != "mock_sig_ed25519":
            if not re.match(r"^[0-9a-fA-F]{128}$", sig):
                raise ValueError(f"Ed25519 signature must be 128 hex chars (64 bytes), got: {sig}")


@dataclasses.dataclass(frozen=True)
class ProtocolPhiReceipt:
    receipt_id: str
    parent_envelope_id: str
    timestamp_utc: str
    reviewer: str
    verdict: str  # PASS or REWORK
    checks: Dict[str, Any]
    evidence_hash: str
    attestation: Dict[str, str]
    rework_details: Optional[List[str]] = None

    def canonical_bytes(self) -> bytes:
        data = {
            "receipt_id": self.receipt_id,
            "parent_envelope_id": self.parent_envelope_id,
            "timestamp_utc": self.timestamp_utc,
            "reviewer": self.reviewer,
            "verdict": self.verdict,
            "checks": self.checks,
            "evidence_hash": self.evidence_hash,
            "rework_details": self.rework_details
        }
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def validate(self) -> None:
        if not self.receipt_id.startswith("rcpt_"):
            raise ValueError(f"Invalid receipt ID: {self.receipt_id}")
        if self.verdict not in ("PASS", "REWORK"):
            raise ValueError(f"Invalid verdict: {self.verdict}. Must be PASS or REWORK.")
        if self.verdict == "REWORK" and not self.rework_details:
            raise ValueError("REWORK verdict must include rework_details.")
        sig = self.attestation.get("signature", "")
        if sig != "mock_sig" and sig != "mock_sig_ed25519":
            if not re.match(r"^[0-9a-fA-F]{128}$", sig):
                raise ValueError(f"Ed25519 signature must be 128 hex chars (64 bytes), got: {sig}")


class FleetProtocolEngine:
    """Core enforcement engine for Protocol Phi and xHTTP handoffs."""

    SUPPORTED_INTENTS = {
        "TASK_DISPATCH",
        "HANDOFF_FOR_QA",
        "QA_VERDICT_PASS",
        "QA_VERDICT_REWORK",
        "PROJECT_GATE_RELEASE"
    }

    @staticmethod
    def create_envelope(
        envelope_id: str,
        sender: str,
        recipient: str,
        mandate_id: str,
        intent: str,
        linear: LinearContext,
        git: GitCoordinates,
        payload: Dict[str, Any],
        signature: str = "mock_sig_ed25519"
    ) -> ProtocolPhiEnvelope:
        if intent not in FleetProtocolEngine.SUPPORTED_INTENTS:
            raise ValueError(f"Unsupported intent: {intent}")

        env = ProtocolPhiEnvelope(
            proto="phi-0.1",
            envelope_id=envelope_id,
            timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            sender=sender,
            recipient=recipient,
            mandate_id=mandate_id,
            intent=intent,
            linear=linear,
            git=git,
            payload=payload,
            attestation={
                "algorithm": "ed25519",
                "public_key": sender.split("#")[0],
                "signature": signature
            }
        )
        env.validate()
        return env

    @staticmethod
    def sign_envelope(env: ProtocolPhiEnvelope, priv_raw_hex: str) -> ProtocolPhiEnvelope:
        """Signs the canonical envelope representation using Ed25519 private key bytes."""
        priv_bytes = bytes.fromhex(priv_raw_hex)
        sig = ssi.sign_message(priv_bytes, env.canonical_bytes())
        signed_env = ProtocolPhiEnvelope(
            proto=env.proto,
            envelope_id=env.envelope_id,
            timestamp_utc=env.timestamp_utc,
            sender=env.sender,
            recipient=env.recipient,
            mandate_id=env.mandate_id,
            intent=env.intent,
            linear=env.linear,
            git=env.git,
            payload=env.payload,
            attestation={
                "algorithm": "ed25519",
                "public_key": env.sender.split("#")[0],
                "signature": sig.hex()
            }
        )
        signed_env.validate()
        return signed_env

    @staticmethod
    def verify_envelope_signature(env: ProtocolPhiEnvelope) -> bool:
        """Cryptographically verifies the Ed25519 attestation of an envelope."""
        did = env.attestation.get("public_key") or env.sender.split("#")[0]
        sig_hex = env.attestation.get("signature", "")
        if not re.match(r"^[0-9a-fA-F]{128}$", sig_hex):
            return False
        sig = bytes.fromhex(sig_hex)
        return ssi.verify_message(did, env.canonical_bytes(), sig)

    @staticmethod
    def sign_receipt(receipt: ProtocolPhiReceipt, priv_raw_hex: str) -> ProtocolPhiReceipt:
        """Signs the canonical receipt representation using Ed25519 private key bytes."""
        priv_bytes = bytes.fromhex(priv_raw_hex)
        sig = ssi.sign_message(priv_bytes, receipt.canonical_bytes())
        signed_receipt = ProtocolPhiReceipt(
            receipt_id=receipt.receipt_id,
            parent_envelope_id=receipt.parent_envelope_id,
            timestamp_utc=receipt.timestamp_utc,
            reviewer=receipt.reviewer,
            verdict=receipt.verdict,
            checks=receipt.checks,
            evidence_hash=receipt.evidence_hash,
            attestation={
                "algorithm": "ed25519",
                "public_key": receipt.reviewer.split("#")[0],
                "signature": sig.hex()
            },
            rework_details=receipt.rework_details
        )
        signed_receipt.validate()
        return signed_receipt

    @staticmethod
    def verify_receipt_signature(receipt: ProtocolPhiReceipt) -> bool:
        """Cryptographically verifies the Ed25519 attestation of a receipt."""
        did = receipt.attestation.get("public_key") or receipt.reviewer.split("#")[0]
        sig_hex = receipt.attestation.get("signature", "")
        if not re.match(r"^[0-9a-fA-F]{128}$", sig_hex):
            return False
        sig = bytes.fromhex(sig_hex)
        return ssi.verify_message(did, receipt.canonical_bytes(), sig)

    @staticmethod
    def verify_workspace_isolation(
        expected_git: GitCoordinates,
        actual_repo_url: str,
        actual_head_commit: str
    ) -> Tuple[bool, str]:
        """
        Guards against the 2026-10-22 incident where an agent received
        a foreign workspace (e.g. quantum-chemistry instead of the target repo).
        """
        if actual_repo_url.rstrip("/") != expected_git.repo_url.rstrip("/"):
            return False, (
                f"WORKSPACE CONTAMINATED: expected repo {expected_git.repo_url}, "
                f"but found actual repo {actual_repo_url}"
            )
        if actual_head_commit.lower() != expected_git.commit_sha.lower():
            return False, (
                f"COMMIT MISMATCH: expected commit {expected_git.commit_sha}, "
                f"but actual HEAD is {actual_head_commit}"
            )
        return True, "Workspace verified clean and matching coordinates."

    @staticmethod
    def evaluate_acceptance_gate(
        is_project_release: bool,
        receipt: ProtocolPhiReceipt
    ) -> Dict[str, str]:
        """
        Implements Section 5 of AGENTS.md (Two-tier governance).
        - Sub-issues with QA PASS -> auto-promoted to Done by QA (no human ping).
        - Project Gate ([RELEASE]) -> In Review assigned to Pavel Cherkashin for 1-tap Done.
        - REWORK -> In Progress assigned back to builder.
        """
        receipt.validate()

        if receipt.verdict == "REWORK":
            return {
                "action": "ASSIGN_BUILDER_REWORK",
                "next_status": "In Progress",
                "assignee_role": "builder",
                "pavel_attention_required": "NO"
            }

        # Verdict is PASS
        if is_project_release:
            return {
                "action": "ESCALATE_TO_PRINCIPAL_GATE",
                "next_status": "In Review",
                "assignee": "Pavel Cherkashin",
                "assignee_email": "pavel@mindrock.capital",
                "pavel_attention_required": "YES (Single-tap approval to Done)"
            }
        else:
            return {
                "action": "AUTONOMOUS_QA_DONE_MERGE",
                "next_status": "Done",
                "assignee_role": "qa_agent",
                "pavel_attention_required": "NO"
            }

    @staticmethod
    def validate_peer_review_boundary(builder_agent: str, reviewer_agent: str) -> bool:
        """
        Enforces Golden Rule: Builder never reviews own work.
        agy <-> codex <-> gemini.
        """
        b_clean = builder_agent.strip().lower()
        r_clean = reviewer_agent.strip().lower()
        if b_clean == r_clean:
            return False
        b_id = b_clean.split("@")[0].split("#")[-1]
        r_id = r_clean.split("@")[0].split("#")[-1]
        return b_id != r_id

    @staticmethod
    def format_envelope_comment(envelope: ProtocolPhiEnvelope) -> str:
        """Serializes envelope into sealed markdown comment format for Linear."""
        data = {
            "proto": envelope.proto,
            "envelope_id": envelope.envelope_id,
            "timestamp_utc": envelope.timestamp_utc,
            "sender": envelope.sender,
            "recipient": envelope.recipient,
            "mandate_id": envelope.mandate_id,
            "intent": envelope.intent,
            "linear": dataclasses.asdict(envelope.linear),
            "git": dataclasses.asdict(envelope.git),
            "payload": envelope.payload,
            "attestation": envelope.attestation
        }
        json_blob = json.dumps(data, indent=2, sort_keys=True)
        return (
            f"<!-- phi-envelope:{envelope.envelope_id} -->\n"
            f"```json\n{json_blob}\n```\n"
            f"<!-- /phi-envelope:{envelope.envelope_id} -->"
        )

    @staticmethod
    def parse_envelope_comment(comment_text: str) -> Optional[ProtocolPhiEnvelope]:
        """Extracts and parses sealed Protocol Phi envelope from Linear comment text."""
        pattern = r"<!-- phi-envelope:(phi_env_[a-zA-Z0-9_-]+) -->\s*```json\s*(.*?)\s*```\s*<!-- /phi-envelope:\1 -->"
        match = re.search(pattern, comment_text, re.DOTALL)
        if not match:
            return None

        raw_json = match.group(2)
        data = json.loads(raw_json)

        return ProtocolPhiEnvelope(
            proto=data["proto"],
            envelope_id=data["envelope_id"],
            timestamp_utc=data["timestamp_utc"],
            sender=data["sender"],
            recipient=data["recipient"],
            mandate_id=data["mandate_id"],
            intent=data["intent"],
            linear=LinearContext(**data["linear"]),
            git=GitCoordinates(**data["git"]),
            payload=data["payload"],
            attestation=data["attestation"]
        )

    @staticmethod
    def format_receipt_comment(receipt: ProtocolPhiReceipt) -> str:
        """Serializes receipt into sealed markdown comment format for Linear."""
        data = {
            "receipt_id": receipt.receipt_id,
            "parent_envelope_id": receipt.parent_envelope_id,
            "timestamp_utc": receipt.timestamp_utc,
            "reviewer": receipt.reviewer,
            "verdict": receipt.verdict,
            "checks": receipt.checks,
            "evidence_hash": receipt.evidence_hash,
            "attestation": receipt.attestation,
            "rework_details": receipt.rework_details
        }
        json_blob = json.dumps(data, indent=2, sort_keys=True)
        return (
            f"<!-- phi-receipt:{receipt.receipt_id} -->\n"
            f"```json\n{json_blob}\n```\n"
            f"<!-- /phi-receipt:{receipt.receipt_id} -->"
        )

    @staticmethod
    def parse_receipt_comment(comment_text: str) -> Optional[ProtocolPhiReceipt]:
        """Extracts and parses sealed Protocol Phi receipt from Linear comment text."""
        pattern = r"<!-- phi-receipt:(rcpt_[a-zA-Z0-9_-]+) -->\s*```json\s*(.*?)\s*```\s*<!-- /phi-receipt:\1 -->"
        match = re.search(pattern, comment_text, re.DOTALL)
        if not match:
            return None

        raw_json = match.group(2)
        data = json.loads(raw_json)

        return ProtocolPhiReceipt(
            receipt_id=data["receipt_id"],
            parent_envelope_id=data["parent_envelope_id"],
            timestamp_utc=data["timestamp_utc"],
            reviewer=data["reviewer"],
            verdict=data["verdict"],
            checks=data["checks"],
            evidence_hash=data["evidence_hash"],
            attestation=data["attestation"],
            rework_details=data.get("rework_details")
        )
