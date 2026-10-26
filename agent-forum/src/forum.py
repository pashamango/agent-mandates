"""Core Forum Engine for Agent Forum v0.1 on sovereign Forgejo stack.

Mapping:
- Question = Forgejo Issue
- Answer = Forgejo Comment
- Domain = Forgejo Label
- Standardization Trigger = Linear PM Task (Pavel Cherkashin tap only)
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .forgejo import ForgejoBackend, ForgejoClient, SovereignEnforcementError
from .linear import LinearClient
from .security import (
    AuthorizationDeniedError,
    RightsChecker,
    SecretRejectionError,
    SecretScanner,
)
from .storage import CanonizationTask, Comment, ForumStorage, Issue


@dataclass
class AskResult:
    """Outcome of an ask_question operation."""
    issue_id: int
    joined_duplicate: bool
    answered: bool
    uncertainty_marked: bool
    confidence: str
    body: str
    requires_canonical_verification: bool
    comments: List[Comment] = field(default_factory=list)


class ForumClient:
    """Client for interacting with Agent Forum v0.1."""

    def __init__(
        self,
        backend: Optional[ForgejoBackend] = None,
        linear: Optional[LinearClient] = None,
        storage_path: Optional[str] = None,
        offline: bool = False,
    ):
        if backend:
            self.backend = backend
        else:
            self.backend = ForgejoBackend(storage_path=storage_path, offline=offline)

        self.storage = self.backend.storage
        self.linear = linear or LinearClient(storage=self.storage)
        if self.linear.storage is None:
            self.linear.storage = self.storage
        self.next_task_id = 1

    @property
    def canonization_tasks(self) -> List[CanonizationTask]:
        """Persistent list of canonization tasks for the PM."""
        return self.storage.list_canonization_tasks()

    def search_questions(self, query: str, domain: Optional[str] = None) -> List[Issue]:
        """Search existing questions to implement the search-first protocol."""
        return self.backend.search_issues(query, domain)

    def ask_question(
        self,
        title: str,
        body: str,
        domains: List[str],
        author: str,
        timeout_sec: Optional[float] = None,
    ) -> AskResult:
        """Ask a question or join an existing duplicate thread.

        Includes:
        1. Fail-closed secret and PII rejection.
        2. Rights / permission checking with canonical disclaimer injection.
        3. Search-First duplicate join with idempotent PM canonization trigger.
        4. Real timeout execution loop with active polling and uncertainty marking.
        """
        # 1. Fail-closed Secret and PII Rejection Check
        SecretScanner.scan(title)
        SecretScanner.scan(body)

        # 2. Check for rights / permission inquiries
        is_rights = RightsChecker.check_rights_question(f"{title} {body}")
        enriched_body = body
        if is_rights:
            enriched_body = f"{body}\n\n{RightsChecker.NOTICE}"

        # 3. Search-First Protocol: check for duplicate question
        existing_matches = self.search_questions(title, domains[0] if domains else None)
        for existing in existing_matches:
            # Match on identical normalized title
            if title.strip().lower() == existing.title.strip().lower():
                existing.duplicate_count += 1
                existing.askers.add(author)

                # Idempotent canonization trigger for duplicates: fire exactly once at threshold
                if existing.duplicate_count >= 3 and not existing.canonization_triggered_duplicate:
                    existing.canonization_triggered_duplicate = True
                    self._check_and_trigger_canonization(
                        existing,
                        "Duplicate question threshold reached (>= 3 askers)",
                    )

                # Persist updated issue state
                self.storage.save_issue(existing)

                # Refresh latest comments from backend/storage
                refreshed = self.backend.get_issue(existing.id) or existing
                has_answers = len(refreshed.comments) > 0

                # If already answered, return immediately with answer
                if has_answers:
                    return AskResult(
                        issue_id=existing.id,
                        joined_duplicate=True,
                        answered=True,
                        uncertainty_marked=False,
                        confidence="medium",
                        body=existing.body,
                        requires_canonical_verification=existing.requires_canonical_verification,
                        comments=refreshed.comments,
                    )

                # If UNANSWERED and timeout requested: apply real execution timeout loop!
                if timeout_sec is not None and timeout_sec > 0:
                    deadline = time.time() + timeout_sec
                    poll_interval = min(0.05, timeout_sec / 10.0)
                    while time.time() < deadline:
                        refreshed = self.backend.get_issue(existing.id)
                        if refreshed and len(refreshed.comments) > 0:
                            break
                        time.sleep(poll_interval)

                    answered = bool(refreshed and len(refreshed.comments) > 0)
                    uncertainty_marked = not answered
                    confidence = "medium" if answered else "low"
                    comments = refreshed.comments if refreshed else []
                else:
                    # Non-blocking / immediate ask: do not mark uncertainty prematurely
                    answered = False
                    uncertainty_marked = False
                    confidence = "low"
                    comments = []

                return AskResult(
                    issue_id=existing.id,
                    joined_duplicate=True,
                    answered=answered,
                    uncertainty_marked=uncertainty_marked,
                    confidence=confidence,
                    body=existing.body,
                    requires_canonical_verification=existing.requires_canonical_verification,
                    comments=comments,
                )

        # 4. Create new issue in sovereign Forgejo
        new_issue = self.backend.create_issue(
            title=title,
            body=enriched_body,
            domains=domains,
            author=author,
            requires_verification=is_rights,
        )

        # 5. Handle execution window / real timeout
        if timeout_sec is not None and timeout_sec > 0:
            # Active polling loop until deadline or until answer arrives
            deadline = time.time() + timeout_sec
            poll_interval = min(0.05, timeout_sec / 10.0)
            while time.time() < deadline:
                refreshed = self.backend.get_issue(new_issue.id)
                if refreshed and len(refreshed.comments) > 0:
                    new_issue = refreshed
                    break
                time.sleep(poll_interval)

            answered = len(new_issue.comments) > 0
            uncertainty_marked = not answered
            confidence = "medium" if answered else "low"
        else:
            # Non-blocking / immediate ask: do not mark uncertainty prematurely
            answered = len(new_issue.comments) > 0
            uncertainty_marked = False
            confidence = "medium" if answered else "low"

        return AskResult(
            issue_id=new_issue.id,
            joined_duplicate=False,
            answered=answered,
            uncertainty_marked=uncertainty_marked,
            confidence=confidence,
            body=new_issue.body,
            requires_canonical_verification=is_rights,
            comments=new_issue.comments,
        )

    def post_answer(
        self,
        issue_id: int,
        body: str,
        author: str,
        confidence: str = "medium",
        stance: Optional[str] = None,
    ) -> Comment:
        """Post an answer (Forgejo comment) to a question."""
        # 1. Fail-closed Secret and PII Rejection Check
        SecretScanner.scan(body)

        # 2. Post comment and persist
        comment = self.backend.add_comment(
            issue_id=issue_id,
            author=author,
            body=body,
            confidence=confidence,
            stance=stance,
        )

        # 3. Check for contradiction between confident answers (idempotently)
        issue = self.backend.get_issue(issue_id)
        if issue:
            self._check_contradiction(issue)

        return comment

    def _check_contradiction(self, issue: Issue) -> None:
        """Check if there are 2 confident contradictory answers (idempotent trigger)."""
        if issue.canonization_triggered_contradiction:
            return

        confident_comments = [c for c in issue.comments if c.confidence == "high"]
        if len(confident_comments) >= 2:
            stances = [c.stance for c in confident_comments if c.stance]
            # Contradiction when distinct opposing stances exist
            has_conflict = False
            if len(set(stances)) > 1:
                has_conflict = True
            elif ("yes" in stances and "no" in stances) or ("approved" in stances and "denied" in stances):
                has_conflict = True
            elif ("prebuilt" in stances and "compile" in stances):
                has_conflict = True

            if has_conflict:
                issue.canonization_triggered_contradiction = True
                self.storage.save_issue(issue)
                self._check_and_trigger_canonization(
                    issue,
                    "Contradictory confident answers detected between multiple agents",
                )

    def _check_and_trigger_canonization(self, issue: Issue, reason: str) -> CanonizationTask:
        """Trigger standardization task for PM and register in Linear without modifying canon."""
        task_title = f"[CANONIZATION PROPOSAL] Resolve recurrent/conflicting Q&A #{issue.id}: {issue.title}"
        task_body = (
            f"Trigger Reason: {reason}\n"
            f"Forgejo Issue: #{issue.id}\n"
            f"Duplicate Count: {issue.duplicate_count}\n"
            f"Comments Count: {len(issue.comments)}\n\n"
            f"Assignee: {LinearClient.PM_ASSIGNEE} ({LinearClient.PM_EMAIL})\n"
            f"Note: AGENTS.md was NOT modified. Changes require Principal Pavel Cherkashin's human tap."
        )
        task_id = f"PM-CANON-{issue.id:04d}-{self.next_task_id:02d}"
        self.next_task_id += 1

        task = CanonizationTask(
            id=task_id,
            issue_id=issue.id,
            reason=reason,
            title=task_title,
            body=task_body,
            canon_changed=False,  # Hard invariant: NEVER changes canon automatically
            assignee=LinearClient.PM_ASSIGNEE,
            status="queued_for_pm_tap",
        )

        # Dispatch to Linear and persist in storage & outbox
        self.storage.save_canonization_task(task)
        self.linear.create_pm_canonization_task(task)
        return task

    def verify_authorization(self, action: str, forum_advice: Optional[str] = None) -> bool:
        """Hard rule: Advice is not authorization. Forum content cannot authorize restricted actions."""
        RESTRICTED_ACTIONS = {"mark_done", "push_to_main", "skip_qa", "access_vault", "bypass_gate"}
        if action in RESTRICTED_ACTIONS:
            if forum_advice:
                raise AuthorizationDeniedError(
                    f"Access denied: Action '{action}' cannot be authorized by forum advice. "
                    f"Task authority and human tap (Pavel Cherkashin) are strictly required."
                )
            raise AuthorizationDeniedError(f"Action '{action}' is restricted.")
        return True
