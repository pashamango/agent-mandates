#!/usr/bin/env python3
"""
Test suite for MIN-125 Survivable Security Architecture.
Validates the Phalanx Capsule, all 8 State Machine Gates, Quarantined Bootstrap Invariant,
Evidence Accounting, and all 5 Tabletop Failure/Adversary Scenarios.
Zero-dependency: works with standard python3 -m unittest AND pytest.
"""

import os
import sys
import time
import unittest

# Ensure repo root is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from min125.recovery_simulation import (
    RecoveryCapsule,
    RecoveryStateMachine,
    RecoveryState,
    sha256_hex,
    run_scenario_1_loss,
    run_scenario_2_theft,
    run_scenario_3_coercion,
    run_scenario_4_replay,
    run_scenario_5_partial_destruction,
)


class TestSurvivableSecurityRecovery(unittest.TestCase):
    def setUp(self):
        self.capsule = RecoveryCapsule.generate_synthetic()
        self.witness_did = "did:key:z6MkuWitnessTrusteeNodeSurvivable2026"
        self.fsm = RecoveryStateMachine(
            expected_capsule=self.capsule,
            witness_did=self.witness_did,
        )

    def test_capsule_integrity_and_repair(self):
        self.assertTrue(self.capsule.verify_integrity())
        # Test 25% degradation repair
        damaged = self.capsule.derivation_seed[:24] + "ABCD"
        repaired = self.capsule.repair_ecc(damaged)
        self.assertIsNotNone(repaired)
        self.assertTrue(repaired.verify_integrity())
        self.assertEqual(repaired.derivation_seed, self.capsule.derivation_seed)

        # Test catastrophic corruption failure
        fatal = "ABCD" + self.capsule.derivation_seed[4:]
        self.assertIsNone(self.capsule.repair_ecc(fatal))

    def test_full_recovery_happy_path(self):
        # Gate 1: Present capsule
        ev1 = self.fsm.gate1_present_capsule(self.capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")
        self.assertEqual(self.fsm.state, RecoveryState.INITIATED)
        self.assertEqual(ev1.receipt_type, "EV_CAPSULE_PRESENTED")
        self.assertTrue(ev1.payload["cue_verified"])

        # Gate 2: Liveness
        now = time.time()
        nonce = "test-nonce-001"
        sig = sha256_hex(f"test-chal:{nonce}:{self.fsm.active_request_id}")
        ev2 = self.fsm.gate2_confirm_liveness("test-chal", sig, now, nonce)
        self.assertEqual(self.fsm.state, RecoveryState.LIVENESS_CONFIRMED)
        self.assertEqual(ev2.receipt_type, "EV_LIVENESS_CONFIRMED")

        # Gate 3: Witness Quorum
        w_sig = sha256_hex(f"witness-approval:{self.fsm.witness_did}:{self.fsm.active_request_id}")
        ev3 = self.fsm.gate3_witness_attestation(self.fsm.witness_did, True, w_sig)
        self.assertEqual(self.fsm.state, RecoveryState.WITNESSED)
        self.assertEqual(ev3.receipt_type, "EV_QUORUM_ATTESTATION")

        # Gate 4: Mandatory Time Delay (simulate 10s for test)
        ev4 = self.fsm.gate4_commit_timelock(delay_seconds=10.0)
        self.assertEqual(self.fsm.state, RecoveryState.TIMELOCKED)
        self.assertEqual(ev4.receipt_type, "EV_TIMELOCK_COMMITTED")

        # Gate 5: Freshness & Revocation Check (after simulated unlock)
        future_time = self.fsm.timelock_unlock_time + 1.0
        ev5 = self.fsm.gate5_verify_freshness(current_time=future_time)
        self.assertEqual(self.fsm.state, RecoveryState.FRESHNESS_VERIFIED)
        self.assertEqual(ev5.receipt_type, "EV_FRESHNESS_VERIFIED")

        # Gate 6: Quarantined Bounded Bootstrap
        ev6 = self.fsm.gate6_activate_bounded_bootstrap()
        self.assertEqual(self.fsm.state, RecoveryState.BOOTSTRAP_ACTIVE)
        self.assertEqual(ev6.receipt_type, "EV_BOOTSTRAP_PROVISIONED")
        self.assertEqual(self.fsm.current_epoch, 2)
        self.assertTrue(self.fsm.bootstrap_active)

        # Verify CORE INVARIANT: Bootstrap CANNOT access legacy vault
        with self.assertRaises(PermissionError) as exc_info:
            self.fsm.access_legacy_vault()
        self.assertIn("Bootstrap epoch is denied access to legacy vault data", str(exc_info.exception))

        # Gate 7: Audit Log
        ev7 = self.fsm.gate7_commit_audit_log()
        self.assertEqual(self.fsm.state, RecoveryState.AUDIT_LOGGED)
        self.assertEqual(ev7.receipt_type, "EV_AUDIT_LOG_COMMITTED")
        self.assertEqual(len(self.fsm.evidence_log), 7)

        # Gate 8: Post-Recovery Key Rotation
        new_keys = {"master_root_2026": "did:key:z6MkuNewRootKey..."}
        ev8 = self.fsm.gate8_rotate_and_seal(new_keys)
        self.assertEqual(self.fsm.state, RecoveryState.ROTATED_AND_OPERATIONAL)
        self.assertEqual(ev8.receipt_type, "EV_KEYS_ROTATED")
        self.assertTrue(self.fsm.capsule_burned)
        self.assertFalse(self.fsm.bootstrap_active)

    def test_gate1_wrong_cue_rejection(self):
        with self.assertRaises(ValueError) as exc:
            self.fsm.gate1_present_capsule(self.capsule, recognition_cue="INCORRECT-CUE")
        self.assertIn("recognition cue mismatch", str(exc.exception))
        self.assertEqual(self.fsm.state, RecoveryState.ABORTED)

    def test_gate2_replay_rejection(self):
        self.fsm.gate1_present_capsule(self.capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")
        now = time.time()
        nonce = "replayed-nonce"
        sig = sha256_hex(f"chal:{nonce}:{self.fsm.active_request_id}")
        self.fsm.gate2_confirm_liveness("chal", sig, now, nonce)

        # Replay the same nonce in a new recovery session
        fsm2 = RecoveryStateMachine(expected_capsule=self.capsule, witness_did=self.witness_did)
        fsm2.seen_nonces = self.fsm.seen_nonces
        fsm2.gate1_present_capsule(self.capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")
        with self.assertRaises(ValueError) as exc:
            fsm2.gate2_confirm_liveness("chal", sig, now, nonce)
        self.assertIn("replay detected", str(exc.exception))
        self.assertEqual(fsm2.state, RecoveryState.ABORTED)

    def test_gate3_witness_rejection(self):
        self.fsm.gate1_present_capsule(self.capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")
        now = time.time()
        nonce = "nonce-g3"
        sig = sha256_hex(f"chal:{nonce}:{self.fsm.active_request_id}")
        self.fsm.gate2_confirm_liveness("chal", sig, now, nonce)

        # Unauthorized witness attempt
        with self.assertRaises(ValueError) as exc:
            self.fsm.gate3_witness_attestation("did:key:impostorWitness", True, "invalid-sig")
        self.assertIn("does not match registered trustee", str(exc.exception))

        # Reset and test witness disapproval
        fsm2 = RecoveryStateMachine(self.capsule, witness_did="did:key:witness")
        fsm2.gate1_present_capsule(self.capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")
        sig2 = sha256_hex(f"chal:{nonce}:{fsm2.active_request_id}")
        fsm2.gate2_confirm_liveness("chal", sig2, now, nonce)
        with self.assertRaises(ValueError) as exc:
            fsm2.gate3_witness_attestation("did:key:witness", False, "")
        self.assertIn("rejected recovery attestation", str(exc.exception))

    def test_killswitch_cancellation_during_timelock(self):
        self.fsm.gate1_present_capsule(self.capsule, recognition_cue="RIVER-GRANITE-OBSIDIAN")
        now = time.time()
        nonce = "nonce-ks"
        sig = sha256_hex(f"chal:{nonce}:{self.fsm.active_request_id}")
        self.fsm.gate2_confirm_liveness("chal", sig, now, nonce)
        w_sig = sha256_hex(f"witness-approval:{self.fsm.witness_did}:{self.fsm.active_request_id}")
        self.fsm.gate3_witness_attestation(self.fsm.witness_did, True, w_sig)
        self.fsm.gate4_commit_timelock(delay_seconds=100.0)

        # Emergency killswitch fired
        ks_sig = sha256_hex(f"killswitch:did:key:trustee:{self.fsm.active_request_id}")
        self.fsm.trigger_killswitch("did:key:trustee", "COERCION_ATTACK", ks_sig)
        self.assertEqual(self.fsm.state, RecoveryState.ABORTED)

        # Advancing time does not allow continuation
        with self.assertRaises(ValueError) as exc:
            self.fsm.gate5_verify_freshness(current_time=now + 500.0)
        self.assertIn("Cannot verify freshness in state", str(exc.exception))
        self.assertEqual(self.fsm.state, RecoveryState.ABORTED)

    def test_tabletop_all_scenarios(self):
        s1 = run_scenario_1_loss()
        self.assertEqual(s1["verdict"], "PASS")
        self.assertFalse(s1["legacy_exposed"])

        s2 = run_scenario_2_theft()
        self.assertEqual(s2["verdict"], "PASS")
        self.assertFalse(s2["legacy_exposed"])

        s3 = run_scenario_3_coercion()
        self.assertEqual(s3["verdict"], "PASS")
        self.assertFalse(s3["legacy_exposed"])

        s4 = run_scenario_4_replay()
        self.assertEqual(s4["verdict"], "PASS")
        self.assertFalse(s4["legacy_exposed"])

        s5 = run_scenario_5_partial_destruction()
        self.assertEqual(s5["verdict"], "PASS")
        self.assertTrue(s5["ecc_repair_successful"])


if __name__ == "__main__":
    unittest.main()
