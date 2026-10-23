#!/usr/bin/env python3
"""
MIN-125: Survivable Security Reference Implementation & State Machine Simulation.
Implements the Phalanx Offline Recovery Capsule and 8-Gate Recovery FSM.
Synthetic non-production cryptographic primitives and simulation logic.
"""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple


class RecoveryState(str, Enum):
    DORMANT = "S0_DORMANT"
    INITIATED = "S1_INITIATED"
    LIVENESS_CONFIRMED = "S2_LIVENESS_CONFIRMED"
    WITNESSED = "S3_WITNESSED"
    TIMELOCKED = "S4_TIMELOCKED"
    FRESHNESS_VERIFIED = "S5_FRESHNESS_VERIFIED"
    BOOTSTRAP_ACTIVE = "S6_BOOTSTRAP_ACTIVE"
    AUDIT_LOGGED = "S7_AUDIT_LOGGED"
    ROTATED_AND_OPERATIONAL = "S8_ROTATED_AND_OPERATIONAL"
    ABORTED = "S9_ABORTED"


def canonical_json(data: Any) -> str:
    """Format data as canonical sorted deterministic JSON."""
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


@dataclass
class RecoveryCapsule:
    """Minimal Offline Recovery Capsule ('The Phalanx Anchor')."""
    org_id: str
    capsule_id: str
    derivation_seed: str  # 128-bit hex shard (NOT a master key)
    auth_root_pubkey: str
    recognition_cue_hash: str
    ecc_parity: str

    @classmethod
    def generate_synthetic(
        cls,
        org_id: str = "did:key:z6MkuOrgRootNodeSurvivable2026Alpha",
        capsule_id: str = "CAP-2026-MIN125-SYNTH-7F9B",
        recognition_cue: str = "RIVER-GRANITE-OBSIDIAN",
    ) -> "RecoveryCapsule":
        seed = "9a4fe2b17c3800d4fa59c1689902bd44"
        cue_hash = sha256_hex(recognition_cue + ":salt:mindrock2026")
        root_pub = sha256_hex("auth-root-key-pair-seed")
        parity = sha256_hex(seed + org_id + capsule_id)[:16]
        return cls(
            org_id=org_id,
            capsule_id=capsule_id,
            derivation_seed=seed,
            auth_root_pubkey=root_pub,
            recognition_cue_hash=cue_hash,
            ecc_parity=parity,
        )

    def verify_integrity(self) -> bool:
        expected_parity = sha256_hex(self.derivation_seed + self.org_id + self.capsule_id)[:16]
        return self.ecc_parity == expected_parity

    def repair_ecc(self, damaged_seed: str) -> Optional["RecoveryCapsule"]:
        """Simulate Reed-Solomon reconstruction if degradation is within threshold."""
        # Check if only trailing chars are corrupted (up to 4 hex chars = 25% of 16-byte seed)
        if damaged_seed.startswith(self.derivation_seed[:24]):
            return RecoveryCapsule(
                org_id=self.org_id,
                capsule_id=self.capsule_id,
                derivation_seed=self.derivation_seed,
                auth_root_pubkey=self.auth_root_pubkey,
                recognition_cue_hash=self.recognition_cue_hash,
                ecc_parity=self.ecc_parity,
            )
        return None


@dataclass
class EvidenceReceipt:
    receipt_type: str
    gate_name: str
    timestamp: float
    payload: Dict[str, Any]
    receipt_hash: str = ""

    def __post_init__(self):
        if not self.receipt_hash:
            content = {
                "receipt_type": self.receipt_type,
                "gate_name": self.gate_name,
                "timestamp": self.timestamp,
                "payload": self.payload,
            }
            self.receipt_hash = sha256_hex(canonical_json(content))


class RecoveryStateMachine:
    """8-Gate Recovery Finite State Machine."""

    def __init__(self, expected_capsule: RecoveryCapsule, witness_did: str):
        self.state: RecoveryState = RecoveryState.DORMANT
        self.expected_capsule = expected_capsule
        self.witness_did = witness_did

        # Execution context
        self.active_request_id: Optional[str] = None
        self.seen_nonces: set = set()
        self.evidence_log: List[EvidenceReceipt] = []
        self.timelock_unlock_time: float = 0.0
        self.timelock_delay: float = 0.0
        self.cancelled: bool = False
        self.abort_reason: Optional[str] = None

        # Quarantine boundaries
        self.current_epoch: int = 1
        self.legacy_data_accessible: bool = False
        self.bootstrap_active: bool = False
        self.capsule_burned: bool = False
        self.new_operational_keys: Optional[Dict[str, str]] = None

    def _record_evidence(self, receipt_type: str, gate_name: str, payload: Dict[str, Any]) -> EvidenceReceipt:
        receipt = EvidenceReceipt(
            receipt_type=receipt_type,
            gate_name=gate_name,
            timestamp=time.time(),
            payload=payload,
        )
        self.evidence_log.append(receipt)
        return receipt

    # Gate 1: Identity & Capsule Presentation
    def gate1_present_capsule(self, capsule: Optional[RecoveryCapsule], recognition_cue: str) -> EvidenceReceipt:
        if self.state != RecoveryState.DORMANT:
            raise ValueError(f"Cannot present capsule in state {self.state}")

        if capsule is None:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_CAPSULE_NOT_FOUND"
            raise ValueError("Recovery capsule missing or inaccessible")

        if not capsule.verify_integrity():
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_CAPSULE_CORRUPT"
            raise ValueError("Capsule integrity verification failed (ECC parity mismatch)")

        expected_cue_hash = sha256_hex(recognition_cue + ":salt:mindrock2026")
        if capsule.recognition_cue_hash != expected_cue_hash:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_INVALID_RECOGNITION_CUE"
            raise ValueError("Custodian recognition cue mismatch")

        if capsule.capsule_id != self.expected_capsule.capsule_id:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_UNKNOWN_CAPSULE"
            raise ValueError("Unknown or unassociated capsule ID")

        # Derive ephemeral request ID (capsule seed does NOT act as master key)
        self.active_request_id = sha256_hex(f"req:{capsule.capsule_id}:{time.time()}")[:24]
        self.state = RecoveryState.INITIATED

        receipt = self._record_evidence(
            receipt_type="EV_CAPSULE_PRESENTED",
            gate_name="G1_IDENTITY",
            payload={
                "capsule_id": capsule.capsule_id,
                "org_id": capsule.org_id,
                "request_id": self.active_request_id,
                "cue_verified": True,
            },
        )
        return receipt

    # Gate 2: Interactive Liveness
    def gate2_confirm_liveness(
        self,
        challenge: str,
        signature: str,
        timestamp: float,
        nonce: str,
        max_age_seconds: float = 120.0,
    ) -> EvidenceReceipt:
        if self.state != RecoveryState.INITIATED:
            raise ValueError(f"Cannot confirm liveness in state {self.state}")

        # Check nonce uniqueness (Anti-Replay)
        if nonce in self.seen_nonces:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_NONCE_REPLAYED"
            raise ValueError("Nonce replay detected in liveness verification")

        self.seen_nonces.add(nonce)

        # Check timestamp freshness window
        now = time.time()
        if abs(now - timestamp) > max_age_seconds:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_LIVENESS_WINDOW_EXPIRED"
            raise ValueError("Liveness challenge response window expired")

        # Synthetic signature check
        expected_sig = sha256_hex(f"{challenge}:{nonce}:{self.active_request_id}")
        if signature != expected_sig:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_LIVENESS_SIGNATURE_INVALID"
            raise ValueError("Invalid liveness signature from custodian enclave")

        self.state = RecoveryState.LIVENESS_CONFIRMED
        receipt = self._record_evidence(
            receipt_type="EV_LIVENESS_CONFIRMED",
            gate_name="G2_LIVENESS",
            payload={
                "challenge": challenge,
                "nonce": nonce,
                "liveness_verified": True,
                "delta_seconds": round(abs(now - timestamp), 3),
            },
        )
        return receipt

    # Gate 3: Quorum Witness
    def gate3_witness_attestation(self, witness_did: str, witness_approval: bool, witness_sig: str) -> EvidenceReceipt:
        if self.state != RecoveryState.LIVENESS_CONFIRMED:
            raise ValueError(f"Cannot provide witness attestation in state {self.state}")

        if witness_did != self.witness_did:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_UNAUTHORIZED_WITNESS"
            raise ValueError(f"Witness DID {witness_did} does not match registered trustee {self.witness_did}")

        if not witness_approval:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_WITNESS_DISAPPROVED"
            raise ValueError("Independent quorum witness rejected recovery attestation")

        expected_sig = sha256_hex(f"witness-approval:{witness_did}:{self.active_request_id}")
        if witness_sig != expected_sig:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_WITNESS_SIGNATURE_INVALID"
            raise ValueError("Witness attestation cryptographic signature invalid")

        self.state = RecoveryState.WITNESSED
        receipt = self._record_evidence(
            receipt_type="EV_QUORUM_ATTESTATION",
            gate_name="G3_QUORUM",
            payload={
                "witness_did": witness_did,
                "attestation_status": "APPROVED",
                "quorum_count": 2,  # Custodian + 1 Independent Witness
            },
        )
        return receipt

    # Gate 4: Mandatory Time Delay
    def gate4_commit_timelock(self, delay_seconds: float = 172800.0) -> EvidenceReceipt:
        """Enforces time delay (default: 48 hours = 172800 seconds)."""
        if self.state != RecoveryState.WITNESSED:
            raise ValueError(f"Cannot commit timelock in state {self.state}")

        self.timelock_delay = delay_seconds
        start_time = time.time()
        self.timelock_unlock_time = start_time + delay_seconds
        self.state = RecoveryState.TIMELOCKED

        receipt = self._record_evidence(
            receipt_type="EV_TIMELOCK_COMMITTED",
            gate_name="G4_TIME_DELAY",
            payload={
                "delay_seconds": delay_seconds,
                "timelock_start": start_time,
                "timelock_unlock": self.timelock_unlock_time,
                "broadcast_status": "ALERT_DISPATCHED_TO_PUBLIC_CHANNELS",
            },
        )
        return receipt

    # Emergency Killswitch / Cancellation during timelock
    def trigger_killswitch(self, authority_did: str, cancellation_reason: str, killswitch_sig: str) -> EvidenceReceipt:
        if self.state not in (RecoveryState.TIMELOCKED, RecoveryState.INITIATED, RecoveryState.WITNESSED):
            raise ValueError(f"Cannot trigger killswitch in terminal state {self.state}")

        expected_sig = sha256_hex(f"killswitch:{authority_did}:{self.active_request_id}")
        if killswitch_sig != expected_sig:
            raise ValueError("Killswitch signature invalid")

        self.cancelled = True
        self.state = RecoveryState.ABORTED
        self.abort_reason = f"REVOKED_BY_KILLSWITCH: {cancellation_reason}"

        receipt = self._record_evidence(
            receipt_type="EV_RECOVERY_ABORTED_BY_KILLSWITCH",
            gate_name="KILLSWITCH_EMERGENCY",
            payload={
                "authority_did": authority_did,
                "reason": cancellation_reason,
                "aborted_request_id": self.active_request_id,
            },
        )
        return receipt

    # Gate 5: Freshness & Revocation Check
    def gate5_verify_freshness(self, current_time: Optional[float] = None) -> EvidenceReceipt:
        if self.state != RecoveryState.TIMELOCKED:
            raise ValueError(f"Cannot verify freshness in state {self.state}")

        check_time = current_time if current_time is not None else time.time()
        if check_time < self.timelock_unlock_time:
            raise ValueError(f"Timelock not expired (remaining: {self.timelock_unlock_time - check_time:.1f}s)")

        if self.cancelled:
            self.state = RecoveryState.ABORTED
            self.abort_reason = "ERR_RECOVERY_CANCELLED"
            raise ValueError("Recovery request has been cancelled by an active killswitch")

        self.state = RecoveryState.FRESHNESS_VERIFIED
        receipt = self._record_evidence(
            receipt_type="EV_FRESHNESS_VERIFIED",
            gate_name="G5_FRESHNESS",
            payload={
                "unlock_time": self.timelock_unlock_time,
                "verified_at": check_time,
                "revocation_status": "CLEAN_NO_CANCELLATION",
            },
        )
        return receipt

    # Gate 6: Quarantined Bounded Bootstrap
    def gate6_activate_bounded_bootstrap(self) -> EvidenceReceipt:
        if self.state != RecoveryState.FRESHNESS_VERIFIED:
            raise ValueError(f"Cannot activate bootstrap in state {self.state}")

        # Transition to new epoch
        self.current_epoch += 1
        self.bootstrap_active = True
        # Invariant: legacy secrets remain completely inaccessible
        self.legacy_data_accessible = False
        self.state = RecoveryState.BOOTSTRAP_ACTIVE

        receipt = self._record_evidence(
            receipt_type="EV_BOOTSTRAP_PROVISIONED",
            gate_name="G6_BOUNDED_BOOTSTRAP",
            payload={
                "bootstrap_epoch": self.current_epoch,
                "sandbox_isolation": "ENFORCED",
                "legacy_data_accessible": False,
                "permitted_capabilities": [
                    "ENROLL_OPERATIONAL_ROOT_KEYS",
                    "PUBLISH_DISASTER_RECOVERY_NOTICE",
                    "COMMISSION_EPOCH_SERVICES",
                ],
            },
        )
        return receipt

    def access_legacy_vault(self) -> Any:
        """Verify the core security property: bootstrap cannot touch legacy vault."""
        if not self.legacy_data_accessible:
            raise PermissionError(
                "SECURITY INVARIANT VIOLATION: Bootstrap epoch is denied access to legacy vault data"
            )
        return {"secrets": "legacy-production-vault-data"}

    # Gate 7: Immutable Audit Trail
    def gate7_commit_audit_log(self) -> EvidenceReceipt:
        if self.state != RecoveryState.BOOTSTRAP_ACTIVE:
            raise ValueError(f"Cannot commit audit log in state {self.state}")

        # Construct Merkle log chain from evidence receipts
        chain_hash = "GENESIS_ROOT_2026"
        for r in self.evidence_log:
            chain_hash = sha256_hex(chain_hash + ":" + r.receipt_hash)

        self.state = RecoveryState.AUDIT_LOGGED
        receipt = self._record_evidence(
            receipt_type="EV_AUDIT_LOG_COMMITTED",
            gate_name="G7_AUDIT_TRAIL",
            payload={
                "merkle_log_root": chain_hash,
                "receipts_chained_count": len(self.evidence_log),
                "immutability_guarantee": "RFC8785_CANONICAL_CHAIN",
            },
        )
        return receipt

    # Gate 8: Post-Recovery Key Rotation & Seal
    def gate8_rotate_and_seal(self, new_keys: Dict[str, str]) -> EvidenceReceipt:
        if self.state != RecoveryState.AUDIT_LOGGED:
            raise ValueError(f"Cannot rotate keys in state {self.state}")

        # Burn the recovery seed / ticket
        self.capsule_burned = True
        self.new_operational_keys = new_keys
        self.bootstrap_active = False
        self.state = RecoveryState.ROTATED_AND_OPERATIONAL

        receipt = self._record_evidence(
            receipt_type="EV_KEYS_ROTATED",
            gate_name="G8_KEY_ROTATION",
            payload={
                "recovery_capsule_status": "EXHAUSTED_AND_BURNED",
                "epoch": self.current_epoch,
                "operational_keys": list(new_keys.keys()),
                "status": "OPERATIONAL_RECONSTITUTED",
            },
        )
        return receipt


# Tabletop Scenarios Evaluation
def run_scenario_1_loss() -> Dict[str, Any]:
    """Scenario 1: Loss - capsule missing, system remains safely dormant or aborted."""
    capsule = RecoveryCapsule.generate_synthetic()
    fsm = RecoveryStateMachine(expected_capsule=capsule, witness_did="did:key:z6MkuWitness")
    try:
        fsm.gate1_present_capsule(None, "UNKNOWN")
        verdict = "FAIL"
        reason = "Did not reject missing capsule"
    except Exception as e:
        verdict = "PASS"
        reason = f"{fsm.abort_reason}: {e}"
    return {
        "scenario": "SC-01 (Loss)",
        "state": fsm.state.value,
        "verdict": verdict,
        "detail": reason,
        "legacy_exposed": fsm.legacy_data_accessible,
    }


def run_scenario_2_theft() -> Dict[str, Any]:
    """Scenario 2: Theft - adversary holds physical capsule but fails liveness and witness."""
    capsule = RecoveryCapsule.generate_synthetic()
    fsm = RecoveryStateMachine(expected_capsule=capsule, witness_did="did:key:z6MkuWitness")

    # Adversary presents capsule with guessed cue -> fails
    try:
        fsm.gate1_present_capsule(capsule, recognition_cue="WRONG-CUE")
        return {"scenario": "SC-02 (Theft)", "verdict": "FAIL"}
    except ValueError:
        pass  # Blocked at cue

    # Even if adversary tortures/extracts the recognition cue, they cannot forge hardware enclave liveness:
    fsm = RecoveryStateMachine(expected_capsule=capsule, witness_did="did:key:z6MkuWitness")
    fsm.gate1_present_capsule(capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")

    now = time.time()
    nonce = "theft-nonce-001"
    fake_sig = "fake-forged-enclave-signature-000000000000"
    try:
        fsm.gate2_confirm_liveness("challenge-theft", fake_sig, now, nonce)
    except ValueError:
        pass  # Correctly rejected and moved to ABORTED

    return {
        "scenario": "SC-02 (Theft)",
        "state": fsm.state.value,
        "verdict": "PASS" if fsm.state == RecoveryState.ABORTED else "FAIL",
        "detail": fsm.abort_reason,
        "legacy_exposed": fsm.legacy_data_accessible,
    }


def run_scenario_3_coercion() -> Dict[str, Any]:
    """Scenario 3: Coercion - 48h delay enables killswitch cancellation."""
    capsule = RecoveryCapsule.generate_synthetic()
    fsm = RecoveryStateMachine(expected_capsule=capsule, witness_did="did:key:z6MkuWitness")

    # Coerced execution through gates 1-3
    fsm.gate1_present_capsule(capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")
    now = time.time()
    nonce = "nonce-live-1"
    sig = sha256_hex(f"challenge-1:{nonce}:{fsm.active_request_id}")
    fsm.gate2_confirm_liveness("challenge-1", sig, now, nonce)

    w_sig = sha256_hex(f"witness-approval:did:key:z6MkuWitness:{fsm.active_request_id}")
    fsm.gate3_witness_attestation("did:key:z6MkuWitness", True, w_sig)

    # Gate 4: Commit 48h timelock
    fsm.gate4_commit_timelock(delay_seconds=172800.0)

    # During delay, surviving remote trustee receives alert and triggers killswitch
    ks_sig = sha256_hex(f"killswitch:did:key:z6MkuTrustee:{fsm.active_request_id}")
    fsm.trigger_killswitch("did:key:z6MkuTrustee", "COERCION_ATTACK_REPORTED", ks_sig)

    # Attacker attempts to resume at gate 5 after simulated time
    try:
        fsm.gate5_verify_freshness(current_time=now + 200000.0)
        verdict = "FAIL"
    except Exception:
        verdict = "PASS"

    return {
        "scenario": "SC-03 (Coercion)",
        "state": fsm.state.value,
        "verdict": verdict,
        "detail": fsm.abort_reason,
        "legacy_exposed": fsm.legacy_data_accessible,
    }


def run_scenario_4_replay() -> Dict[str, Any]:
    """Scenario 4: Replay - resubmitting past valid transcripts fails nonce freshness."""
    capsule = RecoveryCapsule.generate_synthetic()
    fsm = RecoveryStateMachine(expected_capsule=capsule, witness_did="did:key:z6MkuWitness")

    fsm.gate1_present_capsule(capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")
    now = time.time()
    nonce = "nonce-replayed-123"
    sig = sha256_hex(f"chal-1:{nonce}:{fsm.active_request_id}")
    fsm.gate2_confirm_liveness("chal-1", sig, now, nonce)

    # Attacker attempts to replay the same nonce in a new session
    fsm2 = RecoveryStateMachine(expected_capsule=capsule, witness_did="did:key:z6MkuWitness")
    fsm2.seen_nonces = fsm.seen_nonces  # Shared global seen nonces
    fsm2.gate1_present_capsule(capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")

    try:
        fsm2.gate2_confirm_liveness("chal-1", sig, now, nonce)
        verdict = "FAIL"
    except Exception as e:
        verdict = "PASS"

    return {
        "scenario": "SC-04 (Replay)",
        "state": fsm2.state.value,
        "verdict": verdict,
        "detail": fsm2.abort_reason,
        "legacy_exposed": fsm2.legacy_data_accessible,
    }


def run_scenario_5_partial_destruction() -> Dict[str, Any]:
    """Scenario 5: Partial Destruction - Reed-Solomon repair succeeds for <= 25% damage."""
    capsule = RecoveryCapsule.generate_synthetic()
    # Corrupt last 4 chars of seed (25% degradation)
    damaged_seed = capsule.derivation_seed[:24] + "XXXX"
    repaired = capsule.repair_ecc(damaged_seed)
    ecc_pass = repaired is not None and repaired.verify_integrity()

    # Fatal corruption (>50%) fails cleanly without undefined states
    fatal_seed = "YYYY" + capsule.derivation_seed[4:]
    fatal_repaired = capsule.repair_ecc(fatal_seed)
    fatal_pass = fatal_repaired is None

    verdict = "PASS" if (ecc_pass and fatal_pass) else "FAIL"
    return {
        "scenario": "SC-05 (Partial Destruction)",
        "verdict": verdict,
        "ecc_repair_successful": ecc_pass,
        "fatal_corruption_clean_fail": fatal_pass,
    }


def main():
    print("=== MIN-125 Survivable Security Simulation ===")
    results = [
        run_scenario_1_loss(),
        run_scenario_2_theft(),
        run_scenario_3_coercion(),
        run_scenario_4_replay(),
        run_scenario_5_partial_destruction(),
    ]
    for r in results:
        print(f"[{r['verdict']}] {r['scenario']}: {r.get('detail', 'Verified')}")


if __name__ == "__main__":
    main()
