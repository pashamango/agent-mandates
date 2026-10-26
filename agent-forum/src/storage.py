"""Persistent Storage Engine for Agent Forum v0.1.

Backed by SQLite (standard library) with JSON serialization to ensure
complete persistence across process restarts, zero external dependencies,
and 100% sovereign local operation.
"""

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class Comment:
    """Forgejo Issue Comment representing an Answer or advisory response."""
    id: int
    issue_id: int
    author: str
    body: str
    confidence: str = "medium"  # 'high', 'medium', 'low'
    stance: Optional[str] = None  # e.g., 'approve', 'reject', 'prebuilt', 'compile'
    created_at: float = field(default_factory=time.time)


@dataclass
class Issue:
    """Forgejo Issue representing a Question."""
    id: int
    title: str
    body: str
    domains: List[str]
    author: str
    remote_id: Optional[int] = None
    duplicate_count: int = 1
    askers: Set[str] = field(default_factory=set)
    status: str = "open"  # 'open', 'closed'
    comments: List[Comment] = field(default_factory=list)
    requires_canonical_verification: bool = False
    canonization_triggered_duplicate: bool = False
    canonization_triggered_contradiction: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class CanonizationTask:
    """Linear task generated for PM to review standardization/canonization."""
    id: str
    issue_id: int
    reason: str
    title: str
    body: str
    canon_changed: bool = False  # Hard invariant: NEVER changes canon automatically
    linear_id: Optional[str] = None
    assignee: str = "Pavel Cherkashin"
    status: str = "queued_for_pm_tap"
    created_at: float = field(default_factory=time.time)


class ForumStorage:
    """SQLite-backed persistent storage for Agent Forum."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        if db_path != ":memory:":
            try:
                os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            except Exception:
                pass
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema for issues, comments, and canonization tasks."""
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    remote_id INTEGER,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    domains_json TEXT NOT NULL,
                    author TEXT NOT NULL,
                    duplicate_count INTEGER DEFAULT 1,
                    askers_json TEXT NOT NULL,
                    status TEXT DEFAULT 'open',
                    requires_canonical_verification INTEGER DEFAULT 0,
                    canonization_triggered_duplicate INTEGER DEFAULT 0,
                    canonization_triggered_contradiction INTEGER DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # Schema migration check for remote_id
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA table_info(issues)")
            cols = [r[1] for r in cursor.fetchall()]
            if "remote_id" not in cols:
                self.conn.execute("ALTER TABLE issues ADD COLUMN remote_id INTEGER")

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    body TEXT NOT NULL,
                    confidence TEXT DEFAULT 'medium',
                    stance TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(issue_id) REFERENCES issues(id)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS canonization_tasks (
                    id TEXT PRIMARY KEY,
                    issue_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    canon_changed INTEGER DEFAULT 0,
                    linear_id TEXT,
                    assignee TEXT DEFAULT 'Pavel Cherkashin',
                    status TEXT DEFAULT 'queued_for_pm_tap',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(issue_id) REFERENCES issues(id)
                )
            """)

    def save_issue(self, issue: Issue) -> Issue:
        """Insert or update an issue in SQLite."""
        with self.conn:
            if issue.id and self.get_issue(issue.id):
                self.conn.execute(
                    """
                    UPDATE issues SET
                        remote_id = ?, title = ?, body = ?, domains_json = ?, author = ?,
                        duplicate_count = ?, askers_json = ?, status = ?,
                        requires_canonical_verification = ?,
                        canonization_triggered_duplicate = ?,
                        canonization_triggered_contradiction = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        issue.remote_id,
                        issue.title,
                        issue.body,
                        json.dumps(issue.domains),
                        issue.author,
                        issue.duplicate_count,
                        json.dumps(list(issue.askers)),
                        issue.status,
                        1 if issue.requires_canonical_verification else 0,
                        1 if issue.canonization_triggered_duplicate else 0,
                        1 if issue.canonization_triggered_contradiction else 0,
                        issue.updated_at,
                        issue.id,
                    ),
                )
            elif issue.remote_id is not None:
                self.conn.execute(
                    """
                    INSERT INTO issues (
                        id, remote_id, title, body, domains_json, author, duplicate_count,
                        askers_json, status, requires_canonical_verification,
                        canonization_triggered_duplicate,
                        canonization_triggered_contradiction,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue.remote_id,
                        issue.remote_id,
                        issue.title,
                        issue.body,
                        json.dumps(issue.domains),
                        issue.author,
                        issue.duplicate_count,
                        json.dumps(list(issue.askers)),
                        issue.status,
                        1 if issue.requires_canonical_verification else 0,
                        1 if issue.canonization_triggered_duplicate else 0,
                        1 if issue.canonization_triggered_contradiction else 0,
                        issue.created_at,
                        issue.updated_at,
                    ),
                )
                issue.id = issue.remote_id
            else:
                cursor = self.conn.execute(
                    """
                    INSERT INTO issues (
                        remote_id, title, body, domains_json, author, duplicate_count,
                        askers_json, status, requires_canonical_verification,
                        canonization_triggered_duplicate,
                        canonization_triggered_contradiction,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue.remote_id,
                        issue.title,
                        issue.body,
                        json.dumps(issue.domains),
                        issue.author,
                        issue.duplicate_count,
                        json.dumps(list(issue.askers)),
                        issue.status,
                        1 if issue.requires_canonical_verification else 0,
                        1 if issue.canonization_triggered_duplicate else 0,
                        1 if issue.canonization_triggered_contradiction else 0,
                        issue.created_at,
                        issue.updated_at,
                    ),
                )
                issue.id = cursor.lastrowid
        return issue

    def get_issue(self, issue_id: int) -> Optional[Issue]:
        """Retrieve an issue and its comments by local ID or remote ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM issues WHERE id = ? OR remote_id = ? ORDER BY id ASC", (issue_id, issue_id))
        row = cursor.fetchone()
        if not row:
            return None
        return self._row_to_issue(row)

    def list_issues(self, status: Optional[str] = None) -> List[Issue]:
        """List all issues, optionally filtered by status."""
        cursor = self.conn.cursor()
        if status:
            cursor.execute("SELECT * FROM issues WHERE status = ? ORDER BY id ASC", (status,))
        else:
            cursor.execute("SELECT * FROM issues ORDER BY id ASC")
        return [self._row_to_issue(r) for r in cursor.fetchall()]

    def search_issues(self, query: str, domain: Optional[str] = None) -> List[Issue]:
        """Search issues matching keywords and optional domain."""
        all_issues = self.list_issues()
        results = []
        tokens = query.lower().split()
        for issue in all_issues:
            if domain and domain not in issue.domains:
                continue
            text = f"{issue.title} {issue.body}".lower()
            if any(t in text for t in tokens if len(t) > 2):
                results.append(issue)
        return results

    def save_comment(self, comment: Comment) -> Comment:
        """Insert a comment into SQLite."""
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO comments (issue_id, author, body, confidence, stance, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    comment.issue_id,
                    comment.author,
                    comment.body,
                    comment.confidence,
                    comment.stance,
                    comment.created_at,
                ),
            )
            comment.id = cursor.lastrowid
            self.conn.execute(
                "UPDATE issues SET updated_at = ? WHERE id = ?",
                (time.time(), comment.issue_id),
            )
        return comment

    def get_comments_for_issue(self, issue_id: int) -> List[Comment]:
        """Retrieve all comments for a specific issue."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM comments WHERE issue_id = ? ORDER BY id ASC", (issue_id,))
        comments = []
        for r in cursor.fetchall():
            comments.append(
                Comment(
                    id=r["id"],
                    issue_id=r["issue_id"],
                    author=r["author"],
                    body=r["body"],
                    confidence=r["confidence"],
                    stance=r["stance"],
                    created_at=r["created_at"],
                )
            )
        return comments

    def save_canonization_task(self, task: CanonizationTask) -> CanonizationTask:
        """Save a canonization task."""
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO canonization_tasks (
                    id, issue_id, reason, title, body, canon_changed, linear_id, assignee, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.issue_id,
                    task.reason,
                    task.title,
                    task.body,
                    1 if task.canon_changed else 0,
                    task.linear_id,
                    task.assignee,
                    task.status,
                    task.created_at,
                ),
            )
        return task

    def list_canonization_tasks(self) -> List[CanonizationTask]:
        """List all canonization tasks."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM canonization_tasks ORDER BY created_at ASC")
        tasks = []
        for r in cursor.fetchall():
            tasks.append(
                CanonizationTask(
                    id=r["id"],
                    issue_id=r["issue_id"],
                    reason=r["reason"],
                    title=r["title"],
                    body=r["body"],
                    canon_changed=bool(r["canon_changed"]),
                    linear_id=r["linear_id"],
                    assignee=r["assignee"],
                    status=r["status"],
                    created_at=r["created_at"],
                )
            )
        return tasks

    def _row_to_issue(self, row: sqlite3.Row) -> Issue:
        """Convert an SQLite row into an Issue dataclass."""
        issue_id = row["id"]
        comments = self.get_comments_for_issue(issue_id)
        remote_id = None
        if "remote_id" in row.keys() and row["remote_id"] is not None:
            remote_id = int(row["remote_id"])
        return Issue(
            id=issue_id,
            remote_id=remote_id,
            title=row["title"],
            body=row["body"],
            domains=json.loads(row["domains_json"]),
            author=row["author"],
            duplicate_count=row["duplicate_count"],
            askers=set(json.loads(row["askers_json"])),
            status=row["status"],
            comments=comments,
            requires_canonical_verification=bool(row["requires_canonical_verification"]),
            canonization_triggered_duplicate=bool(row["canonization_triggered_duplicate"]),
            canonization_triggered_contradiction=bool(row["canonization_triggered_contradiction"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def close(self) -> None:
        """Close SQLite database connection."""
        self.conn.close()
