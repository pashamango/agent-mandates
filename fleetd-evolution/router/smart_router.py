"""
Smart Router (fleetd vNext - Sections 3, 5 & 6)
Features:
- Primary lane: agy (min pacing interval 300s)
- Fallback lane: codex-local (only on ВС/ВП in Todo with wait > 2h OR agy quota exhausted, concurrent <= 1, health OK, pilot passed)
- Single lease manager (cross-process durable atomic lease with file lock & commit_sha tracking)
- Fail-closed quota control
- Integrated routing ledger recording all canonical rejections, claims, lease events, and model incidents
- Offline Wolfram model router contract with deterministic fallback and active target selection
"""

import fcntl
import json
import os
import time
import re
from typing import Dict, Any, Optional, Tuple, List
from .canonical_gate import validate_canonical_rules
from .ledger import RoutingLedger


class LeaseRecord:
    def __init__(self, task_id: str, slot: str, lease_ttl_secs: int = 3600, commit_sha: Optional[str] = None, created_at: Optional[float] = None):
        self.task_id = task_id
        self.slot = slot
        self.created_at = created_at or time.time()
        self.expires_at = self.created_at + lease_ttl_secs
        self.commit_sha = commit_sha

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "slot": self.slot,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "commit_sha": self.commit_sha,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LeaseRecord":
        rec = cls(
            task_id=data["task_id"],
            slot=data["slot"],
            lease_ttl_secs=int(data["expires_at"] - data["created_at"]),
            commit_sha=data.get("commit_sha"),
            created_at=data["created_at"],
        )
        rec.expires_at = data["expires_at"]
        return rec


class DurableLeaseManager:
    """
    Fleet-wide durable lease manager backed by an atomic file lock.
    Guarantees cross-process mutual exclusion and persistent lease records.
    """
    def __init__(self, lease_file: str = "fleetd-evolution/data/active_leases.json"):
        self.lease_file = lease_file
        os.makedirs(os.path.dirname(self.lease_file), exist_ok=True)
        self.lock_file = f"{self.lease_file}.lock"

    def _read_leases_locked(self) -> Dict[str, LeaseRecord]:
        if not os.path.exists(self.lease_file):
            return {}
        try:
            with open(self.lease_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                data = json.loads(content)
                if not isinstance(data, dict):
                    raise ValueError(f"Expected JSON dict, got {type(data)}")
                return {k: LeaseRecord.from_dict(v) for k, v in data.items()}
        except Exception as e:
            # Corrupted lease database must fail-closed, never assumed empty!
            raise RuntimeError(f"FATAL: Corrupted lease storage at {self.lease_file}: {e}")

    def _write_leases_locked(self, leases: Dict[str, LeaseRecord]):
        data = {k: v.to_dict() for k, v in leases.items() if not v.is_expired()}
        tmp_file = f"{self.lease_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, self.lease_file)

    def acquire_lease(
        self,
        task_id: str,
        slot: str,
        lease_ttl_secs: int = 3600,
        commit_sha: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        with open(self.lock_file, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                leases = self._read_leases_locked()
                existing = leases.get(task_id)
                if existing and not existing.is_expired():
                    return False, "DOUBLE_CLAIM_REJECTED"

                leases[task_id] = LeaseRecord(
                    task_id=task_id,
                    slot=slot,
                    lease_ttl_secs=lease_ttl_secs,
                    commit_sha=commit_sha,
                )
                self._write_leases_locked(leases)
                return True, None
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def release_lease(self, task_id: str):
        with open(self.lock_file, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                leases = self._read_leases_locked()
                if task_id in leases:
                    del leases[task_id]
                    self._write_leases_locked(leases)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def count_active_fallback(self) -> int:
        with open(self.lock_file, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
            try:
                leases = self._read_leases_locked()
                return sum(1 for l in leases.values() if l.slot == "codex-local" and not l.is_expired())
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def get_lease(self, task_id: str) -> Optional[LeaseRecord]:
        with open(self.lock_file, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
            try:
                leases = self._read_leases_locked()
                rec = leases.get(task_id)
                if rec and not rec.is_expired():
                    return rec
                return None
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)


class SmartRouter:
    def __init__(
        self,
        pacing_secs: int = 300,
        ledger: Optional[RoutingLedger] = None,
        lease_manager: Optional[DurableLeaseManager] = None,
        pilot_passed: bool = False,
    ):
        self.pacing_secs = pacing_secs
        self.last_agy_start: float = 0.0
        self.slot_quota_status: Dict[str, str] = {"agy": "OK", "codex-local": "OK"}
        self.slot_health_status: Dict[str, str] = {"agy": "HEALTH_OK", "codex-local": "HEALTH_OK"}
        self.pilot_passed: bool = pilot_passed
        self.incidents: List[Dict[str, Any]] = []

        self.ledger = ledger or RoutingLedger()
        self.lease_manager = lease_manager or DurableLeaseManager()

    def set_slot_quota(self, slot: str, status: str):
        self.slot_quota_status[slot] = status

    def set_slot_health(self, slot: str, status: str):
        self.slot_health_status[slot] = status

    def set_pilot_passed(self, passed: bool):
        self.pilot_passed = passed

    def simulate_local_model_decision(self, task: Dict[str, Any], timeout_simulate: bool = False) -> Optional[str]:
        """
        Local Wolfram offline routing model decision logic.
        Evaluates task attributes (tier, wait time, quota, status) and outputs target slot.
        If timeout_simulate is True, exceeds latency budget and triggers deterministic fallback.
        """
        if timeout_simulate:
            return None

        # Extract tier from description or task
        description = task.get("description", "") or ""
        title = task.get("title", "") or ""
        combined_text = f"{title}\n{description}"
        tier_match = re.search(r"\bTier:\s*(ВС|ВП|МС|МП)\b", combined_text, re.IGNORECASE)
        tier = tier_match.group(1).upper() if tier_match else task.get("tier")
        status = task.get("status")
        wait_time = task.get("wait_time_secs", 0)
        agy_quota = self.slot_quota_status.get("agy", "OK")

        # Fallback conditions according to §3.3:
        # Tier is ВС or ВП, and wait > 2h in Todo OR agy quota exhausted
        time_trigger = (wait_time > 7200 and status == "Todo")
        quota_trigger = (agy_quota != "OK")
        if tier in {"ВС", "ВП"} and (time_trigger or quota_trigger):
            return "codex-local"
        return "agy"

    def route_and_claim_task(
        self,
        task: Dict[str, Any],
        lease_ttl_secs: int = 3600,
        model_timeout: bool = False,
        commit_sha: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], str]:
        """
        Routes and claims a task atomically.
        All decisions, rejections, and incidents are recorded into the persistent routing ledger.
        Returns: (success, slot_or_none, message_or_verdict)
        """
        task_id = task.get("id")
        if not task_id:
            return False, None, "INVALID_TASK_ID"

        # 1. Canonical validation gate check
        verdict = validate_canonical_rules(task)
        if not verdict.passed:
            self.ledger.record_decision({
                "task_id": task_id,
                "event": "CANONICAL_GATE_REJECTED",
                "reason": verdict.reason,
                "rework_comment": verdict.rework_comment,
                "tier": task.get("tier"),
            })
            return False, None, verdict.reason

        # 2. Check for active durable lease (Single-lease protection)
        existing_lease = self.lease_manager.get_lease(task_id)
        if existing_lease:
            incident = {
                "task_id": task_id,
                "event": "DOUBLE_CLAIM_REJECTED",
                "existing_slot": existing_lease.slot,
                "existing_commit": existing_lease.commit_sha,
                "timestamp": time.time(),
            }
            self.incidents.append(incident)
            self.ledger.record_decision({
                "task_id": task_id,
                "event": "DOUBLE_CLAIM_REJECTED",
                "slot": existing_lease.slot,
            })
            return False, None, "DOUBLE_CLAIM_REJECTED"

        # 3. Model routing decision with deterministic fallback
        suggested_slot = self.simulate_local_model_decision(task, timeout_simulate=model_timeout)
        router_mode = "model_offline" if suggested_slot is not None else "deterministic_fallback"

        if suggested_slot is None:
            incident = {
                "task_id": task_id,
                "event": "MODEL_ROUTER_UNAVAILABLE",
                "timestamp": time.time(),
            }
            self.incidents.append(incident)
            self.ledger.record_decision({
                "task_id": task_id,
                "event": "MODEL_ROUTER_UNAVAILABLE",
                "fallback": "deterministic_rules",
            })

        tier = verdict.extracted_tier or task.get("tier")
        status = task.get("status")
        wait_time_secs = task.get("wait_time_secs", 0)
        agy_quota = self.slot_quota_status.get("agy", "OK")
        codex_quota = self.slot_quota_status.get("codex-local", "OK")
        codex_health = self.slot_health_status.get("codex-local", "HEALTH_OK")
        fallback_active_count = self.lease_manager.count_active_fallback()

        # 4. Strict evaluation of fallback eligibility (§3.3, §6)
        # Conditions:
        # - Strictly ВС or ВП
        # - wait_time > 7200 in Todo OR agy quota exhausted
        # - concurrent fallback <= 1
        # - codex-local healthy and quota OK
        # - pilot passed (§6)
        time_trigger = (wait_time_secs > 7200 and status == "Todo")
        quota_trigger = (agy_quota != "OK")
        triggers_met = time_trigger or quota_trigger

        is_candidate_by_policy = (
            tier in {"ВС", "ВП"}
            and triggers_met
            and codex_health == "HEALTH_OK"
            and codex_quota == "OK"
            and fallback_active_count < 1
        )
        is_fallback_candidate = (is_candidate_by_policy and self.pilot_passed is True)

        # 5. Resolve target slot based on model recommendation and policy guards
        if suggested_slot == "codex-local":
            if is_fallback_candidate:
                target_slot = "codex-local"
            elif is_candidate_by_policy and not self.pilot_passed:
                # Fallback conditions met, but pilot gate is not passed -> fail closed!
                incident = {
                    "task_id": task_id,
                    "event": "PILOT_GATE_BLOCKED",
                    "reason": "PILOT_NOT_PASSED",
                    "timestamp": time.time(),
                }
                self.incidents.append(incident)
                self.ledger.record_decision({
                    "task_id": task_id,
                    "event": "PILOT_GATE_BLOCKED",
                    "slot": "codex-local",
                    "reason": "PILOT_NOT_PASSED",
                })
                return False, None, "PILOT_GATE_BLOCKED_FALLBACK_DISABLED"
            else:
                # Model recommended codex-local but constraints are not met -> fail-closed safety!
                # If agy quota is exhausted, fail-closed without silent transfer
                if agy_quota != "OK":
                    reason = "SLOT_QUOTA_EXHAUSTED_AGY"
                    self.ledger.record_decision({
                        "task_id": task_id,
                        "event": "QUOTA_FAIL_CLOSED",
                        "slot": "agy",
                        "reason": reason,
                    })
                    return False, None, reason
                target_slot = "agy"
        else:
            # Model suggested agy or deterministic default
            if agy_quota != "OK":
                if is_fallback_candidate:
                    target_slot = "codex-local"
                elif is_candidate_by_policy and not self.pilot_passed:
                    self.ledger.record_decision({
                        "task_id": task_id,
                        "event": "PILOT_GATE_BLOCKED",
                        "slot": "codex-local",
                        "reason": "PILOT_NOT_PASSED",
                    })
                    return False, None, "PILOT_GATE_BLOCKED_FALLBACK_DISABLED"
                else:
                    reason = "SLOT_QUOTA_EXHAUSTED_AGY"
                    self.ledger.record_decision({
                        "task_id": task_id,
                        "event": "QUOTA_FAIL_CLOSED",
                        "slot": "agy",
                        "reason": reason,
                    })
                    return False, None, reason
            else:
                target_slot = "agy"

        # 6. Final fail-closed quota check on the selected slot
        if self.slot_quota_status.get(target_slot) != "OK":
            reason = f"SLOT_QUOTA_EXHAUSTED_{target_slot.upper()}"
            self.ledger.record_decision({
                "task_id": task_id,
                "event": "QUOTA_FAIL_CLOSED",
                "slot": target_slot,
                "reason": reason,
            })
            return False, None, reason

        # 7. Apply pacing interval for agy (all runs, regardless of wait_time_secs)
        agy_busy = (time.time() - self.last_agy_start) < self.pacing_secs
        if target_slot == "agy" and agy_busy:
            self.ledger.record_decision({
                "task_id": task_id,
                "event": "PACING_BACKOFF",
                "slot": "agy",
            })
            return False, None, "PACING_BACKOFF"

        # 8. Grant atomic durable lease
        acquired, err = self.lease_manager.acquire_lease(
            task_id=task_id,
            slot=target_slot,
            lease_ttl_secs=lease_ttl_secs,
            commit_sha=commit_sha,
        )
        if not acquired:
            self.ledger.record_decision({
                "task_id": task_id,
                "event": "DOUBLE_CLAIM_REJECTED",
                "slot": target_slot,
            })
            return False, None, err or "DOUBLE_CLAIM_REJECTED"

        if target_slot == "agy":
            self.last_agy_start = time.time()

        outcome = f"CLAIMED_{target_slot.upper()}_{router_mode}"
        self.ledger.record_decision({
            "task_id": task_id,
            "event": "TASK_CLAIMED",
            "slot": target_slot,
            "router_mode": router_mode,
            "tier": tier,
            "wait_time_secs": wait_time_secs,
            "commit_sha": commit_sha,
        })
        return True, target_slot, outcome

    def release_lease(self, task_id: str):
        self.lease_manager.release_lease(task_id)
