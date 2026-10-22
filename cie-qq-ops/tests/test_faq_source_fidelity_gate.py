"""Regression coverage for current-official-source FAQ auto-review checks."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

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


def _create_branch_with_entry(
    tmp_path: Path, answer: str, reference_urls: str | tuple[str, ...]
) -> tuple[Path, Path]:
    """Create a tiny FAQ repository and one committed source branch."""

    remote = tmp_path / "origin.git"
    repo = tmp_path / "faq"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(
        ["git", "init", "--initial-branch=master", str(repo)],
        check=True,
        capture_output=True,
    )
    _git(repo, "config", "user.name", "FAQ Gate Test")
    _git(repo, "config", "user.email", "faq-gate-test@example.invalid")
    (repo / "faq.md").write_text("# CIE 技术问题 FAQ\n", encoding="utf-8")
    _git(repo, "add", "faq.md")
    _git(repo, "commit", "-m", "docs: initialize FAQ")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "master")

    urls = (reference_urls,) if isinstance(reference_urls, str) else reference_urls
    _git(repo, "switch", "-c", "bot/source-fidelity")
    (repo / "faq.md").write_text(
        "# CIE 技术问题 FAQ\n\n"
        "## K3 如何部署 LLM？\n\n"
        f"{answer}\n\n"
        "### 已核验的官方参考\n\n"
        + "".join(f"- [官方开发者文档 {index}]({url})\n" for index, url in enumerate(urls, 1)),
        encoding="utf-8",
    )
    _git(repo, "add", "faq.md")
    _git(repo, "commit", "-m", "docs: add FAQ entry")
    _git(repo, "switch", "master")

    candidates = tmp_path / "candidates.sqlite"
    with sqlite3.connect(candidates) as connection:
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
        references = [
            {
                "title": f"官方开发者文档 {index}",
                "url": url,
                "verified": True,
            }
            for index, url in enumerate(urls, 1)
        ]
        connection.execute(
            "INSERT INTO candidates VALUES (?,?,?,?,?,?,?)",
            (
                "candidate-source-fidelity",
                "K3 如何部署 LLM？",
                answer,
                json.dumps(references, ensure_ascii=False),
                "",
                "2026-09-10T12:00:00+08:00",
                "queued_for_review",
            ),
        )
    return repo, candidates


def _write_index(path: Path, documents: dict[str, str]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE documents (path TEXT UNIQUE, body TEXT)")
        connection.executemany(
            "INSERT INTO documents(path, body) VALUES (?, ?)",
            tuple(documents.items()),
        )


def _review(repo: Path, candidates: Path) -> faq_sync.BranchReviewResult:
    return faq_sync.auto_review_branch(
        repo,
        "bot/source-fidelity",
        "master",
        candidates,
        candidate_fingerprints=("candidate-source-fidelity",),
    )


@pytest.mark.parametrize(
    ("nodepath", "source_body", "answer", "expected"),
    (
        (
            "ai/application_tools/ollama.md",
            "| K3 Bianbu LXQT/GNOME | ❌ 不支持加速 |",
            "K3 Bianbu 上的 Ollama 支持硬件加速，并会自动开启加速。",
            "K3 Bianbu不支持加速",
        ),
        (
            "ai/compute_stack/ai_compute_stack/llama.cpp.md",
            "| K3 Buildroot | ✅ 支持 |",
            "llama.cpp 仅 K3 Bianbu LXQT/GNOME 环境支持。",
            "K3 Buildroot也支持",
        ),
        (
            "software/SDK/bianbu/development/python.md",
            "pip config set global.extra-index-url https://git.spacemit.com/api/v4/projects/33/packages/pypi/simple",
            "使用 `pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple` 配置清华 PyPI 源。",
            "未支持清华PyPI源",
        ),
    ),
)
def test_auto_review_rejects_current_source_contradictions(
    tmp_path, monkeypatch, nodepath, source_body, answer, expected
):
    """Known K3 and PyPI inversions cannot reach the automatic merge path."""

    url = f"https://www.spacemit.com/community/document/info?nodepath={nodepath}&lang=zh"
    repo, candidates = _create_branch_with_entry(tmp_path, answer, url)
    index = tmp_path / "docs-v2.sqlite"
    _write_index(index, {nodepath: source_body})
    monkeypatch.setattr(faq_sync, "OFFICIAL_DOCS_INDEX", index)

    review = _review(repo, candidates)

    assert review.approved is False
    assert expected in review.detail


def test_auto_review_rejects_missing_current_document_snapshot(tmp_path, monkeypatch):
    nodepath = "ai/application_tools/ollama.md"
    url = f"https://www.spacemit.com/community/document/info?nodepath={nodepath}&lang=zh"
    repo, candidates = _create_branch_with_entry(tmp_path, "按官方文档安装工具。", url)
    index = tmp_path / "docs-v2.sqlite"
    _write_index(index, {})
    monkeypatch.setattr(faq_sync, "OFFICIAL_DOCS_INDEX", index)

    review = _review(repo, candidates)

    assert review.approved is False
    assert "当前官方文档快照缺少" in review.detail


def test_auto_review_allows_llama_cpp_acceleration_in_a_mixed_method_entry(tmp_path, monkeypatch):
    """A llama.cpp acceleration fact must not be attributed to Ollama."""

    ollama_path = "ai/application_tools/ollama.md"
    llama_path = "ai/compute_stack/ai_compute_stack/llama.cpp.md"
    ollama_url = f"https://www.spacemit.com/community/document/info?nodepath={ollama_path}&lang=zh"
    llama_url = f"https://www.spacemit.com/community/document/info?nodepath={llama_path}&lang=zh"
    answer = (
        "### 方法一：Ollama\n"
        "因此可以按其安装/运行方式试用，但不能把它作为 K3 硬件加速方案。\n\n"
        "### 方法二：llama.cpp\n"
        "llama.cpp 支持硬件加速，可用于本地推理。"
    )
    repo, candidates = _create_branch_with_entry(tmp_path, answer, (ollama_url, llama_url))
    index = tmp_path / "docs-v2.sqlite"
    _write_index(
        index,
        {
            ollama_path: "| K3 Bianbu LXQT/GNOME | ❌ 不支持加速 |",
            llama_path: "| K3 Buildroot | ✅ 支持 |",
        },
    )
    monkeypatch.setattr(faq_sync, "OFFICIAL_DOCS_INDEX", index)

    review = _review(repo, candidates)

    assert review.approved is True
    # A question title can mention Ollama while parallel method headings make
    # the local llama.cpp acceleration statement unambiguously separate.
    ollama_scope = faq_sync._ollama_acceleration_claim_scope(
        "K3 上如何部署 Ollama 和 LLM？", answer
    )
    assert "llama.cpp 支持硬件加速" not in ollama_scope


def test_auto_review_keeps_legacy_no_nodepath_reference_compatible(tmp_path, monkeypatch):
    """Existing first-party links without a document nodepath stay on old gate."""

    repo, candidates = _create_branch_with_entry(
        tmp_path,
        "按官方文档安装工具。",
        "https://www.spacemit.com/community/document/k3-llm",
    )
    missing_index = tmp_path / "missing-docs-v2.sqlite"
    monkeypatch.setattr(faq_sync, "OFFICIAL_DOCS_INDEX", missing_index)

    review = _review(repo, candidates)

    assert review.approved is True
