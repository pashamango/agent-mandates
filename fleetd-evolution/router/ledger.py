"""
Routing Ledger & Policy Verifier (fleetd vNext - Section 5.3)
Records decisions to routing_ledger.jsonl and validates policy regression
on historical incidents: MIN-124, MIN-173, MIN-213, MIN-221, MIN-219.

Includes baseline evaluation and automated rollback upon metric degradation:
- on_time_ratio: fraction of VS/VP tasks claimed in a timely manner
- double_claims: count of double claims (must be 0)
- missed_triggers: count of missed fallback opportunities (must be 0)
- uncanonical_dispatched: count of uncanonical tasks dispatched to builders (must be 0)
"""

import json
import os
import time
from typing import Dict, Any, List, Optional, Callable


class RoutingLedger:
    def __init__(self, ledger_path: str = "fleetd-evolution/data/routing_ledger.jsonl"):
        self.ledger_path = ledger_path
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)

    def record_decision(self, entry: Dict[str, Any]):
        entry["timestamp"] = entry.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.ledger_path):
            return []
        records = []
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        return records

    def clear(self):
        if os.path.exists(self.ledger_path):
            os.remove(self.ledger_path)


def get_historical_test_suite() -> List[Dict[str, Any]]:
    """
    Standard test dataset representing known incidents:
    - MIN-124: codex-cloud assigned task with @Codex mention (must not be claimed locally)
    - MIN-173: ambiguous builder marker (must return REWORK)
    - MIN-213: submitted MP job application (must be frozen)
    - MIN-221: Collatz M3 waiting > 2 hours in Todo (must trigger fallback)
    - MIN-219: Collatz M1 waiting > 2 hours in Todo with agy quota exhausted (must trigger fallback)
    """
    return [
        {
            "id": "MIN-124",
            "title": "InsightCodex: Riemann null-модели",
            "project": "Research",
            "description": "Tier: ВС\nInsight investigation. @Codex handle this in cloud.",
            "tier": "ВС",
            "assignee": "Codex",
            "status": "Todo",
            "wait_time_secs": 3600,
            "expected_outcome": "REJECT_CLOUD_LANE",
        },
        {
            "id": "MIN-173",
            "title": "Исследование dev-программ",
            "project": "Infrastructure",
            "description": "Builder: codex-custom-unknown\nTier: ВП\nTask details.",
            "tier": "ВП",
            "status": "Todo",
            "wait_time_secs": 1800,
            "expected_outcome": "REWORK_AMBIGUOUS_MARKER",
        },
        {
            "id": "MIN-213",
            "title": "Cartesia Software Engineer Agent Harness",
            "project": "Поиск работы",
            "description": "Builder: agy\nTier: МП\nStatus: submitted cv-ready awaiting-tap. Application was sent yesterday.",
            "tier": "МП",
            "status": "Todo",
            "wait_time_secs": 300,
            "expected_outcome": "FROZEN_SUBMITTED",
        },
        {
            "id": "MIN-221",
            "title": "Collatz v0.1.0 — M3: CacheCheck",
            "project": "Project Collatz",
            "description": "Builder: agy\nTier: ВП\nCacheCheck verification.",
            "tier": "ВП",
            "status": "Todo",
            "wait_time_secs": 7500,  # > 2h
            "expected_outcome": "CLAIM_FALLBACK_CODEX",
        },
        {
            "id": "MIN-219",
            "title": "Collatz v0.1.0 — M0/M1 Lake Skeleton",
            "project": "Project Collatz",
            "description": "Builder: agy\nTier: ВС\nFoundation lake build.",
            "tier": "ВС",
            "status": "Todo",
            "wait_time_secs": 1000,  # < 2h, but agy quota exhausted
            "agy_quota": "QUOTA_EXHAUSTED_429",
            "expected_outcome": "CLAIM_FALLBACK_QUOTA",
        },
    ]


def evaluate_policy_metrics(router, test_suite: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Evaluates router against historical incident suite and calculates quantifiable metrics:
    - on_time_ratio: float (0.0 to 1.0)
    - double_claims: int
    - missed_triggers: int
    - uncanonical_dispatched: int
    """
    suite = test_suite or get_historical_test_suite()
    total_vs_vp = 0
    on_time_count = 0
    double_claims = 0
    missed_triggers = 0
    uncanonical_dispatched = 0

    for item in suite:
        task = dict(item)
        expected = task.pop("expected_outcome", None)
        quota_override = task.pop("agy_quota", None)
        task_id = task["id"]
        tier = task.get("tier", "ВП")

        orig_quota = router.slot_quota_status.get("agy", "OK")
        if quota_override:
            router.set_slot_quota("agy", quota_override)

        is_vs_vp = tier in {"ВС", "ВП"}
        if is_vs_vp:
            total_vs_vp += 1

        success, slot, reason = router.route_and_claim_task(task)

        # Restore original quota
        if quota_override:
            router.set_slot_quota("agy", orig_quota)

        # Check for uncanonical dispatch
        if expected in {"REJECT_CLOUD_LANE", "REWORK_AMBIGUOUS_MARKER", "FROZEN_SUBMITTED"}:
            if success:
                uncanonical_dispatched += 1

        # Check fallback triggers
        if expected in {"CLAIM_FALLBACK_CODEX", "CLAIM_FALLBACK_QUOTA"}:
            if success and slot == "codex-local":
                on_time_count += 1
            else:
                missed_triggers += 1

        # Double claim test simulation on successfully claimed task
        if success:
            sec_success, sec_slot, sec_reason = router.route_and_claim_task(task)
            if sec_success:
                double_claims += 1

        # Clean up lease
        router.release_lease(task_id)

    on_time_ratio = (on_time_count / total_vs_vp) if total_vs_vp > 0 else 1.0

    return {
        "on_time_ratio": on_time_ratio,
        "double_claims": double_claims,
        "missed_triggers": missed_triggers,
        "uncanonical_dispatched": uncanonical_dispatched,
    }


def evaluate_and_apply_policy_update(
    router,
    candidate_model_fn: Optional[Callable] = None,
    candidate_rules: Optional[Dict[str, Any]] = None,
    baseline_metrics: Optional[Dict[str, Any]] = None,
    test_suite: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Applies candidate policy and runs regression verification (§5.3).
    If candidate metrics degrade compared to baseline on any metric,
    automatically triggers rollback to baseline deterministic rules.
    """
    suite = test_suite or get_historical_test_suite()

    # Determine baseline metrics if not supplied
    if baseline_metrics is None:
        baseline_metrics = evaluate_policy_metrics(router, suite)

    # Save original decision function for rollback
    original_fn = router.simulate_local_model_decision

    # Apply candidate policy
    if candidate_model_fn:
        router.simulate_local_model_decision = candidate_model_fn

    candidate_metrics = evaluate_policy_metrics(router, suite)

    # Rollback condition: any degradation
    is_degraded = (
        candidate_metrics["double_claims"] > baseline_metrics["double_claims"]
        or candidate_metrics["missed_triggers"] > baseline_metrics["missed_triggers"]
        or candidate_metrics["uncanonical_dispatched"] > baseline_metrics["uncanonical_dispatched"]
        or candidate_metrics["on_time_ratio"] < baseline_metrics["on_time_ratio"]
    )

    if is_degraded:
        # Execute immediate rollback
        router.simulate_local_model_decision = original_fn
        router.ledger.record_decision({
            "event": "POLICY_DEGRADATION_ROLLBACK",
            "baseline": baseline_metrics,
            "candidate": candidate_metrics,
            "status": "ROLLED_BACK_TO_BASELINE",
        })
        return {
            "status": "ROLLED_BACK",
            "policy_accepted": False,
            "baseline_metrics": baseline_metrics,
            "candidate_metrics": candidate_metrics,
            "reason": "Candidate degraded metrics compared to baseline; automatic rollback executed.",
        }

    # Policy accepted
    router.ledger.record_decision({
        "event": "POLICY_UPDATE_ACCEPTED",
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "status": "ACCEPTED",
    })
    return {
        "status": "ACCEPTED",
        "policy_accepted": True,
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
    }


def run_historical_regression_suite(router) -> Dict[str, Any]:
    """
    Runs historical incident verification suite on router instance.
    """
    results = {}

    # Case 1: MIN-124 (codex-cloud isolation)
    min_124_task = {
        "id": "MIN-124",
        "title": "InsightCodex: Riemann null-модели",
        "project": "Research",
        "description": "Tier: ВС\nInsight investigation. @Codex execute.",
        "assignee": "Codex",
        "status": "Todo",
        "tier": "ВС",
        "wait_time_secs": 3600,
    }
    success, slot, reason = router.route_and_claim_task(min_124_task)
    results["MIN-124"] = {
        "passed": (success is False and reason == "CODEX_CLOUD_LANE"),
        "expected": "Blocked from local fleetd (CODEX_CLOUD_LANE)",
        "actual": reason,
    }

    # Case 2: MIN-173 (ambiguous marker discipline)
    min_173_task = {
        "id": "MIN-173",
        "title": "Исследование dev-программ",
        "project": "Infrastructure",
        "description": "Builder: codex-custom-unknown\nTier: ВП\nTask details.",
        "status": "Todo",
        "tier": "ВП",
        "wait_time_secs": 1800,
    }
    success, slot, reason = router.route_and_claim_task(min_173_task)
    results["MIN-173"] = {
        "passed": (success is False and reason == "AMBIGUOUS_BUILDER_MARKER"),
        "expected": "REWORK for ambiguous marker",
        "actual": reason,
    }

    # Case 3: MIN-213 (submitted application frozen)
    min_213_task = {
        "id": "MIN-213",
        "title": "Cartesia Software Engineer Agent Harness",
        "project": "Поиск работы",
        "description": "Builder: agy\nTier: МП\nStatus: submitted cv-ready awaiting-tap. Application was sent yesterday.",
        "status": "Todo",
        "tier": "МП",
        "wait_time_secs": 300,
    }
    success, slot, reason = router.route_and_claim_task(min_213_task)
    results["MIN-213"] = {
        "passed": (success is False and reason == "SUBMITTED_FROZEN"),
        "expected": "Blocked from builder cycles (SUBMITTED_FROZEN)",
        "actual": reason,
    }

    # Case 4: MIN-221 (Collatz M3 fallback trigger after 2h wait)
    min_221_task = {
        "id": "MIN-221",
        "title": "Collatz v0.1.0 — M3: CacheCheck",
        "project": "Project Collatz",
        "description": "Builder: agy\nTier: ВП\nCacheCheck verification.",
        "status": "Todo",
        "tier": "ВП",
        "wait_time_secs": 7500,
    }
    success, slot, reason = router.route_and_claim_task(min_221_task)
    results["MIN-221"] = {
        "passed": (success is True and slot == "codex-local"),
        "expected": "Fallback to codex-local (wait > 2h)",
        "actual": f"slot={slot}, reason={reason}",
    }
    router.release_lease("MIN-221")

    all_passed = all(r["passed"] for r in results.values())
    return {
        "all_passed": all_passed,
        "cases": results,
    }
