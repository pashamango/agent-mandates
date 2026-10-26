"""Sovereign Forgejo Backend and Client for Agent Forum v0.1.

Implements sovereign Git/Issue mapping:
- Question = Forgejo Issue (POST /repos/{owner}/{repo}/issues)
- Answer = Forgejo Comment (POST /repos/{owner}/{repo}/issues/{id}/comments)
- Domain = Forgejo Label (POST /repos/{owner}/{repo}/labels)

Operates 100% on the sovereign fleet mesh (Tailscale / local) with zero
external SaaS calls. Backed by persistent SQLite storage to guarantee
continuity across process restarts. Fail-closed production mode enforces
real HTTP delivery and synchronization between remote and local IDs.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Set
from .security import (
    SovereignEnforcementError,
    is_sovereign_host,
    validate_sovereign_url,
)
from .storage import Comment, ForumStorage, Issue


class ForgejoDeliveryError(Exception):
    """Raised when communication with Forgejo REST API fails in production mode."""
    pass


class ForgejoClient:
    """REST API client for sovereign Forgejo instance using urllib."""

    SOVEREIGN_ALLOWLIST = [
        "100.64.0.0/10",  # Tailscale CGNAT subnet
        "127.0.0.0/8",    # Loopback
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
        opener: Optional[urllib.request.OpenerDirector] = None,
    ):
        self.base_url = (base_url or os.environ.get("FORGEJO_URL", "http://100.112.18.4:3000/api/v1")).rstrip("/")
        self.api_token = api_token or os.environ.get("FORGEJO_TOKEN")
        self.owner = owner
        self.repo = repo
        self.opener = opener
        self.external_saas_calls = 0
        self._validate_sovereignty()

    def _validate_sovereignty(self) -> None:
        """Enforce sovereign fleet boundary: no external SaaS allowed for forum."""
        try:
            validate_sovereign_url(self.base_url)
        except SovereignEnforcementError:
            self.external_saas_calls += 1
            raise

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

        opener = self.opener or urllib.request
        with opener.open(req, timeout=5.0) as resp:
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
    """Sovereign Forgejo Forum repository backend backed by persistent storage.

    Default mode: fail-closed production (use_http=True).
    To run in offline test mode without network dependencies, pass offline=True
    or use OfflineForgejoBackend.
    """

    def __init__(
        self,
        repo_name: str = "agent-forum",
        storage_path: Optional[str] = None,
        base_url: Optional[str] = None,
        api_token: Optional[str] = None,
        use_http: Optional[bool] = None,
        offline: bool = False,
        opener: Optional[urllib.request.OpenerDirector] = None,
    ):
        self.repo_name = repo_name
        self.storage = ForumStorage(storage_path or ":memory:")
        self.client = ForgejoClient(base_url=base_url, api_token=api_token, repo=repo_name, opener=opener)
        if offline or os.environ.get("FORGEJO_OFFLINE") == "1":
            self.use_http = False
        elif use_http is not None:
            self.use_http = use_http
        else:
            self.use_http = True  # Production fail-closed mode
        self.offline = not self.use_http
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
        """Create and persist a new issue.

        In production (use_http=True), remote creation is executed and remote number
        is synchronized with local state. Errors fail closed immediately.
        """
        remote_id = None
        if self.use_http:
            try:
                remote_resp = self.client.create_remote_issue(title=title, body=body, labels=domains)
                remote_id = remote_resp.get("number") or remote_resp.get("id")
                if remote_id is not None:
                    remote_id = int(remote_id)
            except Exception as e:
                raise ForgejoDeliveryError(f"Forgejo remote issue creation failed: {e}") from e

        issue = Issue(
            id=remote_id if remote_id is not None else 0,
            remote_id=remote_id,
            title=title,
            body=body,
            domains=domains,
            author=author,
            askers={author},
            requires_canonical_verification=requires_verification,
        )
        saved = self.storage.save_issue(issue)
        return saved

    def get_issue(self, issue_id: int) -> Optional[Issue]:
        """Retrieve issue by ID or remote number."""
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
        """Add a comment/answer to an issue and persist.

        In production (use_http=True), comment is sent to the synchronized remote
        Forgejo issue number. Errors fail closed immediately.
        """
        issue = self.storage.get_issue(issue_id)
        if not issue:
            raise KeyError(f"Issue #{issue_id} not found")

        remote_index = issue.remote_id if issue.remote_id is not None else issue.id
        if self.use_http:
            try:
                self.client.add_remote_comment(issue_index=remote_index, body=body)
            except Exception as e:
                raise ForgejoDeliveryError(
                    f"Forgejo remote comment delivery failed for issue #{remote_index}: {e}"
                ) from e

        comment = Comment(
            id=0,
            issue_id=issue.id,
            author=author,
            body=body,
            confidence=confidence,
            stance=stance,
        )
        saved_comment = self.storage.save_comment(comment)
        return saved_comment

    def search_issues(self, query: str, domain: Optional[str] = None) -> List[Issue]:
        """Search issues matching query keywords and optional domain."""
        return self.storage.search_issues(query=query, domain=domain)


class OfflineForgejoBackend(ForgejoBackend):
    """Explicit offline test backend backed exclusively by SQLite storage with HTTP disabled."""

    def __init__(self, repo_name: str = "agent-forum", storage_path: Optional[str] = None):
        super().__init__(repo_name=repo_name, storage_path=storage_path, use_http=False, offline=True)
