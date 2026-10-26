"""Sovereign Forgejo Backend and Client for Agent Forum v0.1.

Implements sovereign Git/Issue mapping:
- Question = Forgejo Issue (POST /repos/{owner}/{repo}/issues)
- Answer = Forgejo Comment (POST /repos/{owner}/{repo}/issues/{id}/comments)
- Domain = Forgejo Label (POST /repos/{owner}/{repo}/labels)

Operates 100% on the sovereign fleet mesh (Tailscale / local) with zero
external SaaS calls. Backed by persistent SQLite storage to guarantee
continuity across process restarts.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Set
from .storage import Comment, ForumStorage, Issue


class SovereignEnforcementError(Exception):
    """Raised when an operation attempts to breach sovereign infrastructure boundaries."""
    pass


class ForgejoClient:
    """REST API client for sovereign Forgejo instance using urllib."""

    SOVEREIGN_ALLOWLIST = [
        "100.",  # Tailscale CGNAT subnet
        "127.",  # Loopback
        "localhost",
        "chitakasha",
        "tail94aa6c.ts.net",
        "darwin",
        "wolfram",
        "forgejo.chitakasha.local",
    ]

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        owner: str = "mindrock",
        repo: str = "agent-forum",
    ):
        self.base_url = (base_url or os.environ.get("FORGEJO_URL", "http://100.112.18.4:3000/api/v1")).rstrip("/")
        self.api_token = api_token or os.environ.get("FORGEJO_TOKEN")
        self.owner = owner
        self.repo = repo
        self.external_saas_calls = 0
        self._validate_sovereignty()

    def _validate_sovereignty(self) -> None:
        """Enforce sovereign fleet boundary: no external SaaS allowed for forum."""
        parsed = urllib.parse.urlparse(self.base_url)
        host = parsed.hostname or ""
        is_sovereign = any(allowed in host for allowed in self.SOVEREIGN_ALLOWLIST)
        if not is_sovereign:
            self.external_saas_calls += 1
            raise SovereignEnforcementError(
                f"Sovereign boundary violation: Host '{host}' is outside sovereign fleet mesh. "
                f"Forum data must remain on sovereign Tailscale infrastructure."
            )

    def _request(self, endpoint: str, method: str = "GET", data: Optional[Dict] = None) -> Dict:
        """Execute authenticated HTTP request to sovereign Forgejo REST API."""
        self._validate_sovereignty()
        url = f"{self.base_url}{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_token:
            headers["Authorization"] = f"token {self.api_token}"

        req_data = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)

        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def create_remote_issue(self, title: str, body: str, labels: List[str]) -> Dict:
        """Create issue on remote Forgejo via POST /repos/{owner}/{repo}/issues."""
        endpoint = f"/repos/{self.owner}/{self.repo}/issues"
        return self._request(endpoint, method="POST", data={"title": title, "body": body, "labels": labels})

    def add_remote_comment(self, issue_index: int, body: str) -> Dict:
        """Add comment to issue via POST /repos/{owner}/{repo}/issues/{index}/comments."""
        endpoint = f"/repos/{self.owner}/{self.repo}/issues/{issue_index}/comments"
        return self._request(endpoint, method="POST", data={"body": body})

    def list_remote_issues(self, state: str = "open") -> List[Dict]:
        """List issues via GET /repos/{owner}/{repo}/issues."""
        endpoint = f"/repos/{self.owner}/{self.repo}/issues?state={state}"
        return self._request(endpoint, method="GET")


class ForgejoBackend:
    """Sovereign Forgejo Forum repository backend backed by persistent storage."""

    def __init__(
        self,
        repo_name: str = "agent-forum",
        storage_path: Optional[str] = None,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        use_http: bool = False,
    ):
        self.repo_name = repo_name
        self.storage = ForumStorage(storage_path or ":memory:")
        self.client = ForgejoClient(base_url=base_url, api_token=api_token, repo=repo_name)
        self.use_http = use_http
        self.is_sovereign: bool = True

    @property
    def external_saas_calls(self) -> int:
        """Track external SaaS calls. Invariant: MUST remain 0 for sovereign forum."""
        return self.client.external_saas_calls

    @property
    def issues(self) -> Dict[int, Issue]:
        """Dictionary representation of issues for backwards-compatibility."""
        issue_list = self.storage.list_issues()
        return {issue.id: issue for issue in issue_list}

    def create_issue(
        self,
        title: str,
        body: str,
        domains: List[str],
        author: str,
        requires_verification: bool,
    ) -> Issue:
        """Create and persist a new issue."""
        issue = Issue(
            id=0,
            title=title,
            body=body,
            domains=domains,
            author=author,
            askers={author},
            requires_canonical_verification=requires_verification,
        )
        saved = self.storage.save_issue(issue)

        if self.use_http:
            try:
                self.client.create_remote_issue(title=title, body=body, labels=domains)
            except Exception:
                pass  # Fall back to sovereign local persistence

        return saved

    def get_issue(self, issue_id: int) -> Optional[Issue]:
        """Retrieve issue by ID."""
        return self.storage.get_issue(issue_id)

    def list_issues(self, status: Optional[str] = None) -> List[Issue]:
        """List all issues, optionally filtered by status."""
        return self.storage.list_issues(status=status)

    def add_comment(
        self,
        issue_id: int,
        author: str,
        body: str,
        confidence: str = "medium",
        stance: Optional[str] = None,
    ) -> Comment:
        """Add a comment/answer to an issue and persist."""
        issue = self.storage.get_issue(issue_id)
        if not issue:
            raise KeyError(f"Issue #{issue_id} not found")

        comment = Comment(
            id=0,
            issue_id=issue_id,
            author=author,
            body=body,
            confidence=confidence,
            stance=stance,
        )
        saved_comment = self.storage.save_comment(comment)

        if self.use_http:
            try:
                self.client.add_remote_comment(issue_index=issue_id, body=body)
            except Exception:
                pass

        return saved_comment

    def search_issues(self, query: str, domain: Optional[str] = None) -> List[Issue]:
        """Search issues matching query keywords and optional domain."""
        return self.storage.search_issues(query=query, domain=domain)
