"""
Smart Router (fleetd vNext - Section 3 & 5)
Features:
- Primary lane: agy (min pacing interval 300s)
- Fallback lane: codex-local (only on ВС/ВП, Todo > 2h or agy quota exhausted, concurrent <= 1, health OK)
- Single lease manager (atomic claim, duplicate claim prevention)
- Quota fail-closed control
- Offline Wolfram model router contract with deterministic fallback
"""

import time
import threading
from typing import Dict, Any, Optional, Tuple
from .canonical_gate import validate_canonical_rules, CanonicalVerdict


class LeaseRecord:
    def __init__(self, task_id: str, slot: str, lease_ttl_secs: int = 3600):
        self.task_id = task_id
        self.slot = slot
        self.created_at = time.time()
        self.expires_at = self.created_at + lease_ttl_secs
        self.commit_sha: Optional[str] = None

    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class SmartRouter:
    def __init__(self, pacing_secs: int = 300):
        self.pacing_secs = pacing_secs
        self.last_agy_start: float = 0.0
        self.active_leases: Dict[str, LeaseRecord] = {}
        self.slot_quota_status: Dict[str, str] = {"agy": "OK", "codex-local": "OK"}
        self.slot_health_status: Dict[str, str] = {"agy": "HEALTH_OK", "codex-local": "HEALTH_OK"}
        self.lock = threading.Lock()
        self.incidents: list = []

    def set_slot_quota(self, slot: str, status: str):
        with self.lock:
            self.slot_quota_status[slot] = status

    def set_slot_health(self, slot: str, status: str):
        with self.lock:
            self.slot_health_status[slot] = status

    def simulate_local_model_decision(self, task: Dict[str, Any], timeout_simulate: bool = False) -> Optional[str]:
        """
        Simulates the local Wolfram offline model router.
        If timeout_simulate is True, exceeds budget and triggers deterministic fallback.
        """
        if timeout_simulate:
            # Model timeout / failure simulation
            return None
        # Model returns suggested slot based on tier and queue
        tier = task.get("tier", "ВП")
        wait_time = task.get("wait_time_secs", 0)
        if tier in {"ВС", "ВП"} and wait_time > 7200:
            return "codex-local"
        return "agy"

    def route_and_claim_task(
        self,
        task: Dict[str, Any],
        lease_ttl_secs: int = 3600,
        model_timeout: bool = False,
    ) -> Tuple[bool, Optional[str], str]:
        """
        Routes and claims a task atomically.
        Returns: (success, slot_or_none, message_or_verdict)
        """
        task_id = task.get("id")
        if not task_id:
            return False, None, "INVALID_TASK_ID"

        # 1. Canonical validation gate check
        verdict = validate_canonical_rules(task)
        if not verdict.passed:
            return False, None, verdict.reason

        with self.lock:
            # 2. Check for active lease (Single-lease protection)
            existing_lease = self.active_leases.get(task_id)
            if existing_lease and not existing_lease.is_expired():
                incident = {
                    "task_id": task_id,
                    "event": "DOUBLE_CLAIM_REJECTED",
                    "existing_slot": existing_lease.slot,
                    "timestamp": time.time(),
                }
                self.incidents.append(incident)
                return False, None, "DOUBLE_CLAIM_REJECTED"

            # 3. Model routing decision with deterministic fallback
            suggested_slot = self.simulate_local_model_decision(task, timeout_simulate=model_timeout)
            router_mode = "model_offline" if suggested_slot is not None else "deterministic_fallback"

            if suggested_slot is None:
                self.incidents.append({
                    "task_id": task_id,
                    "event": "MODEL_ROUTER_UNAVAILABLE",
                    "timestamp": time.time(),
                })

            # 4. Check primary slot assignment and quota fail-closed
            # If a slot is explicitly targeted or default, check its quota first
            tier = task.get("tier", "ВП")
            wait_time_secs = task.get("wait_time_secs", 0)
            target_builder = task.get("builder", "agy").lower() if "builder" in task else "agy"

            # Check for fallback trigger:
            # Fallback only triggers when:
            # 1. Tier is strictly ВС or ВП
            # 2. Task has been waiting in Todo > 7200s (2h)
            # 3. codex-local is healthy and has quota available
            # 4. Concurrent fallback count < 1
            fallback_active_count = sum(
                1 for l in self.active_leases.values()
                if l.slot == "codex-local" and not l.is_expired()
            )

            is_fallback_candidate = (
                tier in {"ВС", "ВП"}
                and wait_time_secs > 7200
                and self.slot_health_status.get("codex-local") == "HEALTH_OK"
                and self.slot_quota_status.get("codex-local") == "OK"
                and fallback_active_count < 1
            )

            if is_fallback_candidate:
                target_slot = "codex-local"
            else:
                target_slot = target_builder if target_builder in {"agy", "codex-local"} else "agy"

            # 5. Fail-closed quota check on the determined slot
            # Signal of exhaustion stops captures of that slot; does NOT silently transfer load!
            if self.slot_quota_status.get(target_slot) != "OK":
                return False, None, f"SLOT_QUOTA_EXHAUSTED_{target_slot.upper()}"

            # 6. Apply pacing for agy
            agy_busy = (time.time() - self.last_agy_start) < self.pacing_secs
            if target_slot == "agy" and agy_busy and wait_time_secs < 7200:
                return False, None, "PACING_BACKOFF"

            # 7. Grant atomic lease
            new_lease = LeaseRecord(task_id=task_id, slot=target_slot, lease_ttl_secs=lease_ttl_secs)
            self.active_leases[task_id] = new_lease
            if target_slot == "agy":
                self.last_agy_start = time.time()

            return True, target_slot, f"CLAIMED_{target_slot.upper()}_{router_mode}"

    def release_lease(self, task_id: str):
        with self.lock:
            if task_id in self.active_leases:
                del self.active_leases[task_id]
