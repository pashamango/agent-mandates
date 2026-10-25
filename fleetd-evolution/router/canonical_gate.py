"""
Canonical Gate (fleetd vNext - Section 4)
Enforces deterministic pre-dispatch rules for incoming Linear tasks:
1. Linear project is mandatory.
2. Tier (Tier: ВС/ВП/МС/МП) is mandatory.
3. Submitted applications (MP tier post-submission) are frozen.
4. Builder markers must be matched with strict word boundaries.
"""

import re
from typing import Dict, Any, Tuple, Optional

BUILDER_REGEX = re.compile(r"\bBuilder:\s*(agy|codex)\b", re.IGNORECASE)
CLOUD_BUILDER_REGEX = re.compile(r"\bBuilder:\s*codex-cloud\b", re.IGNORECASE)
TIER_REGEX = re.compile(r"\bTier:\s*(ВС|ВП|МС|МП)\b", re.IGNORECASE)


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

    # 4. Marker discipline check (word boundaries & cloud lane isolation)
    if CLOUD_BUILDER_REGEX.search(combined_text):
        # Explicit codex-cloud task — local fleetd must not touch it
        return CanonicalVerdict(
            passed=False,
            reason="CODEX_CLOUD_LANE",
            rework_comment=None,  # Not a rework, just not for local fleetd
        )

    # Check for ambiguous marker formats like "Builder:codex-something"
    raw_builder_matches = re.findall(r"Builder:\s*([^\s\n\r]+)", combined_text, re.IGNORECASE)
    for m in raw_builder_matches:
        cleaned = m.strip("[](),.")
        if cleaned.lower() not in {"agy", "codex", "codex-cloud", "gemini"}:
            return CanonicalVerdict(
                passed=False,
                reason="AMBIGUOUS_BUILDER_MARKER",
                rework_comment=f"@PM REWORK: Неоднозначный маркер билдера 'Builder: {cleaned}'. Исправьте маркировку лейна на agy или codex.",
            )

    return CanonicalVerdict(passed=True, reason="CANONICAL_PASS")
