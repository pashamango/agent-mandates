"""
Unit tests for Protocol Phi + xHTTP Fleet Interaction Engine (MIN-188)
"""

import dataclasses
import sys
import unittest
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import ssi
from protocol_phi import (
    FleetProtocolEngine,
    GitCoordinates,
    LinearContext,
    ProtocolPhiEnvelope,
    ProtocolPhiReceipt
)


class TestProtocolPhiEngine(unittest.TestCase):

    def setUp(self):
        self.did_agy, self.priv_agy, self.pub_agy = ssi.generate_identity()
        self.did_codex, self.priv_codex, self.pub_codex = ssi.generate_identity()

        self.valid_git = GitCoordinates(
            repo_url="https://github.com/pashamango/agent-mandates.git",
            branch="minutta/min-188-spec-protocol-phi-xhttp-standart-vzaimodeistviya-ahentov",
            commit_sha="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
            tree_sha="11223344556677889900aabbccddeeff00112233",
            manifest_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )
        self.linear_ctx = LinearContext(
            ticket_id="MIN-188",
            ticket_uuid="38356400-fa91-4ffe-8c04-86fe3f7f15d8",
            current_status="In Progress",
            target_status="In Review"
        )

    def test_git_coordinates_validation_success(self):
        self.valid_git.validate()

    def test_git_coordinates_reject_local_path(self):
        invalid_git = GitCoordinates(
            repo_url="/Users/pavelcherkashin/ChitAkasha-Wolfram",
            branch="main",
            commit_sha="a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
        )
        with self.assertRaises(ValueError) as ctx:
            invalid_git.validate()
        self.assertIn("Must be remote URL", str(ctx.exception))

    def test_git_coordinates_reject_invalid_sha(self):
        invalid_git = GitCoordinates(
            repo_url="https://github.com/pashamango/agent-mandates.git",
            branch="main",
            commit_sha="not-a-40-char-hex"
        )
        with self.assertRaises(ValueError) as ctx:
            invalid_git.validate()
        self.assertIn("40-character hex", str(ctx.exception))

    def test_envelope_creation_and_validation(self):
        envelope = FleetProtocolEngine.create_envelope(
            envelope_id="phi_env_0e7f9fffbb35",
            sender=f"{self.did_agy}#agy@wolfram",
            recipient=f"{self.did_codex}#codex@wolfram",
            mandate_id="mandate_c847d10e9f1a",
            intent="HANDOFF_FOR_QA",
            linear=self.linear_ctx,
            git=self.valid_git,
            payload={"summary": "Task complete, ready for QA"}
        )
        self.assertEqual(envelope.proto, "phi-0.1")
        self.assertEqual(envelope.envelope_id, "phi_env_0e7f9fffbb35")
        self.assertEqual(envelope.git.commit_sha, "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0")

    def test_envelope_rejects_truncated_signature(self):
        # Remark 8: reject dummy truncated signature like "3a7b9c...f8e2"
        with self.assertRaises(ValueError) as ctx:
            FleetProtocolEngine.create_envelope(
                envelope_id="phi_env_0e7f9fffbb35",
                sender=f"{self.did_agy}#agy@wolfram",
                recipient=f"{self.did_codex}#codex@wolfram",
                mandate_id="mandate_c847d10e9f1a",
                intent="HANDOFF_FOR_QA",
                linear=self.linear_ctx,
                git=self.valid_git,
                payload={},
                signature="3a7b9c...f8e2"
            )
        self.assertIn("Ed25519 signature must be 128 hex chars", str(ctx.exception))

    def test_envelope_real_ed25519_signing_and_verification(self):
        env = FleetProtocolEngine.create_envelope(
            envelope_id="phi_env_0e7f9fffbb35",
            sender=f"{self.did_agy}#agy@wolfram",
            recipient=f"{self.did_codex}#codex@wolfram",
            mandate_id="mandate_c847d10e9f1a",
            intent="HANDOFF_FOR_QA",
            linear=self.linear_ctx,
            git=self.valid_git,
            payload={"task": "MIN-188 Zero-Trust Handoff"}
        )
        signed_env = FleetProtocolEngine.sign_envelope(env, self.priv_agy.hex())
        self.assertEqual(len(signed_env.attestation["signature"]), 128)
        self.assertTrue(FleetProtocolEngine.verify_envelope_signature(signed_env))

        # Tampered envelope fails verification
        tampered_env = dataclasses.replace(signed_env, mandate_id="tampered_mandate")
        self.assertFalse(FleetProtocolEngine.verify_envelope_signature(tampered_env))

    def test_envelope_rejects_self_handoff(self):
        with self.assertRaises(ValueError) as ctx:
            FleetProtocolEngine.create_envelope(
                envelope_id="phi_env_0e7f9fffbb35",
                sender=f"{self.did_agy}#agy@wolfram",
                recipient=f"{self.did_agy}#agy@wolfram",
                mandate_id="mandate_c847d10e9f1a",
                intent="HANDOFF_FOR_QA",
                linear=self.linear_ctx,
                git=self.valid_git,
                payload={}
            )
        self.assertIn("cannot be identical", str(ctx.exception))

    def test_sealed_comment_markdown_roundtrip(self):
        envelope = FleetProtocolEngine.create_envelope(
            envelope_id="phi_env_roundtrip001",
            sender=f"{self.did_agy}#agy@wolfram",
            recipient=f"{self.did_codex}#codex@wolfram",
            mandate_id="mandate_c847d10e9f1a",
            intent="HANDOFF_FOR_QA",
            linear=self.linear_ctx,
            git=self.valid_git,
            payload={"test_key": "test_val"}
        )
        signed_env = FleetProtocolEngine.sign_envelope(envelope, self.priv_agy.hex())
        comment_md = FleetProtocolEngine.format_envelope_comment(signed_env)
        self.assertIn("<!-- phi-envelope:phi_env_roundtrip001 -->", comment_md)

        parsed_env = FleetProtocolEngine.parse_envelope_comment(comment_md)
        self.assertIsNotNone(parsed_env)
        self.assertEqual(parsed_env.envelope_id, envelope.envelope_id)
        self.assertEqual(parsed_env.git.commit_sha, envelope.git.commit_sha)
        self.assertEqual(parsed_env.payload["test_key"], "test_val")
        self.assertTrue(FleetProtocolEngine.verify_envelope_signature(parsed_env))

    def test_receipt_creation_and_parsing_roundtrip(self):
        receipt = ProtocolPhiReceipt(
            receipt_id="rcpt_8f92a10b",
            parent_envelope_id="phi_env_0e7f9fffbb35",
            timestamp_utc="2026-10-23T06:22:15Z",
            reviewer=f"{self.did_codex}#codex@wolfram",
            verdict="PASS",
            checks={
                "repo_coordinates_valid": True,
                "clean_sandbox_checkout": True,
                "commit_sha_verified": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
                "test_suite_passed": True
            },
            evidence_hash="c5a71b8e9d2f0a1c3e4b5d6f7a8b9c0d1e2f3a4b",
            attestation={"algorithm": "ed25519", "signature": "mock_sig"}
        )
        signed_rcpt = FleetProtocolEngine.sign_receipt(receipt, self.priv_codex.hex())
        self.assertEqual(len(signed_rcpt.attestation["signature"]), 128)
        self.assertTrue(FleetProtocolEngine.verify_receipt_signature(signed_rcpt))

        comment_md = FleetProtocolEngine.format_receipt_comment(signed_rcpt)
        self.assertIn("<!-- phi-receipt:rcpt_8f92a10b -->", comment_md)

        parsed_receipt = FleetProtocolEngine.parse_receipt_comment(comment_md)
        self.assertIsNotNone(parsed_receipt)
        self.assertEqual(parsed_receipt.verdict, "PASS")
        self.assertTrue(parsed_receipt.checks["test_suite_passed"])
        self.assertTrue(FleetProtocolEngine.verify_receipt_signature(parsed_receipt))

    def test_workspace_contamination_detector_catches_incident_22_10(self):
        # Simulation of incident 22.10:
        # Expected repo was ChitAkasha-Wolfram / agent-mandates, but environment had quantum-chemistry
        actual_contaminated_repo = "https://github.com/IndraProposal/quantum-chemistry.git"
        actual_head_commit = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

        ok, msg = FleetProtocolEngine.verify_workspace_isolation(
            expected_git=self.valid_git,
            actual_repo_url=actual_contaminated_repo,
            actual_head_commit=actual_head_commit
        )
        self.assertFalse(ok)
        self.assertIn("WORKSPACE CONTAMINATED", msg)

    def test_workspace_isolation_detects_commit_mismatch(self):
        # Correct repo, but wrong commit
        wrong_commit = "1111222233334444555566667777888899990000"
        ok, msg = FleetProtocolEngine.verify_workspace_isolation(
            expected_git=self.valid_git,
            actual_repo_url=self.valid_git.repo_url,
            actual_head_commit=wrong_commit
        )
        self.assertFalse(ok)
        self.assertIn("COMMIT MISMATCH", msg)

    def test_workspace_isolation_clean_match(self):
        ok, msg = FleetProtocolEngine.verify_workspace_isolation(
            expected_git=self.valid_git,
            actual_repo_url=self.valid_git.repo_url,
            actual_head_commit=self.valid_git.commit_sha
        )
        self.assertTrue(ok)
        self.assertIn("verified clean", msg)

    def test_peer_review_boundary_enforcement(self):
        # Builder cannot review own code
        self.assertFalse(FleetProtocolEngine.validate_peer_review_boundary("agy@wolfram", "agy@wolfram"))
        self.assertFalse(FleetProtocolEngine.validate_peer_review_boundary("codex@wolfram", "codex@wolfram"))
        self.assertFalse(FleetProtocolEngine.validate_peer_review_boundary("did:key:1#agy@wolfram", "did:key:2#agy@darwin"))

        # Cross audit allowed
        self.assertTrue(FleetProtocolEngine.validate_peer_review_boundary("agy@wolfram", "codex@wolfram"))
        self.assertTrue(FleetProtocolEngine.validate_peer_review_boundary("codex@wolfram", "agy@wolfram"))
        self.assertTrue(FleetProtocolEngine.validate_peer_review_boundary("gemini@darwin", "agy@wolfram"))

    def test_two_tier_governance_sub_issue_autonomous_done(self):
        receipt_pass = ProtocolPhiReceipt(
            receipt_id="rcpt_001",
            parent_envelope_id="phi_env_001",
            timestamp_utc="2026-10-23T06:22:15Z",
            reviewer=f"{self.did_codex}#codex@wolfram",
            verdict="PASS",
            checks={"tests": "passed"},
            evidence_hash="abcdef",
            attestation={"algorithm": "ed25519", "signature": "mock_sig"}
        )
        decision = FleetProtocolEngine.evaluate_acceptance_gate(
            is_project_release=False,
            receipt=receipt_pass
        )
        self.assertEqual(decision["action"], "AUTONOMOUS_QA_DONE_MERGE")
        self.assertEqual(decision["next_status"], "Done")
        self.assertEqual(decision["pavel_attention_required"], "NO")

    def test_two_tier_governance_project_gate_escalates_to_pavel(self):
        receipt_pass = ProtocolPhiReceipt(
            receipt_id="rcpt_002",
            parent_envelope_id="phi_env_002",
            timestamp_utc="2026-10-23T06:22:15Z",
            reviewer=f"{self.did_codex}#codex@wolfram",
            verdict="PASS",
            checks={"tests": "passed"},
            evidence_hash="abcdef",
            attestation={"algorithm": "ed25519", "signature": "mock_sig"}
        )
        decision = FleetProtocolEngine.evaluate_acceptance_gate(
            is_project_release=True,
            receipt=receipt_pass
        )
        self.assertEqual(decision["action"], "ESCALATE_TO_PRINCIPAL_GATE")
        self.assertEqual(decision["next_status"], "In Review")
        self.assertEqual(decision["assignee"], "Pavel Cherkashin")
        self.assertIn("Single-tap", decision["pavel_attention_required"])

    def test_two_tier_governance_rework_returns_to_builder(self):
        receipt_rework = ProtocolPhiReceipt(
            receipt_id="rcpt_003",
            parent_envelope_id="phi_env_003",
            timestamp_utc="2026-10-23T06:22:15Z",
            reviewer=f"{self.did_codex}#codex@wolfram",
            verdict="REWORK",
            checks={"tests": "failed"},
            evidence_hash="abcdef",
            attestation={"algorithm": "ed25519", "signature": "mock_sig"},
            rework_details=["test_failure in module X line 42"]
        )
        decision = FleetProtocolEngine.evaluate_acceptance_gate(
            is_project_release=False,
            receipt=receipt_rework
        )
        self.assertEqual(decision["action"], "ASSIGN_BUILDER_REWORK")
        self.assertEqual(decision["next_status"], "In Progress")
        self.assertEqual(decision["pavel_attention_required"], "NO")


if __name__ == "__main__":
    unittest.main()
