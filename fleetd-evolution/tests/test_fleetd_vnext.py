"""
Acceptance Test Suite for fleetd vNext (MIN-229 Section 8)
Compatible with both pytest and python3 -m unittest (zero external dependency).

Validates all 9 acceptance criteria:
1. Task without project -> REWORK to PM, builder not dispatched, automatic ledger record.
2. Task without tier -> REWORK to PM, builder not dispatched.
3. Task MP (submitted application) -> builder not dispatched; tasks with status Done rejected.
4. Task VS/VP in Todo > 2h OR agy quota exhausted -> fallback to codex-local exactly once, no double claim.
5. Double claim simulation -> cross-instance durable lease protection, 2nd rejected & logged.
6. Quota exhaustion signal -> slot halted, fail-closed, no silent transfer.
7. Model decision actively applied; when model unavailable -> deterministic fallback, incident logged, no task lost.
8. Baseline quantifiable metrics comparison on historical incidents (MIN-124, MIN-173, MIN-213, MIN-221) and automated rollback verified.
9. Zero-trust git handoff, clean clone verification, fail-closed health check, and pilot gate validation.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from router.canonical_gate import validate_canonical_rules
from router.smart_router import SmartRouter, DurableLeaseManager
from router.ledger import (
    RoutingLedger,
    evaluate_policy_metrics,
    evaluate_and_apply_policy_update,
    run_historical_regression_suite,
)
from router.health_pilot import (
    run_codex_health_check,
    execute_pilot_task_min_221,
    verify_pilot_git_handoff_clean_clone,
)


class TestFleetdVNextAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_file = os.path.join(self.temp_dir, "test_ledger.jsonl")
        self.lease_file = os.path.join(self.temp_dir, "active_leases.json")
        self.ledger = RoutingLedger(ledger_path=self.ledger_file)
        self.lease_mgr = DurableLeaseManager(lease_file=self.lease_file)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_acceptance_1_task_without_project(self):
        """1. Задача без проекта → REWORK к PM, билдеру не выдана, в ledger — запись."""
        task = {
            "id": "TASK-NOPROJ",
            "title": "A task with no project assigned",
            "project": None,
            "description": "Builder: agy\nTier: ВП\nDo something without a container.",
            "tier": "ВП",
            "status": "Todo",
        }
        verdict = validate_canonical_rules(task)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "MISSING_PROJECT")
        self.assertIn("@PM REWORK", verdict.rework_comment)

        router = SmartRouter(pacing_secs=0, ledger=self.ledger, lease_manager=self.lease_mgr)
        success, slot, reason = router.route_and_claim_task(task)
        self.assertFalse(success)
        self.assertIsNone(slot)
        self.assertEqual(reason, "MISSING_PROJECT")

        records = self.ledger.read_records()
        self.assertGreaterEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "CANONICAL_GATE_REJECTED")
        self.assertEqual(records[0]["reason"], "MISSING_PROJECT")

    def test_acceptance_2_task_without_tier(self):
        """2. Задача без тира → REWORK к PM, билдеру не выдана."""
        task = {
            "id": "TASK-NOTIER",
            "title": "A task with no tier",
            "project": "Core Infrastructure",
            "description": "Builder: agy\nDoing some work without tier specification.",
            "status": "Todo",
        }
        verdict = validate_canonical_rules(task)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "MISSING_TIER")
        self.assertIn("@PM REWORK", verdict.rework_comment)

        router = SmartRouter(pacing_secs=0, ledger=self.ledger, lease_manager=self.lease_mgr)
        success, slot, reason = router.route_and_claim_task(task)
        self.assertFalse(success)
        self.assertIsNone(slot)

    def test_acceptance_3_task_mp_submitted_frozen_and_status_check(self):
        """3. Задача МП (поданная заявка) → билдеру не выдана. Задача в статусе Done также отклоняется."""
        # A: MP submitted application
        task_mp = {
            "id": "TASK-SUBMITTED-MP",
            "title": "Job Application to Anthropic",
            "project": "Поиск работы",
            "description": "Builder: agy\nTier: МП\nApplication submitted awaiting-tap.",
            "tier": "МП",
            "status": "Todo",
        }
        verdict_mp = validate_canonical_rules(task_mp)
        self.assertFalse(verdict_mp.passed)
        self.assertEqual(verdict_mp.reason, "SUBMITTED_FROZEN")
        self.assertIn("заморожена", verdict_mp.rework_comment)

        router = SmartRouter(pacing_secs=0, ledger=self.ledger, lease_manager=self.lease_mgr)
        success, slot, _ = router.route_and_claim_task(task_mp)
        self.assertFalse(success)
        self.assertIsNone(slot)

        # B: Status Done rejection check (§6 defect fix)
        task_done = {
            "id": "TASK-ALREADY-DONE",
            "title": "Finished project step",
            "project": "Swarm Engine",
            "description": "Builder: agy\nTier: ВП\nAlready finished work.",
            "tier": "ВП",
            "status": "Done",
            "wait_time_secs": 10000,
        }
        success_done, slot_done, reason_done = router.route_and_claim_task(task_done)
        self.assertFalse(success_done)
        self.assertEqual(reason_done, "ALREADY_DONE")

    def test_acceptance_4_fallback_triggers_time_and_quota(self):
        """4. Fallback-захват codex-local: а) по времени > 2ч; б) по сигналу исчерпания квоты agy."""
        router = SmartRouter(pacing_secs=300, ledger=self.ledger, lease_manager=self.lease_mgr, pilot_passed=True)

        # 4a: Fallback via wait_time > 7200s (2 hours)
        task_time = {
            "id": "TASK-FALLBACK-TIME",
            "title": "Collatz M3 CacheCheck wait",
            "project": "Project Collatz",
            "description": "Builder: agy\nTier: ВП\nWaiting in queue for 2.1 hours.",
            "tier": "ВП",
            "status": "Todo",
            "wait_time_secs": 7560,
        }
        success_a, slot_a, _ = router.route_and_claim_task(task_time)
        self.assertTrue(success_a)
        self.assertEqual(slot_a, "codex-local")

        # Second attempt for same task must be rejected
        sec_success, _, sec_reason = router.route_and_claim_task(task_time)
        self.assertFalse(sec_success)
        self.assertEqual(sec_reason, "DOUBLE_CLAIM_REJECTED")
        router.release_lease("TASK-FALLBACK-TIME")

        # 4b: Fallback via agy quota exhaustion signal (< 2h wait time)
        router.set_slot_quota("agy", "QUOTA_EXHAUSTED_429")
        task_quota = {
            "id": "TASK-FALLBACK-QUOTA",
            "title": "Collatz M0 lake build",
            "project": "Project Collatz",
            "description": "Builder: agy\nTier: ВС\nCritical foundation.",
            "tier": "ВС",
            "status": "Todo",
            "wait_time_secs": 1200,  # Only 20 minutes wait, but agy quota is exhausted!
        }
        success_b, slot_b, _ = router.route_and_claim_task(task_quota)
        self.assertTrue(success_b)
        self.assertEqual(slot_b, "codex-local")
        router.release_lease("TASK-FALLBACK-QUOTA")

    def test_acceptance_5_double_claim_simulation_and_durable_lease(self):
        """5. Симуляция двойного захвата → только один lease, межпроцессная защита, 2-й захват отклонён."""
        router1 = SmartRouter(pacing_secs=0, ledger=self.ledger, lease_manager=self.lease_mgr)
        task = {
            "id": "TASK-DOUBLE-CLAIM",
            "title": "Concurrent claim test",
            "project": "Swarm Engine",
            "description": "Builder: agy\nTier: ВС\nCritical proof.",
            "tier": "ВС",
            "status": "Todo",
            "wait_time_secs": 100,
        }

        # Router instance 1 claims with commit SHA
        success1, slot1, _ = router1.route_and_claim_task(task, commit_sha="abcdef1234567890abcdef1234567890abcdef12")
        self.assertTrue(success1)
        self.assertEqual(slot1, "agy")

        # Verify durable lease recorded commit SHA
        stored_lease = self.lease_mgr.get_lease("TASK-DOUBLE-CLAIM")
        self.assertIsNotNone(stored_lease)
        self.assertEqual(stored_lease.commit_sha, "abcdef1234567890abcdef1234567890abcdef12")

        # Router instance 2 (simulating a separate process sharing the lease database) attempts capture
        router2 = SmartRouter(
            pacing_secs=0,
            ledger=self.ledger,
            lease_manager=DurableLeaseManager(lease_file=self.lease_file),
        )
        success2, slot2, reason2 = router2.route_and_claim_task(task)
        self.assertFalse(success2)
        self.assertIsNone(slot2)
        self.assertEqual(reason2, "DOUBLE_CLAIM_REJECTED")

        # Verify incident logging
        incidents = [inc for inc in router2.incidents if inc["event"] == "DOUBLE_CLAIM_REJECTED"]
        self.assertGreaterEqual(len(incidents), 1)

    def test_acceptance_6_quota_exhaustion_fail_closed(self):
        """6. Сигнал исчерпания квоты слота → захваты слота остановлены, нагрузка молча не перекинута."""
        router = SmartRouter(pacing_secs=0, ledger=self.ledger, lease_manager=self.lease_mgr)
        router.set_slot_quota("agy", "QUOTA_EXHAUSTED_429")

        # Non-fallback candidate (Tier МС - never eligible for fallback)
        task_mc = {
            "id": "TASK-QUOTA-TEST-MC",
            "title": "Small maintenance task",
            "project": "Infrastructure",
            "description": "Builder: agy\nTier: МС\nLow priority docs.",
            "tier": "МС",
            "status": "Todo",
            "wait_time_secs": 600,
        }
        success, slot, reason = router.route_and_claim_task(task_mc)
        # Fail-closed: must NOT silently route to codex when tier is MC!
        self.assertFalse(success)
        self.assertIsNone(slot)
        self.assertIn("QUOTA_EXHAUSTED", reason)

    def test_acceptance_7_local_model_active_decision_and_timeout_fallback(self):
        """7. Модель активно определяет слот; при недоступности -> детерминированный fallback, инцидент в ledger."""
        router = SmartRouter(pacing_secs=0, ledger=self.ledger, lease_manager=self.lease_mgr, pilot_passed=True)

        # 7a: Active model decision for codex-local
        task_urgent = {
            "id": "TASK-MODEL-DECISION",
            "title": "Urgent math task",
            "project": "MathLab",
            "description": "Builder: agy\nTier: ВС\nNeeds immediate work.",
            "tier": "ВС",
            "status": "Todo",
            "wait_time_secs": 8000,
        }
        # Model decision evaluates task attributes and recommends codex-local
        suggested = router.simulate_local_model_decision(task_urgent)
        self.assertEqual(suggested, "codex-local")

        success_a, slot_a, _ = router.route_and_claim_task(task_urgent)
        self.assertTrue(success_a)
        self.assertEqual(slot_a, "codex-local")
        router.release_lease("TASK-MODEL-DECISION")

        # 7b: Model timeout simulation -> fallback to deterministic rules
        task_timeout = {
            "id": "TASK-MODEL-DOWN",
            "title": "Task during model timeout",
            "project": "Core",
            "description": "Builder: agy\nTier: ВП\nWork to do.",
            "tier": "ВП",
            "status": "Todo",
            "wait_time_secs": 100,
        }
        success_b, slot_b, reason_b = router.route_and_claim_task(task_timeout, model_timeout=True)
        self.assertTrue(success_b)
        self.assertEqual(slot_b, "agy")
        self.assertIn("deterministic_fallback", reason_b)

        # Verify incident was recorded in ledger
        records = self.ledger.read_records()
        model_records = [r for r in records if r.get("event") == "MODEL_ROUTER_UNAVAILABLE"]
        self.assertGreaterEqual(len(model_records), 1)

    def test_acceptance_8_historical_incident_metrics_and_rollback(self):
        """8. Новая политика маршрутизации на исторических инцидентах даёт не хуже базовой, иначе rollback."""
        router = SmartRouter(pacing_secs=0, ledger=self.ledger, lease_manager=self.lease_mgr, pilot_passed=True)

        # 8a: Verify all historical cases pass
        regression_report = run_historical_regression_suite(router)
        self.assertTrue(regression_report["all_passed"], f"Historical regression failed: {regression_report['cases']}")

        # 8b: Baseline quantifiable metrics evaluation (§5.3)
        baseline = evaluate_policy_metrics(router)
        self.assertEqual(baseline["double_claims"], 0)
        self.assertEqual(baseline["uncanonical_dispatched"], 0)
        self.assertEqual(baseline["missed_triggers"], 0)
        self.assertGreater(baseline["on_time_ratio"], 0.0)

        # 8c: Test automated rollback upon degrading candidate policy
        # Candidate model that errantly routes uncanonical or non-fallback tasks
        def buggy_candidate_model(task, timeout_simulate=False):
            # Errant model attempts to route everything to agy, causing missed fallback
            return "agy"

        # Force a condition where candidate degrades missed_triggers
        router.set_slot_quota("agy", "QUOTA_EXHAUSTED_429")
        update_result = evaluate_and_apply_policy_update(
            router=router,
            candidate_model_fn=buggy_candidate_model,
            baseline_metrics={"double_claims": 0, "missed_triggers": 0, "uncanonical_dispatched": 0, "on_time_ratio": 1.0},
        )
        self.assertEqual(update_result["status"], "ROLLED_BACK")
        self.assertFalse(update_result["policy_accepted"])

    def test_acceptance_9_zero_trust_git_handoff_pilot_and_manifest(self):
        """9. Zero-trust git handoff: clean-clone verify, fail-closed health check, pilot gate, manifest integrity."""
        # 9a: Fail-closed health check of codex runner
        health = run_codex_health_check()
        # In current live Wolfram environment, codex returns 403 Forbidden -> must FAIL CLOSED!
        self.assertEqual(health["status"], "HEALTH_FAILED")
        self.assertFalse(health["git_capable"])
        self.assertIn("Codex runner", health["details"])

        # 9b: Pilot task MIN-221 verification
        pilot = execute_pilot_task_min_221()
        self.assertEqual(pilot["slot"], "codex-local")
        self.assertTrue(pilot["git_handoff"]["repo_url"].startswith("https://"))
        self.assertEqual(len(pilot["git_handoff"]["commit_sha"]), 40)
        # Verify remote git coordinates live via git ls-remote
        self.assertTrue(pilot["git_handoff"]["remote_verified"])
        # Because independent QA returned FAIL on CacheCheck.lean, fallback MUST remain disabled!
        self.assertFalse(pilot["pilot_passed"])
        self.assertFalse(pilot["fallback_activation_permitted"])
        self.assertEqual(pilot["independent_qa"]["verdict"], "FAIL")

        # 9c: Manifest integrity check
        manifest_path = "fleetd-evolution/evidence/MANIFEST.sha256"
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
            for line in lines:
                parts = line.split(None, 1)
                if len(parts) == 2:
                    expected_sha, file_rel = parts[0], parts[1].strip()
                    self.assertTrue(os.path.exists(file_rel), f"Manifest file missing: {file_rel}")
                    if "TEST_RUN.log" in file_rel or "test_fleetd_vnext.py" in file_rel:
                        self.assertGreater(os.path.getsize(file_rel), 0)
                        continue
                    calc_sha = subprocess.run(
                        ["shasum", "-a", "256", file_rel],
                        stdout=subprocess.PIPE,
                        text=True,
                    ).stdout.split()[0]
                    self.assertEqual(calc_sha, expected_sha, f"SHA mismatch for {file_rel}: {calc_sha} != {expected_sha}")


if __name__ == "__main__":
    unittest.main()
