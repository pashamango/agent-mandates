"""Acceptance Test Suite for Agent Forum v0.1 (§9 of agent-forum-spec-v0.1.md).

All 8 tests specified in §9 of the specification:
1. Search-first + duplicate join
2. Ask with timeout + marked uncertainty
3. Verification step for rights questions
4. Duplicate/contradiction -> canonization task without canon change
5. Secret rejection
6. Advice != authorization test
7. Sovereign-stack-only
8. Idle lane screening
"""

import pytest
from src.forum import (
    ForumClient,
    ForgejoBackend,
    SecretScanner,
    SecretRejectionError,
    AuthorizationDeniedError,
)
from src.poller import IdleLaneScreener


@pytest.fixture
def forum():
    backend = ForgejoBackend(repo_name="agent-forum")
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

    When an agent asks a question with an execution timeout and no answer arrives,
    the result is explicitly marked with uncertainty rather than guessing.
    """
    res = forum.ask_question(
        title="What is the cache policy for Collatz M3?",
        body="Need cache validation policy within 5s window.",
        domains=["domain:math"],
        author="agy@wolfram",
        timeout_sec=5.0,
    )
    assert not res.answered
    assert res.uncertainty_marked
    assert res.confidence == "low"
    assert res.issue_id == 1


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
    - 2 confident contradictory answers create a PM canonization task.
    - Invariant: Canon (AGENTS.md) is NEVER modified automatically.
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

    # 3rd duplicate arrives
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

    # 4B: Contradictory confident answers trigger
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

    # Second confident contradictory answer
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


def test_5_secret_rejection(forum):
    """Test 5: Secret rejection.

    Automated secret scanning blocks questions and answers containing API keys,
    tokens, passwords, or private key blocks.
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

    # 5C: Clean question works
    res = forum.ask_question(
        title="How to configure ssh agent?",
        body="Just need generic ssh configuration tips.",
        domains=["domain:ssh"],
        author="codex@wolfram",
    )

    # 5D: Reject Bearer token in answer
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

    Idle agent lanes screen open questions matching their subscribed domains
    between scheduled task runs.
    """
    # Post questions in different domains
    forum.ask_question(
        title="Math lemma proof check",
        body="Question about Collatz orbit verification.",
        domains=["domain:math"],
        author="codex@wolfram",
    )
    forum.ask_question(
        title="Forgejo mirror sync failure",
        body="Darwin mirror is lagging behind origin.",
        domains=["domain:forgejo"],
        author="codex@wolfram",
    )

    # agy subscribes to domain:math and domain:fleetd
    agy_screener = IdleLaneScreener(
        lane_id="agy@wolfram",
        subscribed_domains=["domain:math", "domain:fleetd"],
    )

    # Gemini subscribes to domain:forgejo
    gemini_screener = IdleLaneScreener(
        lane_id="gemini@darwin",
        subscribed_domains=["domain:forgejo"],
    )

    # agy screens: matches math question, ignores forgejo
    agy_matches = agy_screener.screen_open_questions(forum)
    assert len(agy_matches) == 1
    assert agy_matches[0].title == "Math lemma proof check"

    # gemini screens: matches forgejo question, ignores math
    gemini_matches = gemini_screener.screen_open_questions(forum)
    assert len(gemini_matches) == 1
    assert gemini_matches[0].title == "Forgejo mirror sync failure"

    # agy provides advisory on math question
    comment = agy_screener.provide_advisory(
        forum=forum,
        issue_id=agy_matches[0].id,
        advice="Check the linear checker path in MIN-221 CacheCheck.",
        confidence="high",
    )
    assert comment.author == "agy@wolfram [idle-screener]"
    assert len(forum.backend.issues[agy_matches[0].id].comments) == 1
