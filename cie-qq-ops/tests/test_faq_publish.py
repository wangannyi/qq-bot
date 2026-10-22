"""Local-remote integration coverage for daily FAQ publication.

These tests create a disposable bare Git remote for every case.  They never
touch the real FAQ repository, its remote, or the production candidate store.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import faq_sync


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _new_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create an initialized checkout with a local-only bare origin."""

    remote = tmp_path / "faq-origin.git"
    checkout = tmp_path / "faq-checkout"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(
        ["git", "init", "--initial-branch=master", str(checkout)],
        check=True,
        capture_output=True,
    )
    _git(checkout, "config", "user.name", "FAQ Test")
    _git(checkout, "config", "user.email", "faq-test@example.invalid")
    (checkout / "faq.md").write_text("# CIE 技术问题 FAQ\n", encoding="utf-8")
    _git(checkout, "add", "faq.md")
    _git(checkout, "commit", "-m", "docs: initialize FAQ")
    _git(checkout, "remote", "add", "origin", str(remote))
    _git(checkout, "push", "-u", "origin", "master")
    return checkout, remote


def _candidate_database(path: Path, *, verified: bool) -> str:
    reference = {
        "title": "K3 llama.cpp 部署指南",
        "url": "https://www.spacemit.com/community/document/k3-llm",
        "verified": verified,
    }
    raw_references = json.dumps([reference], ensure_ascii=False)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE candidates (
                fingerprint TEXT PRIMARY KEY,
                question TEXT,
                original_answer TEXT,
                references_json TEXT,
                qq_summary TEXT,
                fetched_at TEXT,
                status TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO candidates VALUES (?,?,?,?,?,?,?)",
            (
                "candidate-k3-llm",
                "K3 如何部署 LLM？",
                "适用于 K3 Bianbu 系统。\n\n1、安装官方工具。\n\n2、按官方说明下载模型。",
                raw_references,
                "K3 LLM 部署简答",
                "2026-09-10T12:00:00+08:00",
                "pending_review",
            ),
        )
    return raw_references


def _candidate_row(path: Path) -> tuple[str, str]:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status, references_json FROM candidates WHERE fingerprint = ?",
            ("candidate-k3-llm",),
        ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def _handled_question() -> dict[str, object]:
    return {
        "timestamp": 1_789_000_000.0,
        "text": "K3 如何部署 LLM？",
        "status": "handled",
    }


def test_verified_candidate_auto_merges_remote_and_marks_published_after_fast_forward(tmp_path, monkeypatch):
    """Only a verified candidate can cross the automated reviewer gate."""

    checkout, remote = _new_repo(tmp_path)
    candidate_db = tmp_path / "candidates.sqlite"
    _candidate_database(candidate_db, verified=True)
    faq_before = (checkout / "faq.md").read_text(encoding="utf-8")
    head_before = _git(checkout, "rev-parse", "HEAD")
    expected_faq = tmp_path / "faq-before-publish.md"
    expected_faq.write_text(faq_before, encoding="utf-8")
    hook_marker = tmp_path / "pre-merge-hook-ran"
    hook = checkout / ".git" / "hooks" / "pre-merge-commit"
    hook.write_text(
        "#!/bin/sh\n"
        "cmp -s \"$FAQ_LIVE_PATH\" \"$FAQ_EXPECTED_PATH\" || exit 42\n"
        ": > \"$FAQ_HOOK_MARKER\"\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    monkeypatch.setenv("FAQ_LIVE_PATH", str(checkout / "faq.md"))
    monkeypatch.setenv("FAQ_EXPECTED_PATH", str(expected_faq))
    monkeypatch.setenv("FAQ_HOOK_MARKER", str(hook_marker))

    result = faq_sync.publish_daily_candidates(
        [_handled_question()],
        candidate_db,
        checkout,
        auto_approve_if_clean=True,
    )

    assert result.published is True
    assert result.review is not None and result.review.approved is True
    assert result.source_commit and result.merge_commit
    assert result.local_checkout_updated is True
    assert _candidate_row(candidate_db)[0] == "published"

    # The hook runs during the temporary-worktree merge and proves the live
    # checkout was still pristine before publication fast-forwarded it.
    assert hook_marker.exists()
    assert "## K3 如何部署 LLM？" not in faq_before
    assert _git(checkout, "show", f"{head_before}:faq.md") == faq_before.rstrip()
    remote_master = _git(checkout, "rev-parse", "origin/master")
    assert remote_master == result.merge_commit
    assert "## K3 如何部署 LLM？" in _git(checkout, "show", "origin/master:faq.md")
    assert _git(checkout, "ls-remote", "origin", "refs/heads/master").split()[0] == result.merge_commit

    assert _git(checkout, "rev-parse", "HEAD") == result.merge_commit
    assert "## K3 如何部署 LLM？" in (checkout / "faq.md").read_text(encoding="utf-8")
    assert remote.exists()  # Guard against a test accidentally using another remote.


def test_unverified_reference_never_enters_auto_merge_path(tmp_path):
    """An unverified link leaves neither a target update nor an approval claim."""

    checkout, _ = _new_repo(tmp_path)
    candidate_db = tmp_path / "candidates.sqlite"
    _candidate_database(candidate_db, verified=False)
    remote_head_before = _git(checkout, "rev-parse", "origin/master")

    result = faq_sync.publish_daily_candidates(
        [_handled_question()],
        candidate_db,
        checkout,
        auto_approve_if_clean=True,
    )

    assert result.published is False
    assert _git(checkout, "rev-parse", "origin/master") == remote_head_before
    assert _git(checkout, "ls-remote", "origin", "refs/heads/master").split()[0] == remote_head_before
    assert not _git(checkout, "ls-remote", "origin", f"refs/heads/{result.source_branch}")
    assert _candidate_row(candidate_db)[0] == "pending_review"


def test_dry_run_does_not_mutate_candidate_status_or_git_refs(tmp_path):
    """A release preview is informational and must leave every real state intact."""

    checkout, _ = _new_repo(tmp_path)
    candidate_db = tmp_path / "candidates.sqlite"
    raw_references = _candidate_database(candidate_db, verified=True)
    faq_before = (checkout / "faq.md").read_text(encoding="utf-8")
    local_refs_before = _git(checkout, "for-each-ref", "--format=%(refname):%(objectname)")
    remote_head_before = _git(checkout, "ls-remote", "origin", "refs/heads/master")

    result = faq_sync.publish_daily_candidates(
        [_handled_question()],
        candidate_db,
        checkout,
        auto_approve_if_clean=True,
        dry_run=True,
    )

    assert result.published is False
    assert result.staged.added == 1
    assert "干运行" in result.detail
    status, references = _candidate_row(candidate_db)
    assert status == "pending_review"
    assert references == raw_references
    assert (checkout / "faq.md").read_text(encoding="utf-8") == faq_before
    assert _git(checkout, "for-each-ref", "--format=%(refname):%(objectname)") == local_refs_before
    assert _git(checkout, "ls-remote", "origin", "refs/heads/master") == remote_head_before
