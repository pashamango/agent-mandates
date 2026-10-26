"""
Handoff Gate Protocol Validator (MIN-236 / fleetd self-evolution)
Enforces the 4 mandatory rules for transitioning builder deliveries into In Review:
1. Gate check: No coordinates -> No In Review. All 4 coordinates (repo, branch, commit, path)
   + verification command must be verifiable. Missing/truncated evidence -> auto-REWORK at gate.
2. Builder report template enforcement with mandatory Evidence section.
3. Rule "Local = BLOCKER / Нет пуша — нет сдачи": local-only unpushed results CANNOT be COMPLETED;
   they must be classified as BLOCKER detailing missing repo/access.
4. QA rule "Нет координат — нет поиска": verdict is strictly 'evidence unavailable, верните координаты'
   (class evidence-unavailable). Searching in unmentioned repositories is prohibited.
"""

import json
import re
from typing import Dict, Any, List, Optional


REPO_REGEX = re.compile(
    r"\*{0,2}(?:Repository|repo_url|repo)\*{0,2}\s*:?\s*\*{0,2}\s*[`'\"]?(https?://[^\s`'\"]+|git@[^\s`'\"]+)[`'\"]?",
    re.IGNORECASE,
)
BRANCH_REGEX = re.compile(
    r"\*{0,2}(?:Branch|git_branch|branch)\*{0,2}\s*:?\s*\*{0,2}\s*[`'\"]?([a-zA-Z0-9_\-\./]+)[`'\"]?",
    re.IGNORECASE,
)
COMMIT_REGEX = re.compile(
    r"\*{0,2}(?:Commit|commit_sha|commit)\*{0,2}\s*:?\s*\*{0,2}\s*[`'\"]?([0-9a-fA-F]{7,40})[`'\"]?",
    re.IGNORECASE,
)
VERIFY_CMD_REGEX = re.compile(
    r"(?:Verification Command|verification_cmd|команда проверки).*?```(?:bash|sh)?\s*(git\s+[^`]+?)\s*```",
    re.DOTALL | re.IGNORECASE,
)
FALLBACK_VERIFY_CMD_REGEX = re.compile(
    r"[`'\"](git\s+(?:clone|checkout|fetch)[^`'\"]+)[\"']",
    re.IGNORECASE,
)
STATUS_COMPLETED_REGEX = re.compile(
    r"Status:\s*[^\n\r]*?\b(?:COMPLETED|READY_FOR_REVIEW)\b",
    re.IGNORECASE,
)
STATUS_BLOCKER_REGEX = re.compile(
    r"Status:\s*[^\n\r]*?\bBLOCKER\b",
    re.IGNORECASE,
)
PHI_ENVELOPE_REGEX = re.compile(
    r"<!--\s*phi-envelope:(.*?)\s*-->",
    re.DOTALL | re.IGNORECASE,
)

# Patterns signaling local-only memory/workspace claims without remote coordinates
LOCAL_ONLY_INDICATORS = [
    re.compile(r"в\s+корне\s+workspace", re.IGNORECASE),
    re.compile(r"только\s+локальн", re.IGNORECASE),
    re.compile(r"локальная\s+память", re.IGNORECASE),
    re.compile(r"без\s+пуша", re.IGNORECASE),
    re.compile(r"unpushed", re.IGNORECASE),
]


class HandoffVerdict:
    def __init__(
        self,
        passed: bool,
        reason: str,
        is_blocker: bool = False,
        missing_coordinates: Optional[List[str]] = None,
        extracted_coordinates: Optional[Dict[str, Any]] = None,
        auto_rework_comment: Optional[str] = None,
    ):
        self.passed = passed
        self.reason = reason
        self.is_blocker = is_blocker
        self.missing_coordinates = missing_coordinates or []
        self.extracted_coordinates = extracted_coordinates or {}
        self.auto_rework_comment = auto_rework_comment

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "is_blocker": self.is_blocker,
            "missing_coordinates": self.missing_coordinates,
            "extracted_coordinates": self.extracted_coordinates,
            "auto_rework_comment": self.auto_rework_comment,
        }


def extract_phi_envelope_git(report_text: str) -> Optional[Dict[str, Any]]:
    match = PHI_ENVELOPE_REGEX.search(report_text)
    if not match:
        return None
    try:
        raw_json = match.group(1).strip()
        data = json.loads(raw_json)
        return data.get("git")
    except Exception:
        return None


def extract_artifact_paths(report_text: str) -> List[str]:
    paths: List[str] = []
    lines = report_text.splitlines()
    in_artifacts_section = False
    for line in lines:
        stripped = line.strip()
        if "Artifact Paths:" in stripped or "Artifacts:" in stripped or "Evidence / Artifacts" in stripped:
            in_artifacts_section = True
            continue
        if in_artifacts_section:
            if stripped.startswith("- ") or stripped.startswith("* "):
                val = stripped[2:].strip().strip("`'\"")
                # ignore headings or key-values like "- **Repository:**"
                if not val.startswith("**") and val:
                    # Clean markdown links [name](path)
                    link_match = re.search(r"\[.*?\]\((.*?)\)", val)
                    if link_match:
                        val = link_match.group(1)
                    paths.append(val)
            elif stripped.startswith("#### ") or stripped.startswith("### ") or stripped.startswith("---") or stripped.startswith("- **Independent"):
                in_artifacts_section = False
    return paths


def validate_handoff_gate(
    report_text: str,
    expected_branch: Optional[str] = None,
    builder: str = "agy",
) -> HandoffVerdict:
    """
    Validates a builder completion report before allowing transition to In Review.
    Enforces Rule 1 (4 coordinates), Rule 2 (template), Rule 3 (local=BLOCKER), Rule 4 (QA no-search).
    """
    if not report_text or not report_text.strip():
        return HandoffVerdict(
            passed=False,
            reason="EMPTY_REPORT",
            auto_rework_comment=f"@{builder} REWORK: Отчёт билдера пуст. Предоставьте отчёт по шаблону HANDOFF-GATE-PROTOCOL.md с обязательным разделом Evidence.",
        )

    # 1. Check for explicit BLOCKER declaration (Rule 3)
    if STATUS_BLOCKER_REGEX.search(report_text):
        return HandoffVerdict(
            passed=False,
            reason="BUILDER_DECLARED_BLOCKER",
            is_blocker=True,
            auto_rework_comment=f"@{builder} Задача остановлена в статусе BLOCKER. Необходимы недостающие ресурсы/доступы.",
        )

    # 2. Extract coordinates from Phi envelope or markdown text
    phi_git = extract_phi_envelope_git(report_text) or {}

    repo = phi_git.get("repo_url")
    if not repo:
        repo_match = REPO_REGEX.search(report_text)
        if repo_match:
            repo = repo_match.group(1).strip()

    branch = phi_git.get("branch")
    if not branch:
        branch_match = BRANCH_REGEX.search(report_text)
        if branch_match:
            branch = branch_match.group(1).strip()

    commit = phi_git.get("commit_sha")
    if not commit:
        commit_match = COMMIT_REGEX.search(report_text)
        if commit_match:
            commit = commit_match.group(1).strip()

    artifact_paths = phi_git.get("artifact_paths") or extract_artifact_paths(report_text)

    verification_cmd = phi_git.get("verification_cmd")
    if not verification_cmd:
        cmd_match = VERIFY_CMD_REGEX.search(report_text)
        if cmd_match:
            verification_cmd = cmd_match.group(1).strip()
        else:
            fb_match = FALLBACK_VERIFY_CMD_REGEX.search(report_text)
            if fb_match:
                verification_cmd = fb_match.group(1).strip()

    missing: List[str] = []
    if not repo:
        missing.append("repo")
    if not branch:
        missing.append("branch")
    if not commit:
        missing.append("commit")
    if not artifact_paths:
        missing.append("path")

    # 3. Rule 3 Enforcement: "Локально — значит BLOCKER"
    # Check if report claims COMPLETED but has local-only indicators and missing push coordinates
    has_local_indicator = any(pattern.search(report_text) for pattern in LOCAL_ONLY_INDICATORS)
    is_claiming_completed = bool(STATUS_COMPLETED_REGEX.search(report_text))

    if is_claiming_completed and (has_local_indicator or len(missing) >= 3):
        return HandoffVerdict(
            passed=False,
            reason="LOCAL_ONLY_COMPLETED_FORBIDDEN",
            is_blocker=True,
            missing_coordinates=missing,
            auto_rework_comment=(
                f"@{builder} REWORK: Нарушение правила «Локально — значит BLOCKER» (MIN-236 Rule 3).\n"
                f"Результат существует только в локальной памяти/диске без пуша в origin. "
                f"Статус COMPLETED запрещён («нет пуша — нет сдачи»). "
                f"Переклассифицируйте задачу в BLOCKER с указанием отсутствующего репозитория или прав доступа."
            ),
        )

    # 4. Rule 1 Enforcement: Missing coordinates = Auto-REWORK at gate
    if missing:
        missing_str = ", ".join(missing)
        return HandoffVerdict(
            passed=False,
            reason="MISSING_COORDINATES",
            missing_coordinates=missing,
            auto_rework_comment=(
                f"@{builder} REWORK: evidence unavailable, верните полные координаты сдачи.\n"
                f"Текущий отчёт не проходит Handoff Gate (отсутствуют обязательные координаты: {missing_str}).\n"
                f"Тикет не может быть переведён в In Review без полного кортежа: repo + branch + commit + path + verification command.\n"
                f"В соответствии с QA-правилом (MIN-236 Rule 4) поиск в неназванных репозиториях запрещён."
            ),
        )

    # 5. Check expected branch if provided
    if expected_branch and branch != expected_branch:
        return HandoffVerdict(
            passed=False,
            reason="BRANCH_MISMATCH",
            missing_coordinates=["branch_mismatch"],
            auto_rework_comment=(
                f"@{builder} REWORK: Ветка в отчёте ('{branch}') не совпадает с закрепленной веткой тикета ('{expected_branch}')."
            ),
        )

    # 6. Verification command mandatory check
    if not verification_cmd:
        return HandoffVerdict(
            passed=False,
            reason="MISSING_VERIFICATION_COMMAND",
            missing_coordinates=["verification_command"],
            auto_rework_comment=(
                f"@{builder} REWORK: Отсутствует воспроизводимая команда независимой проверки (verification command). "
                f"Укажите команду в блоке Evidence по шаблону HANDOFF-GATE-PROTOCOL.md."
            ),
        )

    # All checks passed
    extracted = {
        "repo": repo,
        "branch": branch,
        "commit": commit,
        "paths": artifact_paths,
        "verification_cmd": verification_cmd,
    }
    return HandoffVerdict(
        passed=True,
        reason="COORDINATES_VERIFIED",
        extracted_coordinates=extracted,
    )
