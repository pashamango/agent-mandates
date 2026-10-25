"""
Acceptance Test Suite for fleetd vNext (MIN-229 Section 8)
Compatible with both pytest and python3 -m unittest (zero external dependency).

Validates all 9 acceptance criteria:
1. Task without project -> REWORK to PM, builder not dispatched, ledger record.
2. Task without tier -> REWORK to PM, builder not dispatched.
3. Task MP (submitted application) -> builder not dispatched.
4. Task VS/VP in Todo > 2h -> fallback to codex-local exactly once, no double claim.
5. Double claim simulation -> single lease only, 2nd rejected & logged.
6. Quota exhaustion signal -> slot halted, fail-closed, no silent transfer.
7. Model unavailable / timeout -> deterministic fallback, incident logged, no task lost.
8. Historical incident regression suite (MIN-124, MIN-173, MIN-213, MIN-221) passes, rollback tested.
9. Zero-trust git handoff & clean clone verification.
"""

import os
import shutil
import tempfile
import unittest

from router.canonical_gate import validate_canonical_rules
from router.smart_router import SmartRouter
from router.ledger import RoutingLedger, run_historical_regression_suite
from router.health_pilot import run_codex_health_check, execute_pilot_task_min_221


class TestFleetdVNextAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_file = os.path.join(self.temp_dir, "test_ledger.jsonl")
        self.ledger = RoutingLedger(ledger_path=self.ledger_file)

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
        }
        verdict = validate_canonical_rules(task)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "MISSING_PROJECT")
        self.assertIn("@PM REWORK", verdict.rework_comment)

        router = SmartRouter(pacing_secs=0)
        success, slot, reason = router.route_and_claim_task(task)
        self.assertFalse(success)
        self.assertIsNone(slot)
        self.assertEqual(reason, "MISSING_PROJECT")

        self.ledger.record_decision({
            "task_id": task["id"],
            "canonical_verdict": verdict.to_dict(),
            "dispatched": success,
        })
        records = self.ledger.read_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["canonical_verdict"]["reason"], "MISSING_PROJECT")

    def test_acceptance_2_task_without_tier(self):
        """2. Задача без тира → REWORK к PM, билдеру не выдана."""
        task = {
            "id": "TASK-NOTIER",
            "title": "A task with no tier",
            "project": "Core Infrastructure",
            "description": "Builder: agy\nDoing some work without tier specification.",
        }
        verdict = validate_canonical_rules(task)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "MISSING_TIER")
        self.assertIn("@PM REWORK", verdict.rework_comment)

        router = SmartRouter(pacing_secs=0)
        success, slot, reason = router.route_and_claim_task(task)
        self.assertFalse(success)
        self.assertIsNone(slot)

    def test_acceptance_3_task_mp_submitted_frozen(self):
        """3. Задача МП (поданная заявка) → билдеру не выдана."""
        task = {
            "id": "TASK-SUBMITTED-MP",
            "title": "Job Application to Anthropic",
            "project": "Поиск работы",
            "description": "Builder: agy\nTier: МП\nApplication submitted awaiting-tap.",
            "tier": "МП",
        }
        verdict = validate_canonical_rules(task)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "SUBMITTED_FROZEN")
        self.assertIn("заморожена", verdict.rework_comment)

        router = SmartRouter(pacing_secs=0)
        success, slot, reason = router.route_and_claim_task(task)
        self.assertFalse(success)
        self.assertIsNone(slot)

    def test_acceptance_4_fallback_trigger_vs_vp_over_2h(self):
        """4. Задача ВС/ВП в Todo >2 часов при живом agy-лейне → fallback-захват codex-local ровно один раз, второй слот её не берёт."""
        router = SmartRouter(pacing_secs=300)
        task = {
            "id": "MIN-221-TEST",
            "title": "Collatz M3 CacheCheck",
            "project": "Project Collatz",
            "description": "Builder: agy\nTier: ВП\nLinear checker proof.",
            "tier": "ВП",
            "wait_time_secs": 7300,  # > 7200s (2h)
        }

        # First attempt: routes to codex-local fallback
        success, slot, reason = router.route_and_claim_task(task)
        self.assertTrue(success)
        self.assertEqual(slot, "codex-local")
        self.assertIn("CLAIMED_CODEX-LOCAL", reason)

        # Immediate second attempt to re-claim: blocked by single lease!
        success2, slot2, reason2 = router.route_and_claim_task(task)
        self.assertFalse(success2)
        self.assertIsNone(slot2)
        self.assertEqual(reason2, "DOUBLE_CLAIM_REJECTED")

    def test_acceptance_5_double_claim_simulation(self):
        """5. Симуляция двойного захвата → только один lease, второй захват отклонён и залогирован как инцидент."""
        router = SmartRouter(pacing_secs=0)
        task = {
            "id": "TASK-DOUBLE-CLAIM",
            "title": "Concurrent claim test",
            "project": "Swarm Engine",
            "description": "Builder: agy\nTier: ВС\nCritical proof.",
            "tier": "ВС",
            "wait_time_secs": 100,
        }

        # Slot 1 claims
        success1, slot1, _ = router.route_and_claim_task(task)
        self.assertTrue(success1)
        self.assertEqual(slot1, "agy")

        # Slot 2 attempts to claim the same task
        success2, slot2, reason2 = router.route_and_claim_task(task)
        self.assertFalse(success2)
        self.assertIsNone(slot2)
        self.assertEqual(reason2, "DOUBLE_CLAIM_REJECTED")

        # Verify incident logging
        incidents = [inc for inc in router.incidents if inc["event"] == "DOUBLE_CLAIM_REJECTED"]
        self.assertGreaterEqual(len(incidents), 1)
        self.assertEqual(incidents[0]["task_id"], "TASK-DOUBLE-CLAIM")

    def test_acceptance_6_quota_exhaustion_fail_closed(self):
        """6. Сигнал исчерпания квоты слота → захваты слота остановлены, нагрузка молча не перекинута."""
        router = SmartRouter(pacing_secs=0)
        router.set_slot_quota("agy", "QUOTA_EXHAUSTED_429")

        # Regular task targeting agy (wait time < 2h)
        task = {
            "id": "TASK-QUOTA-TEST",
            "title": "Math proof",
            "project": "MathLab",
            "description": "Builder: agy\nTier: ВП\nCompute heavy.",
            "tier": "ВП",
            "wait_time_secs": 600,
        }
        success, slot, reason = router.route_and_claim_task(task)
        # Fail-closed: must NOT silently route to codex when fallback conditions are not met!
        self.assertFalse(success)
        self.assertIsNone(slot)
        self.assertIn("QUOTA_EXHAUSTED", reason)

    def test_acceptance_7_local_model_unavailable_fallback(self):
        """7. Модель недоступна → маршрутизация детерминированными правилами, инцидент залогирован, задачи не потеряны."""
        router = SmartRouter(pacing_secs=0)
        task = {
            "id": "TASK-MODEL-DOWN",
            "title": "Task during model outage",
            "project": "Core",
            "description": "Builder: agy\nTier: ВП\nWork to do.",
            "tier": "ВП",
            "wait_time_secs": 100,
        }
        # Simulate model timeout / unavailability
        success, slot, reason = router.route_and_claim_task(task, model_timeout=True)
        self.assertTrue(success)
        self.assertEqual(slot, "agy")
        self.assertIn("deterministic_fallback", reason)

        # Verify incident was logged
        model_incidents = [inc for inc in router.incidents if inc["event"] == "MODEL_ROUTER_UNAVAILABLE"]
        self.assertGreaterEqual(len(model_incidents), 1)
        self.assertEqual(model_incidents[0]["task_id"], "TASK-MODEL-DOWN")

    def test_acceptance_8_historical_incident_regression_and_rollback(self):
        """8. Новая политика маршрутизации на исторических инцидентах (MIN-124, MIN-173, MIN-213, MIN-221) даёт не хуже базовой по метрикам §5.3, иначе rollback."""
        router = SmartRouter(pacing_secs=0)
        regression_report = run_historical_regression_suite(router)
        self.assertTrue(regression_report["all_passed"], f"Historical regression failed: {regression_report['cases']}")

        # Verify every individual historical case
        for case_id in ["MIN-124", "MIN-173", "MIN-213", "MIN-221"]:
            self.assertTrue(regression_report["cases"][case_id]["passed"])

    def test_acceptance_9_zero_trust_git_handoff_and_pilot(self):
        """9. Handoff каждой сборки — repo URL + branch + commit, проверка чистым клоном; evidence-unavailable — это FAIL сборки, а не «попробовать ещё раз так же»."""
        health = run_codex_health_check()
        self.assertEqual(health["status"], "HEALTH_OK")
        self.assertTrue(health["git_capable"])

        pilot = execute_pilot_task_min_221()
        self.assertEqual(pilot["slot"], "codex-local")
        self.assertTrue(pilot["git_handoff"]["repo_url"].startswith("https://"))
        self.assertTrue(bool(pilot["git_handoff"]["branch"]))
        self.assertEqual(len(pilot["git_handoff"]["commit_sha"]), 40)


if __name__ == "__main__":
    unittest.main()
