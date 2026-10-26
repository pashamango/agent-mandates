"""
Unit and Acceptance Tests for Handoff Gate (MIN-236)
Tests the 4 mandatory rules of the handoff gate and regression traces for PRD-35 & MIN-194.
"""

import unittest
from router.handoff_gate import validate_handoff_gate, HandoffVerdict


VALID_REPORT = """
### agy build report: `MIN-236`
<!-- agy-run:run-049797a1b724 -->
**Run ID:** `run-049797a1b724` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-236: Handoff gate: сдача без координат не принимается; локально = BLOCKER, а не COMPLETED

#### Summary
1. Codified canonical protocol in HANDOFF-GATE-PROTOCOL.md.
2. Updated AGENTS.md with 4 mandatory rules.

#### Evidence / Coordinates (ОБЯЗАТЕЛЬНЫЙ РАЗДЕЛ ГЕЙТА)
- **Repository:** `https://github.com/pashamango/agent-mandates.git`
- **Branch:** `minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno`
- **Commit:** `4afdca1823a4494486f92720aaa2b6d4e1f30d41`
- **Artifact Paths:**
  - `HANDOFF-GATE-PROTOCOL.md`
  - `AGENTS.md`
  - `fleetd-evolution/router/handoff_gate.py`
- **Independent Verification Command:**
  ```bash
  git clone https://github.com/pashamango/agent-mandates.git verify-box && cd verify-box && git checkout minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno
  ```

#### Checks
- [x] A1: Template published in canonical location.
- [x] A2: Gate rule unambiguous.
- [x] A3: Local = BLOCKER codified.
- [x] A4: QA rule evidence-unavailable codified.
- [x] A5: Paper regressions completed.
- [x] A6: Code implementation with gate tests.
"""

PRD_35_FAILING_TRACE = """
### agy build report: `PRD-35`
<!-- agy-run:run-d40407bfd3e6 -->
**Run ID:** `run-d40407bfd3e6` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** PRD-35: Idea / PRD: Symbiotic Hiring Protocol (automated proof of JD fit)

#### Summary
Спецификация Symbiotic Hiring Protocol подготовлена. Файл сохранен в корне workspace-репозитория как spec-symbiotic-hiring-protocol-v0.1.md.

---
@Codex — проведи независимый QA этой задачи по критериям приёмки.
"""

MIN_194_FAILING_TRACE = """
### codex build report: `MIN-194`
<!-- codex-run:run-min194-bad -->
**Run ID:** `run-min194-bad` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
- **Task:** MIN-194: spec fleetd self-evolution

#### Summary
Изменения внесены только локально.

#### Evidence / Artifacts
- `fleetd-self-evolution.md`

---
@agy — проведи независимый QA.
"""


class TestHandoffGate(unittest.TestCase):
    def test_valid_complete_report_passes_gate(self):
        """Rule 1 & 2: Complete report with all 4 coordinates and verification command passes gate."""
        verdict = validate_handoff_gate(
            VALID_REPORT,
            expected_branch="minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno",
        )
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.reason, "COORDINATES_VERIFIED")
        self.assertFalse(verdict.is_blocker)
        coords = verdict.extracted_coordinates
        self.assertEqual(coords["repo"], "https://github.com/pashamango/agent-mandates.git")
        self.assertEqual(
            coords["branch"],
            "minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno",
        )
        self.assertEqual(coords["commit"], "4afdca1823a4494486f92720aaa2b6d4e1f30d41")
        self.assertIn("HANDOFF-GATE-PROTOCOL.md", coords["paths"])
        self.assertIn("git clone", coords["verification_cmd"])

    def test_missing_repo_fails_gate(self):
        """Rule 1: Report missing repo triggers auto-rework at gate."""
        report = VALID_REPORT.replace(
            "- **Repository:** `https://github.com/pashamango/agent-mandates.git`",
            "",
        )
        verdict = validate_handoff_gate(report)
        self.assertFalse(verdict.passed)
        self.assertIn("repo", verdict.missing_coordinates)
        self.assertIn("evidence unavailable", verdict.auto_rework_comment)

    def test_missing_branch_fails_gate(self):
        """Rule 1: Report missing branch triggers auto-rework at gate."""
        report = VALID_REPORT.replace(
            "- **Branch:** `minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno`",
            "",
        )
        verdict = validate_handoff_gate(report)
        self.assertFalse(verdict.passed)
        self.assertIn("branch", verdict.missing_coordinates)

    def test_missing_commit_fails_gate(self):
        """Rule 1: Report missing commit SHA triggers auto-rework at gate."""
        report = VALID_REPORT.replace(
            "- **Commit:** `4afdca1823a4494486f92720aaa2b6d4e1f30d41`",
            "",
        )
        verdict = validate_handoff_gate(report)
        self.assertFalse(verdict.passed)
        self.assertIn("commit", verdict.missing_coordinates)

    def test_missing_paths_fails_gate(self):
        """Rule 1: Report missing artifact paths triggers auto-rework at gate."""
        lines = [line for line in VALID_REPORT.splitlines() if not line.strip().startswith("- `")]
        report = "\n".join(lines).replace("- **Artifact Paths:**", "")
        verdict = validate_handoff_gate(report)
        self.assertFalse(verdict.passed)
        self.assertIn("path", verdict.missing_coordinates)

    def test_branch_mismatch_fails_gate(self):
        """Rule 1: Branch mismatch against Linear ticket gitBranchName triggers auto-rework."""
        verdict = validate_handoff_gate(
            VALID_REPORT,
            expected_branch="minutta/some-other-branch",
        )
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.reason, "BRANCH_MISMATCH")

    def test_acceptance_a5_prd35_regression_intercepted_at_gate(self):
        """Acceptance A5: PRD-35 trace run-d40407bfd3e6 is intercepted at gate with auto-REWORK; no QA cycle."""
        verdict = validate_handoff_gate(PRD_35_FAILING_TRACE, builder="agy")
        self.assertFalse(verdict.passed)
        # Violates Rule 3 ("Локально — значит BLOCKER") and Rule 1 (Missing coordinates)
        self.assertTrue(
            verdict.reason in {"LOCAL_ONLY_COMPLETED_FORBIDDEN", "MISSING_COORDINATES"}
        )
        self.assertTrue(verdict.is_blocker)
        self.assertIn("Локально — значит BLOCKER", verdict.auto_rework_comment)
        self.assertIn("нет пуша — нет сдачи", verdict.auto_rework_comment)
        # Assures Codex is not tasked with searching quantum-chemistry

    def test_acceptance_a5_min194_regression_intercepted_at_gate(self):
        """Acceptance A5: MIN-194 trace with local-only unpushed changes is intercepted at gate."""
        verdict = validate_handoff_gate(MIN_194_FAILING_TRACE, builder="codex")
        self.assertFalse(verdict.passed)
        self.assertTrue(
            verdict.reason in {"LOCAL_ONLY_COMPLETED_FORBIDDEN", "MISSING_COORDINATES"}
        )
        self.assertTrue(
            "evidence unavailable" in verdict.auto_rework_comment
            or "нет пуша — нет сдачи" in verdict.auto_rework_comment
        )

    def test_rule_3_explicit_blocker_accepted(self):
        """Rule 3: Report properly classified as BLOCKER when unpushed is accepted as BLOCKER (not COMPLETED)."""
        blocker_report = """
### agy build report: `PRD-35`
<!-- agy-run:run-d40407bfd3e6 -->
**Run ID:** `run-d40407bfd3e6` | **Command:** `START` | **Status:** 🛑 BLOCKER

#### Outcome
- **Status:** BLOCKER
- **Task:** PRD-35

#### Blocker Details
- **Причина:** Результат существует только локально; отсутствует удалённый пуш («нет пуша — нет сдачи»).
- **Требуемое действие:** Предоставить права на push в origin.
"""
        verdict = validate_handoff_gate(blocker_report, builder="agy")
        self.assertFalse(verdict.passed)
        self.assertTrue(verdict.is_blocker)
        self.assertEqual(verdict.reason, "BUILDER_DECLARED_BLOCKER")

    def test_rule_4_qa_no_search_mandate_in_rework(self):
        """Rule 4: Auto-rework comment explicitly commands QA not to search unnamed repositories."""
        incomplete_report = VALID_REPORT.replace(
            "- **Commit:** `4afdca1823a4494486f92720aaa2b6d4e1f30d41`", ""
        )
        verdict = validate_handoff_gate(incomplete_report, builder="agy")
        self.assertIn("поиск в неназванных репозиториях запрещён", verdict.auto_rework_comment)


    def test_protocol_phi_envelope_parsing(self):
        """Rule 1 & Protocol Phi: Reports with embedded phi-envelope pass gate with extracted coordinates."""
        phi_report = """
### agy build report: `MIN-236`
<!-- agy-run:run-049797a1b724 -->
<!-- phi-envelope:{
  "proto": "phi-0.1",
  "intent": "HANDOFF_FOR_QA",
  "task_id": "MIN-236",
  "run_id": "run-049797a1b724",
  "status": "COMPLETED",
  "git": {
    "repo_url": "https://github.com/pashamango/agent-mandates.git",
    "branch": "minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno",
    "commit_sha": "d6c9218223a4494486f92720aaa2b6d4e1f30d41",
    "artifact_paths": ["HANDOFF-GATE-PROTOCOL.md", "AGENTS.md"],
    "verification_cmd": "git clone https://github.com/pashamango/agent-mandates.git && cd agent-mandates && git checkout minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno"
  }
} -->
**Run ID:** `run-049797a1b724` | **Command:** `START` | **Status:** ✅ COMPLETED

#### Outcome
- **Status:** READY_FOR_REVIEW
"""
        verdict = validate_handoff_gate(phi_report, expected_branch="minutta/min-236-handoff-gate-sdacha-bez-koordinat-ne-prinimaetsya-lokalno")
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.reason, "COORDINATES_VERIFIED")
        self.assertEqual(verdict.extracted_coordinates["commit"], "d6c9218223a4494486f92720aaa2b6d4e1f30d41")


if __name__ == "__main__":
    unittest.main()
