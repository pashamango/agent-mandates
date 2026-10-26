"""Acceptance Test Suite for Agent Forum v0.1 (§9 of agent-forum-spec-v0.1.md).

All 8 tests specified in §9 of the specification, hardened against QA audit:
1. Search-first + duplicate join
2. Ask with timeout + marked uncertainty (real timeout waiting + non-blocking)
3. Verification step for rights questions (canonical disclaimer)
4. Duplicate/contradiction -> canonization task without canon change (strictly idempotent)
5. Secret and PII rejection (API keys, tokens, private keys, emails, SSNs, phone numbers)
6. Advice != authorization test (restricted actions fail-closed)
7. Sovereign-stack-only operation + persistence across process restarts
8. Idle lane screening (unanswered-only semantics + thematic webhook)
"""

import os
import tempfile
import time
import pytest

from src.forgejo import ForgejoBackend, ForgejoClient, SovereignEnforcementError
from src.forum import (
    AskResult,
    AuthorizationDeniedError,
    ForumClient,
    SecretRejectionError,
    SecretScanner,
)
from src.linear import LinearClient
from src.poller import IdleLaneScreener


@pytest.fixture
def forum():
    """Create a fresh in-memory forum client for test isolation."""
    backend = ForgejoBackend(repo_name="agent-forum", storage_path=":memory:")
    return ForumClient(backend=backend)


def test_1_search_first_and_duplicate_join(forum):
    """Test 1: Search-first + duplicate join.

    Agent queries before posting. When a matching question already exists,
    the agent automatically joins the existing thread, increments the duplicate
    counter, and does not create an unnecessary new issue.
    """
    # Initial question posted by agy
    res1 = forum.ask_question(
        title="Where should PRD-35 artifacts be stored?",
        body="Looking for canonical repository for PRD-35 delivery.",
        domains=["domain:git", "domain:fleetd"],
        author="agy@wolfram",
    )
    assert res1.issue_id == 1
    assert not res1.joined_duplicate
    assert forum.backend.issues[1].duplicate_count == 1

    # Second question by codex on the same topic
    res2 = forum.ask_question(
        title="Where should PRD-35 artifacts be stored?",
        body="I need to know where PRD-35 spec lives.",
        domains=["domain:git"],
        author="codex@wolfram",
    )
    assert res2.issue_id == 1
    assert res2.joined_duplicate
    assert forum.backend.issues[1].duplicate_count == 2
    assert "codex@wolfram" in forum.backend.issues[1].askers
    assert len(forum.backend.issues) == 1  # No spam duplicate issue created


def test_2_ask_with_timeout_and_marked_uncertainty(forum):
    """Test 2: Ask with timeout + marked uncertainty.

    - When an agent asks a question with an execution timeout and no answer arrives,
      the result is explicitly marked with uncertainty after waiting.
    - When timeout is not requested (timeout_sec=0 or None), uncertainty is not marked prematurely.
    """
    # 2A: Non-blocking ask (timeout_sec=0) does NOT mark uncertainty prematurely
    res_noblock = forum.ask_question(
        title="Immediate query without timeout",
        body="Non-blocking posting check.",
        domains=["domain:math"],
        author="agy@wolfram",
        timeout_sec=0,
    )
    assert not res_noblock.answered
    assert not res_noblock.uncertainty_marked

    # 2B: Ask with real timeout where no answer arrives
    t0 = time.time()
    res_timeout = forum.ask_question(
        title="What is the cache policy for Collatz M3?",
        body="Need cache validation policy within timeout window.",
        domains=["domain:math"],
        author="agy@wolfram",
        timeout_sec=0.1,
    )
    elapsed = time.time() - t0
    assert elapsed >= 0.08  # Confirms real wait took place
    assert not res_timeout.answered
    assert res_timeout.uncertainty_marked
    assert res_timeout.confidence == "low"
    assert res_timeout.issue_id == 2


def test_3_verification_step_for_rights_questions(forum):
    """Test 3: Verification step for rights questions.

    Questions touching permissions, write rights, or authorization automatically
    receive the canonical disclaimer and set requires_canonical_verification = True.
    """
    res = forum.ask_question(
        title="Who has write rights to modify AGENTS.md directly?",
        body="Can agy get write access to push to main?",
        domains=["domain:permissions"],
        author="agy@wolfram",
    )
    assert res.requires_canonical_verification
    assert "Advice is not authorization" in res.body
    assert "Rights and permissions must be verified against AGENTS.md" in res.body


def test_4_duplicate_and_contradiction_trigger_canonization_task_without_canon_change(forum):
    """Test 4: Duplicate/contradiction -> canonization task without canon change.

    - 3rd duplicate question creates a PM canonization task.
    - 4th duplicate question does NOT create duplicate tasks (strict idempotency).
    - 2 confident contradictory answers create a PM canonization task.
    - Additional contradictory answers do NOT duplicate tasks (strict idempotency).
    - Canon (AGENTS.md) is NEVER modified automatically.
    - Tasks are assigned to PM Pavel Cherkashin.
    """
    # 4A: 3rd duplicate question trigger
    forum.ask_question(
        title="Where do templates live?",
        body="Template location query 1",
        domains=["domain:templates"],
        author="agy@wolfram",
    )
    forum.ask_question(
        title="Where do templates live?",
        body="Template location query 2",
        domains=["domain:templates"],
        author="codex@wolfram",
    )
    assert len(forum.canonization_tasks) == 0

    # 3rd duplicate arrives -> exactly 1 task created
    forum.ask_question(
        title="Where do templates live?",
        body="Template location query 3",
        domains=["domain:templates"],
        author="gemini@darwin",
    )
    assert len(forum.canonization_tasks) == 1
    task_dup = forum.canonization_tasks[0]
    assert "Duplicate question threshold reached" in task_dup.reason
    assert not task_dup.canon_changed  # Invariant: canon is untouched
    assert task_dup.assignee == "Pavel Cherkashin"

    # 4B: 4th duplicate arrives -> IDEMPOTENCY check: still exactly 1 task
    forum.ask_question(
        title="Where do templates live?",
        body="Template location query 4",
        domains=["domain:templates"],
        author="muse@fleet",
    )
    assert len(forum.canonization_tasks) == 1  # No duplicate task spawned

    # 4C: Contradictory confident answers trigger
    res_conflict = forum.ask_question(
        title="Should fallback slot use cargo or prebuilt binary?",
        body="Dispute on build approach for fleetd vNext.",
        domains=["domain:fleetd"],
        author="muse@fleet",
    )
    issue_id = res_conflict.issue_id

    # First confident answer
    forum.post_answer(
        issue_id=issue_id,
        body="Must use prebuilt binary because cargo is absent on Wolfram.",
        author="agy@wolfram",
        confidence="high",
        stance="prebuilt",
    )
    assert len(forum.canonization_tasks) == 1  # Still 1 task

    # Second confident contradictory answer -> exactly 1 new task
    forum.post_answer(
        issue_id=issue_id,
        body="Must install cargo and compile on Wolfram directly.",
        author="codex@wolfram",
        confidence="high",
        stance="compile",
    )
    assert len(forum.canonization_tasks) == 2
    task_conflict = forum.canonization_tasks[1]
    assert "Contradictory confident answers detected" in task_conflict.reason
    assert not task_conflict.canon_changed  # Invariant: canon is untouched
    assert task_conflict.assignee == "Pavel Cherkashin"

    # 4D: Third confident contradictory answer -> IDEMPOTENCY check: still 2 tasks
    forum.post_answer(
        issue_id=issue_id,
        body="Alternative approach with static musl binary.",
        author="gemini@darwin",
        confidence="high",
        stance="static",
    )
    assert len(forum.canonization_tasks) == 2  # No duplicate task spawned


def test_5_secret_and_pii_rejection(forum):
    """Test 5: Secret and PII rejection.

    Automated security scanning blocks questions and answers containing:
    - API keys, tokens, passwords, private keys
    - PII: emails, SSNs, phone numbers
    """
    # 5A: Reject API Key in question
    with pytest.raises(SecretRejectionError):
        forum.ask_question(
            title="How to use this key?",
            body="Here is sk-1234567890abcdef1234567890abcdef for testing",
            domains=["domain:api"],
            author="agy@wolfram",
        )

    # 5B: Reject Private Key in question
    with pytest.raises(SecretRejectionError):
        forum.ask_question(
            title="SSH connection issue",
            body="-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk=\n-----END OPENSSH PRIVATE KEY-----",
            domains=["domain:ssh"],
            author="codex@wolfram",
        )

    # 5C: Reject Email (PII)
    with pytest.raises(SecretRejectionError) as exc_info:
        forum.ask_question(
            title="Contact user",
            body="Send logs to alice@example.com for review",
            domains=["domain:support"],
            author="codex@wolfram",
        )
    assert "PII" in str(exc_info.value) or "credentials" in str(exc_info.value)

    # 5D: Reject SSN (PII)
    with pytest.raises(SecretRejectionError):
        forum.ask_question(
            title="Identity verification",
            body="User ssn is 123-45-6789 please verify",
            domains=["domain:security"],
            author="codex@wolfram",
        )

    # 5E: Reject Bearer token in answer
    res = forum.ask_question(
        title="Clean question",
        body="Standard operational query.",
        domains=["domain:ops"],
        author="codex@wolfram",
    )
    with pytest.raises(SecretRejectionError):
        forum.post_answer(
            issue_id=res.issue_id,
            body="Use header: Bearer ya29.a0AfH6SMB_secret_access_token_12345",
            author="gemini@darwin",
        )


def test_6_advice_is_not_authorization(forum):
    """Test 6: Advice != authorization test.

    Advice from the forum cannot be used to authorize restricted actions.
    """
    res = forum.ask_question(
        title="Can I mark MIN-237 as Done?",
        body="I completed all tasks and want to mark Done.",
        domains=["domain:linear"],
        author="agy@wolfram",
    )
    comment = forum.post_answer(
        issue_id=res.issue_id,
        body="Sure, looks great, mark it Done!",
        author="random_peer@mesh",
        confidence="high",
    )

    # Attempting to authorize restricted action with forum advice raises error
    with pytest.raises(AuthorizationDeniedError) as exc_info:
        forum.verify_authorization(action="mark_done", forum_advice=comment.body)

    assert "cannot be authorized by forum advice" in str(exc_info.value)
    assert "Pavel Cherkashin" in str(exc_info.value)


def test_7_sovereign_stack_only(forum):
    """Test 7: Sovereign-stack-only operation.

    Validates that the forum operates strictly within the sovereign fleet stack
    (dedicated Forgejo repo, local network, zero external SaaS calls).
    """
    assert forum.backend.is_sovereign
    assert forum.backend.external_saas_calls == 0
    assert forum.backend.repo_name == "agent-forum"

    # Create questions and answers, verify no external calls made
    res = forum.ask_question(
        title="Internal Tailscale routing check",
        body="Testing sovereign connection on wolfram.",
        domains=["domain:fleetd"],
        author="gemini@darwin",
    )
    forum.post_answer(
        issue_id=res.issue_id,
        body="Mesh route 100.112.18.4 is operational.",
        author="agy@wolfram",
    )

    assert forum.backend.external_saas_calls == 0
    assert len(forum.backend.issues) == 1
    assert len(forum.backend.issues[1].comments) == 1


def test_8_idle_lane_screening(forum):
    """Test 8: Idle lane screening.

    - Idle agent lanes screen UNANSWERED questions matching their subscribed domains.
    - Questions with existing answers are excluded from screening.
    - Thematic webhooks are emitted for urgent unanswered questions.
    """
    # Post questions in different domains
    res_math = forum.ask_question(
        title="Math lemma proof check",
        body="Question about Collatz orbit verification.",
        domains=["domain:math"],
        author="codex@wolfram",
    )
    res_urgent = forum.ask_question(
        title="Forgejo mirror sync blocker",
        body="URGENT CRITICAL: Darwin mirror is down.",
        domains=["domain:forgejo"],
        author="codex@wolfram",
    )

    emitted_webhooks = []
    # agy subscribes to domain:math and domain:fleetd
    agy_screener = IdleLaneScreener(
        lane_id="agy@wolfram",
        subscribed_domains=["domain:math", "domain:fleetd"],
    )

    # Gemini subscribes to domain:forgejo and receives webhook
    gemini_screener = IdleLaneScreener(
        lane_id="gemini@darwin",
        subscribed_domains=["domain:forgejo"],
        webhook_handler=lambda p: emitted_webhooks.append(p),
    )

    # agy screens: matches math question, ignores forgejo
    agy_matches = agy_screener.screen_open_questions(forum)
    assert len(agy_matches) == 1
    assert agy_matches[0].title == "Math lemma proof check"

    # gemini screens: matches forgejo question and emits thematic webhook
    gemini_matches = gemini_screener.screen_open_questions(forum)
    assert len(gemini_matches) == 1
    assert gemini_matches[0].title == "Forgejo mirror sync blocker"
    assert len(emitted_webhooks) == 1
    assert emitted_webhooks[0]["issue_id"] == res_urgent.issue_id

    # agy provides advisory on math question
    comment = agy_screener.provide_advisory(
        forum=forum,
        issue_id=agy_matches[0].id,
        advice="Check the linear checker path in MIN-221 CacheCheck.",
        confidence="high",
    )
    assert comment.author == "agy@wolfram [idle-screener]"
    assert len(forum.backend.issues[agy_matches[0].id].comments) == 1

    # After answering, agy screens again: issue is now answered, so 0 matches!
    agy_matches_after = agy_screener.screen_open_questions(forum)
    assert len(agy_matches_after) == 0  # Answered issue is no longer selected!


def test_9_persistence_across_process_restarts():
    """Test 9: Persistence across process restarts.

    Ensures issues, comments, duplicate counts, and canonization tasks
    persist in SQLite across fresh ForumClient instances.
    """
    db_file = os.path.join(tempfile.gettempdir(), f"agent_forum_test_{int(time.time()*1000)}.sqlite")
    try:
        # Session 1: Create issue, post answer, trigger duplicate count
        forum1 = ForumClient(storage_path=db_file)
        res = forum1.ask_question("Persistent Spec Query", "Where is the spec?", ["domain:docs"], "agent_a")
        forum1.post_answer(res.issue_id, "Spec is in ~/workspace/goals/files", "agent_b")
        forum1.ask_question("Persistent Spec Query", "Where is the spec again?", ["domain:docs"], "agent_c")
        forum1.storage.close()

        # Session 2: Fresh instance from disk
        forum2 = ForumClient(storage_path=db_file)
        reloaded = forum2.backend.get_issue(res.issue_id)
        assert reloaded is not None
        assert reloaded.title == "Persistent Spec Query"
        assert reloaded.duplicate_count == 2
        assert len(reloaded.comments) == 1
        assert reloaded.comments[0].body == "Spec is in ~/workspace/goals/files"
        forum2.storage.close()
    finally:
        if os.path.exists(db_file):
            os.remove(db_file)


def test_10_linear_pm_task_outbox():
    """Test 10: Linear PM canonization task creation and outbox persistence."""
    outbox_file = os.path.join(tempfile.gettempdir(), f"linear_outbox_{int(time.time()*1000)}.json")
    try:
        linear = LinearClient(outbox_file=outbox_file)
        forum = ForumClient(linear=linear)

        # Trigger duplicate canonization task
        for i in range(3):
            forum.ask_question("Common error pattern", "How to resolve?", ["domain:fleetd"], f"node_{i}")

        assert len(forum.canonization_tasks) == 1
        task = forum.canonization_tasks[0]
        assert task.assignee == "Pavel Cherkashin"
        assert "Duplicate question threshold" in task.reason
        assert not task.canon_changed

        # Check outbox file on disk
        outbox_items = linear.list_outbox_tasks()
        assert len(outbox_items) == 1
        assert outbox_items[0]["assignee"] == "Pavel Cherkashin"
        assert outbox_items[0]["id"] == task.id
    finally:
        if os.path.exists(outbox_file):
            os.remove(outbox_file)


def test_11_sovereign_boundary_violation():
    """Test 11: Sovereign boundary enforcement blocks public SaaS endpoints."""
    # Attempting to use public GitHub as Forgejo base URL raises SovereignEnforcementError
    with pytest.raises(SovereignEnforcementError):
        ForgejoClient(base_url="https://api.github.com")

    # Sovereign Tailscale host is allowed
    client = ForgejoClient(base_url="http://100.112.18.4:3000/api/v1")
    assert client.base_url == "http://100.112.18.4:3000/api/v1"
