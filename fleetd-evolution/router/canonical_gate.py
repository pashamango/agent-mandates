"""
Canonical Gate (fleetd vNext - Section 4)
Enforces deterministic pre-dispatch rules for incoming Linear tasks:
1. Linear project is mandatory.
2. Tier (Tier: ВС/ВП/МС/МП) is mandatory.
3. Submitted applications (MP tier post-submission) are frozen.
4. Lane marker discipline and codex-cloud isolation contract:
   - codex-cloud lane requires strictly: assignee is Codex + @Codex mention.
   - Ambiguous textual markers (e.g. 'Builder: codex-cloud' without contract) return REWORK to PM.
5. Task status check: only 'Todo' or 'Backlog' can be dispatched.
"""

import re
from typing import Dict, Any, Optional

TIER_REGEX = re.compile(r"\bTier:\s*(ВС|ВП|МС|МП)\b", re.IGNORECASE)
RAW_BUILDER_REGEX = re.compile(r"Builder:\s*([^\s\n\r]+)", re.IGNORECASE)


class CanonicalVerdict:
    def __init__(self, passed: bool, reason: str, rework_comment: Optional[str] = None):
        self.passed = passed
        self.reason = reason
        self.rework_comment = rework_comment

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "rework_comment": self.rework_comment,
        }


def validate_canonical_rules(task: Dict[str, Any]) -> CanonicalVerdict:
    """
    Validates task against canonical rules before dispatching to any builder.
    Returns CanonicalVerdict.
    """
    # 0. Status check: tasks with status 'Done', 'Canceled', etc. are not dispatchable
    status = task.get("status")
    if status:
        normalized_status = status.strip().lower()
        if normalized_status in {"done", "completed", "closed"}:
            return CanonicalVerdict(
                passed=False,
                reason="ALREADY_DONE",
                rework_comment="@PM REWORK: Задача уже находится в статусе Done. Повторный захват запрещён.",
            )
        if normalized_status not in {"todo", "backlog"}:
            return CanonicalVerdict(
                passed=False,
                reason=f"INVALID_STATUS_{status.upper()}",
                rework_comment=f"@PM REWORK: Задача в статусе '{status}' не готова к выдаче. Допустимы только 'Todo' или 'Backlog'.",
            )

    # 1. Project mandatory check
    project = task.get("project")
    if not project or (isinstance(project, str) and not project.strip()):
        return CanonicalVerdict(
            passed=False,
            reason="MISSING_PROJECT",
            rework_comment="@PM REWORK: Задача не привязана к Linear Project. Привязка к проекту обязательна для выдачи билдеру.",
        )

    # 2. Tier mandatory check
    description = task.get("description", "") or ""
    title = task.get("title", "") or ""
    combined_text = f"{title}\n{description}"

    tier_match = TIER_REGEX.search(combined_text)
    if not tier_match:
        return CanonicalVerdict(
            passed=False,
            reason="MISSING_TIER",
            rework_comment="@PM REWORK: Отсутствует классификация тира задачи (Tier: ВС/ВП/МС/МП). Укажите тир в описании.",
        )
    tier = tier_match.group(1).upper()

    # 3. Submitted = Frozen check (Tier MP post-submission)
    if tier == "МП":
        is_submitted = (
            "submitted" in combined_text.lower()
            or "подана" in combined_text.lower()
            or "поданная заявка" in combined_text.lower()
            or "awaiting-tap" in combined_text.lower()
            or "cv-ready" in combined_text.lower()
        )
        has_exception = (
            "рекрутер попросил" in combined_text.lower()
            or "материальная ошибка" in combined_text.lower()
            or "recruiter-request" in combined_text.lower()
        )
        if is_submitted and not has_exception:
            return CanonicalVerdict(
                passed=False,
                reason="SUBMITTED_FROZEN",
                rework_comment="@PM REWORK: Задача тира МП уже отправлена (submitted). Дальнейшая полировка заморожена для сохранения квоты билдера.",
            )

    # 4. Codex-cloud isolation contract & Marker discipline (§3.1, §4)
    # Contract: codex-cloud is strictly identified by assignee (Codex user) + @Codex mention.
    assignee = str(task.get("assignee") or "").strip()
    assignee_id = str(task.get("assignee_id") or task.get("assigneeId") or "").strip()
    is_codex_assignee = (
        assignee.lower() == "codex"
        or assignee_id == "3bc09df8-b112-4d8d-8d0c-519043c77030"
    )
    has_codex_mention = bool(re.search(r"@codex\b", combined_text, re.IGNORECASE))

    # Check for text marker "Builder: codex-cloud"
    has_cloud_builder_text = bool(re.search(r"\bBuilder:\s*codex-cloud\b", combined_text, re.IGNORECASE))

    if is_codex_assignee and has_codex_mention:
        # Canonical codex-cloud lane: local fleetd must NOT claim it
        return CanonicalVerdict(
            passed=False,
            reason="CODEX_CLOUD_LANE",
            rework_comment=None,  # Not an error; task belongs strictly to cloud lane
        )

    if has_cloud_builder_text:
        # Ambiguous textual marker without formal assignee+mention contract
        return CanonicalVerdict(
            passed=False,
            reason="AMBIGUOUS_BUILDER_MARKER",
            rework_comment="@PM REWORK: Неоднозначная маркировка 'Builder: codex-cloud'. Облачные задачи Codex должны назначаться строго через assignee: Codex и @Codex.",
        )

    if is_codex_assignee and not has_codex_mention:
        return CanonicalVerdict(
            passed=False,
            reason="AMBIGUOUS_BUILDER_MARKER",
            rework_comment="@PM REWORK: Задача назначена на Codex, но отсутствует обязательное упоминание @Codex в тикете.",
        )

    # Check for other ambiguous builder markers like "Builder: codex-something"
    raw_builder_matches = RAW_BUILDER_REGEX.findall(combined_text)
    for m in raw_builder_matches:
        cleaned = m.strip("[](),.")
        if cleaned.lower() not in {"agy", "codex", "gemini"}:
            return CanonicalVerdict(
                passed=False,
                reason="AMBIGUOUS_BUILDER_MARKER",
                rework_comment=f"@PM REWORK: Неоднозначный маркер билдера 'Builder: {cleaned}'. Исправьте маркировку лейна на agy или codex.",
            )

    return CanonicalVerdict(passed=True, reason="CANONICAL_PASS")
