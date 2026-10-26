"""Linear Integration for Agent Forum v0.1.

Bridges Forum Layer to Canon Layer by formalizing standardization tasks
directly into Linear for Principal Pavel Cherkashin.
Zero auto-canon mutation: human tap strictly required.
"""

import json
import os
import tempfile
import urllib.error
import urllib.request
from typing import Dict, List, Optional
from .storage import CanonizationTask, ForumStorage


class LinearClient:
    """Client for creating Linear canonization tasks for the PM (Principal Pavel Cherkashin)."""

    DEFAULT_API_URL = "https://api.linear.app/graphql"
    DEFAULT_TEAM = "MIN"
    PM_ASSIGNEE = "Pavel Cherkashin"
    PM_EMAIL = "pavel@mindrock.capital"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        outbox_file: Optional[str] = None,
        storage: Optional[ForumStorage] = None,
    ):
        self.api_key = api_key or os.environ.get("LINEAR_API_KEY")
        self.api_url = api_url or os.environ.get("LINEAR_API_URL", self.DEFAULT_API_URL)
        if outbox_file:
            self.outbox_file = outbox_file
        else:
            default_path = os.path.join(tempfile.gettempdir(), "agent_forum_linear_outbox.json")
            self.outbox_file = default_path
        self.storage = storage
        self.dispatched_tasks: List[CanonizationTask] = []
        self._ensure_outbox()

    def _ensure_outbox(self) -> None:
        """Ensure outbox directory and file exist for persistent offline queue."""
        try:
            outbox_dir = os.path.dirname(os.path.abspath(self.outbox_file))
            os.makedirs(outbox_dir, exist_ok=True)
            if not os.path.exists(self.outbox_file):
                with open(self.outbox_file, "w", encoding="utf-8") as f:
                    json.dump([], f)
        except Exception:
            # Fall back to /tmp if default path has permission restrictions
            self.outbox_file = os.path.join(tempfile.gettempdir(), "agent_forum_linear_outbox.json")
            if not os.path.exists(self.outbox_file):
                try:
                    with open(self.outbox_file, "w", encoding="utf-8") as f:
                        json.dump([], f)
                except Exception:
                    pass

    def create_pm_canonization_task(self, task: CanonizationTask) -> str:
        """Create a canonization task for the PM in Linear or enqueue in persistent outbox."""
        task.assignee = self.PM_ASSIGNEE
        task.status = "queued_for_pm_tap"

        linear_id = f"LIN-{task.id}"
        task.linear_id = linear_id

        # 1. Attempt live Linear GraphQL API dispatch if API key is present
        if self.api_key and not self.api_key.startswith("mock-"):
            try:
                mutation = {
                    "query": """
                        mutation CreateCanonizationIssue($title: String!, $description: String!) {
                            issueCreate(input: {
                                title: $title,
                                description: $description,
                                teamId: "MIN"
                            }) {
                                success
                                issue { id identifier }
                            }
                        }
                    """,
                    "variables": {
                        "title": task.title,
                        "description": task.body,
                    },
                }
                req_data = json.dumps(mutation).encode("utf-8")
                req = urllib.request.Request(
                    self.api_url,
                    data=req_data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": self.api_key,
                    },
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    if "data" in resp_json and resp_json["data"].get("issueCreate", {}).get("success"):
                        linear_id = resp_json["data"]["issueCreate"]["issue"]["identifier"]
                        task.linear_id = linear_id
                        task.status = "dispatched_to_linear"
            except Exception:
                task.status = "outbox_pending_dispatch"

        # 2. Persist to storage and outbox file
        if self.storage:
            self.storage.save_canonization_task(task)

        self._record_to_outbox_file(task)
        self.dispatched_tasks.append(task)
        return linear_id

    def _record_to_outbox_file(self, task: CanonizationTask) -> None:
        """Record task into persistent JSON outbox file."""
        try:
            records = []
            if os.path.exists(self.outbox_file):
                with open(self.outbox_file, "r", encoding="utf-8") as f:
                    try:
                        records = json.load(f)
                    except json.JSONDecodeError:
                        records = []

            existing_idx = next((i for i, r in enumerate(records) if r.get("id") == task.id), None)
            task_dict = {
                "id": task.id,
                "linear_id": task.linear_id,
                "issue_id": task.issue_id,
                "reason": task.reason,
                "title": task.title,
                "body": task.body,
                "canon_changed": task.canon_changed,
                "assignee": task.assignee,
                "status": task.status,
                "created_at": task.created_at,
            }
            if existing_idx is not None:
                records[existing_idx] = task_dict
            else:
                records.append(task_dict)

            with open(self.outbox_file, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
        except Exception:
            pass

    def list_outbox_tasks(self) -> List[Dict]:
        """Read all tasks from persistent outbox file."""
        if not os.path.exists(self.outbox_file):
            return []
        try:
            with open(self.outbox_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
