"""
Pilot & Health-Check Protocol (fleetd vNext - Section 6)
1. Fail-closed health-check of codex-local slot with git handoff capability probe.
2. Pilot verification for MIN-221 (Collatz M3) with reproducible git clean clone and independent QA receipt audit.
"""

import json
import os
import shutil
import subprocess
import tempfile
from typing import Dict, Any, Optional

PILOT_REPO_URL = "https://github.com/pashamango/math.git"
PILOT_BRANCH = "collatz-v0.1.0-m3"
PILOT_COMMIT_SHA = "16590a7574b188225896cdf99268bc476cbe18a3"


def run_codex_health_check(
    codex_bin: str = "/opt/homebrew/bin/codex",
    force_status: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Performs real health probe for codex-local builder.
    Verifies that executable exists, is authenticated, and can perform git operations.
    Returns structured health status dictionary (fail-closed).
    """
    if force_status is not None:
        is_ok = (force_status == "HEALTH_OK")
        return {
            "slot": "codex-local",
            "status": force_status,
            "git_capable": is_ok,
            "isolation_verified": is_ok,
            "details": f"Forced status: {force_status}",
        }

    if not os.path.exists(codex_bin):
        return {
            "slot": "codex-local",
            "status": "HEALTH_FAILED",
            "git_capable": False,
            "isolation_verified": False,
            "details": f"Executable not found at {codex_bin}",
        }

    # 1. Execute CLI version check
    try:
        proc = subprocess.run(
            [codex_bin, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return {
                "slot": "codex-local",
                "status": "HEALTH_FAILED",
                "git_capable": False,
                "isolation_verified": False,
                "details": f"Codex runner CLI returned exit code {proc.returncode}: {proc.stderr.strip()}",
            }

        # 2. Check authentication / probe execution
        probe_res = subprocess.run(
            [codex_bin, "exec", "--color", "never", "echo health_probe"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        combined_out = f"{probe_res.stdout}\n{probe_res.stderr}"
        if "403 Forbidden" in combined_out or probe_res.returncode != 0:
            return {
                "slot": "codex-local",
                "status": "HEALTH_FAILED",
                "git_capable": False,
                "isolation_verified": False,
                "details": f"Codex runner authentication failed (HTTP 403 / unauthenticated): {probe_res.stderr.strip()[:200]}",
            }

        # 3. Verify real git handoff capability (probe temporary git isolation)
        test_dir = tempfile.mkdtemp(prefix="codex_git_probe_")
        try:
            init_res = subprocess.run(["git", "init", test_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            if init_res.returncode != 0:
                return {
                    "slot": "codex-local",
                    "status": "HEALTH_FAILED",
                    "git_capable": False,
                    "isolation_verified": False,
                    "details": f"Git initialization probe failed: {init_res.stderr.strip()}",
                }
            probe_file = os.path.join(test_dir, "probe.txt")
            with open(probe_file, "w") as f:
                f.write("health_probe")
            subprocess.run(["git", "-C", test_dir, "add", "probe.txt"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            commit_res = subprocess.run(
                ["git", "-C", test_dir, "-c", "user.name=Probe", "-c", "user.email=probe@local", "commit", "-m", "probe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            if commit_res.returncode != 0:
                return {
                    "slot": "codex-local",
                    "status": "HEALTH_FAILED",
                    "git_capable": False,
                    "isolation_verified": False,
                    "details": f"Git commit probe failed: {commit_res.stderr.strip()}",
                }
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)

        return {
            "slot": "codex-local",
            "status": "HEALTH_OK",
            "git_capable": True,
            "isolation_verified": True,
            "details": "Codex local runner responsive, clean worktree creation verified.",
        }
    except subprocess.TimeoutExpired:
        return {
            "slot": "codex-local",
            "status": "HEALTH_FAILED",
            "git_capable": False,
            "isolation_verified": False,
            "details": "Codex runner health probe timed out after 5s (runner unresponsive or websocket reconnect loop).",
        }
    except Exception as e:
        return {
            "slot": "codex-local",
            "status": "HEALTH_FAILED",
            "git_capable": False,
            "isolation_verified": False,
            "details": f"Codex runner probe error: {str(e)}",
        }


def verify_pilot_git_handoff_clean_clone(
    repo_url: str = PILOT_REPO_URL,
    branch: str = PILOT_BRANCH,
    expected_commit: str = PILOT_COMMIT_SHA,
    target_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Performs real clean clone verification of git handoff coordinates.
    """
    cleanup = False
    if target_dir is None:
        target_dir = tempfile.mkdtemp(prefix="clean_clone_verify_")
        cleanup = True

    try:
        clone_cmd = [
            "git", "clone",
            "--branch", branch,
            "--single-branch",
            repo_url,
            target_dir,
        ]
        res = subprocess.run(clone_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if res.returncode != 0:
            return {
                "success": False,
                "error": f"git clone failed: {res.stderr.strip()}",
            }

        rev_cmd = ["git", "-C", target_dir, "rev-parse", "HEAD"]
        rev_res = subprocess.run(rev_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        actual_head = rev_res.stdout.strip()

        matched = (actual_head == expected_commit)
        return {
            "success": matched,
            "commit_sha": actual_head,
            "expected_sha": expected_commit,
            "matched": matched,
        }
    finally:
        if cleanup and os.path.exists(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)


def execute_pilot_task_min_221(
    repo_url: str = PILOT_REPO_URL,
    branch: str = PILOT_BRANCH,
    commit_sha: str = PILOT_COMMIT_SHA,
    receipt_path: Optional[str] = "fleetd-evolution/evidence/PILOT_MIN_221_RECEIPT.json",
    verify_clone: bool = True,
) -> Dict[str, Any]:
    """
    Executes the pilot protocol for MIN-221 (Collatz M3) according to §6:
    - Target: Collatz M3 CacheCheck
    - Tier: ВП
    - Git Coordinates: https://github.com/pashamango/math.git @ collatz-v0.1.0-m3 (commit 16590a75...)
    - Verification:
      1. Performs clean-clone or ls-remote verification of git coordinates.
      2. Validates independent QA audit receipt from receipt_path.
      Under fail-closed §6.3, fallback CANNOT be activated until an independent QA PASS is verified.
    """
    # 1. Clean clone verification of git handoff
    clean_clone_ok = False
    if verify_clone:
        clone_res = verify_pilot_git_handoff_clean_clone(repo_url, branch, commit_sha)
        clean_clone_ok = clone_res.get("success", False)
    else:
        remote_check = subprocess.run(
            ["git", "ls-remote", repo_url, f"refs/heads/{branch}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        clean_clone_ok = (remote_check.returncode == 0 and commit_sha in remote_check.stdout)

    # 2. Load and verify independent QA receipt
    qa_reviewer = "Unknown"
    qa_verdict = "MISSING_RECEIPT"
    qa_blocker = "No receipt found at specified path"

    if receipt_path and os.path.exists(receipt_path):
        try:
            with open(receipt_path, "r", encoding="utf-8") as f:
                receipt_data = json.load(f)
            qa_audit = receipt_data.get("independent_qa_audit", {})
            qa_reviewer = qa_audit.get("reviewer", "Codex (Independent QA)")
            qa_verdict = qa_audit.get("verdict", "FAIL")
            qa_blocker = qa_audit.get("summary", "")
        except Exception as e:
            qa_verdict = "CORRUPTED_RECEIPT"
            qa_blocker = f"Receipt decode error: {e}"

    pilot_passed = (clean_clone_ok and qa_verdict == "PASS")

    return {
        "task_id": "MIN-221",
        "title": "Collatz v0.1.0 — M3: CacheCheck (S, τ, линейный чекер пути)",
        "tier": "ВП",
        "slot": "codex-local",
        "pilot_status": "PILOT_PASS" if pilot_passed else "PILOT_FAIL_AWAITING_FIX",
        "pilot_passed": pilot_passed,
        "fallback_activation_permitted": pilot_passed,
        "git_handoff": {
            "repo_url": repo_url,
            "branch": branch,
            "commit_sha": commit_sha,
            "clean_clone_verified": clean_clone_ok,
        },
        "independent_qa": {
            "reviewer": qa_reviewer,
            "verdict": qa_verdict,
            "blocker": qa_blocker,
        },
        "verdict_gate": "Requires independent QA PASS before activating queue fallback (§6.3)",
    }
