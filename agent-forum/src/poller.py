"""Idle Lane Screener for Agent Forum v0.1.

Modelled after linear-poller:
Allows idle agent lanes between tasks to screen open forum questions matching
their agent-card domain subscriptions.
"""

from typing import Dict, List, Optional
from .forum import ForumClient, Issue, Comment


class IdleLaneScreener:
    """Screens forum questions during idle periods between task runs."""

    def __init__(self, lane_id: str, subscribed_domains: List[str]):
        self.lane_id = lane_id
        self.subscribed_domains = subscribed_domains
        self.screened_count = 0

    def screen_open_questions(self, forum: ForumClient) -> List[Issue]:
        """Screen open issues that match subscribed domains and have pending questions."""
        matching_issues: List[Issue] = []
        for issue in forum.backend.issues.values():
            if issue.status != "open":
                continue
            # Check domain intersection
            if any(domain in self.subscribed_domains for domain in issue.domains):
                matching_issues.append(issue)
        self.screened_count += len(matching_issues)
        return matching_issues

    def provide_advisory(
        self,
        forum: ForumClient,
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

    def poll_once(self, forum: ForumClient) -> Dict[str, int]:
        """Execute one polling pass across sovereign Forgejo forum."""
        open_issues = self.screen_open_questions(forum)
        return {
            "lane_id": self.lane_id,
            "matching_open_issues": len(open_issues),
            "total_screened": self.screened_count,
        }
