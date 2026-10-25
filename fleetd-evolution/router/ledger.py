"""
Routing Ledger & Policy Verifier (fleetd vNext - Section 5.3)
Records decisions to routing_ledger.jsonl and validates policy regression
on historical incidents: MIN-124, MIN-173, MIN-213, MIN-221.
"""

import json
import os
import time
from typing import Dict, Any, List


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


def run_historical_regression_suite(router) -> Dict[str, Any]:
    """
    Tests the router against historical incident test cases:
    1. MIN-124: codex-cloud regex marker must NOT be captured by local fleetd.
    2. MIN-173: ambiguous builder marker must NOT cause double claim.
    3. MIN-213: submitted application (MP) must NOT be dispatched to builder.
    4. MIN-221: Collatz M3 waiting > 2 hours must trigger fallback to codex-local.
    """
    results = {}

    # Case 1: MIN-124 (codex-cloud isolation)
    min_124_task = {
        "id": "MIN-124",
        "title": "InsightCodex: Riemann null-модели",
        "project": "Research",
        "description": "Builder: codex-cloud\nTier: ВС\nInsight investigation.",
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
        "tier": "ВП",
        "wait_time_secs": 7500,  # > 2h (7200s)
    }
    success, slot, reason = router.route_and_claim_task(min_221_task)
    results["MIN-221"] = {
        "passed": (success is True and slot == "codex-local"),
        "expected": "Fallback to codex-local (wait > 2h)",
        "actual": f"slot={slot}, reason={reason}",
    }
    # Clean up lease after test
    router.release_lease("MIN-221")

    all_passed = all(r["passed"] for r in results.values())
    return {
        "all_passed": all_passed,
        "cases": results,
    }
