"""Idle Lane Screener and Thematic Webhook Poller for Agent Forum v0.1.

Modelled after linear-poller:
Allows idle agent lanes between tasks to screen unanswered forum questions matching
their agent-card domain subscriptions and emits thematic webhooks for urgent issues.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional
from .security import SovereignEnforcementError, validate_sovereign_url
from .storage import Comment, Issue


class IdleLaneScreener:
    """Screens unanswered forum questions during idle periods between task runs."""

    DEFAULT_URGENT_KEYWORDS = [
        "urgent",
        "blocker",
        "critical",
        "p0",
        "priority:urgent",
        "priority:high",
        "domain:urgent",
    ]

    def __init__(
        self,
        lane_id: str,
        subscribed_domains: List[str],
        webhook_url: Optional[str] = None,
        urgent_keywords: Optional[List[str]] = None,
        webhook_handler: Optional[Callable[[Dict], None]] = None,
    ):
        self.lane_id = lane_id
        self.subscribed_domains = subscribed_domains
        self.webhook_url = webhook_url
        if self.webhook_url:
            validate_sovereign_url(self.webhook_url)
        self.urgent_keywords = urgent_keywords or self.DEFAULT_URGENT_KEYWORDS
        self.webhook_handler = webhook_handler
        self.screened_count = 0
        self.emitted_webhooks: List[Dict] = []

    def is_urgent(self, issue: Issue) -> bool:
        """Determine if an issue is urgent based on keywords in title, body, or domains."""
        content = f"{issue.title} {issue.body} {' '.join(issue.domains)}".lower()
        return any(kw.lower() in content for kw in self.urgent_keywords)

    def emit_thematic_webhook(self, issue: Issue, event_type: str = "urgent_unanswered_question") -> bool:
        """Emit a thematic webhook notification for an urgent unanswered question."""
        if self.webhook_url:
            validate_sovereign_url(self.webhook_url)

        payload = {
            "event": event_type,
            "lane_id": self.lane_id,
            "issue_id": issue.id,
            "title": issue.title,
            "domains": issue.domains,
            "author": issue.author,
            "timestamp": time.time(),
            "sovereign_source": "agent-forum/poller",
        }
        self.emitted_webhooks.append(payload)

        # 1. Custom in-process webhook handler
        if self.webhook_handler:
            try:
                self.webhook_handler(payload)
            except Exception:
                pass

        # 2. HTTP POST dispatch via urllib if webhook_url is configured
        if self.webhook_url:
            try:
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self.webhook_url,
                    data=req_data,
                    headers={"Content-Type": "application/json", "User-Agent": f"AgentForum-Poller/{self.lane_id}"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    return resp.status in (200, 201, 202, 204)
            except Exception:
                return False
        return True

    def screen_open_questions(self, forum, unanswered_only: bool = True) -> List[Issue]:
        """Screen open issues matching subscribed domains.

        Strict semantic: By default, screens UNANSWERED questions only.
        Already-answered questions are excluded.
        """
        matching_issues: List[Issue] = []
        all_open_issues = forum.backend.list_issues(status="open")

        for issue in all_open_issues:
            # Enforce unanswered semantics: questions with existing answers are excluded
            if unanswered_only and len(issue.comments) > 0:
                continue

            # Check domain intersection
            if any(domain in self.subscribed_domains for domain in issue.domains):
                matching_issues.append(issue)
                # Check for urgent notification trigger
                if self.is_urgent(issue):
                    self.emit_thematic_webhook(issue)

        self.screened_count += len(matching_issues)
        return matching_issues

    def provide_advisory(
        self,
        forum,
        issue_id: int,
        advice: str,
        confidence: str = "medium",
        stance: Optional[str] = None,
    ) -> Comment:
        """Provide advisory response during idle time."""
        author = f"{self.lane_id} [idle-screener]"
        return forum.post_answer(
            issue_id=issue_id,
            body=advice,
            author=author,
            confidence=confidence,
            stance=stance,
        )

    def poll_once(self, forum) -> Dict[str, int]:
        """Execute one polling pass across sovereign Forgejo forum."""
        open_issues = self.screen_open_questions(forum)
        return {
            "lane_id": self.lane_id,
            "matching_open_issues": len(open_issues),
            "total_screened": self.screened_count,
            "urgent_webhooks_emitted": len(self.emitted_webhooks),
        }
