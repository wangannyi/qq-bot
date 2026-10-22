"""Regression contracts for detailed FAQ rendering.

The daily publisher receives a long answer from the official community bot,
but the FAQ must remain a readable, self-contained record.  These tests lock
down the two safety boundaries here: commands must not lose their URLs, while
only explicitly verified, relevant official documents may be labelled as
references.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import faq_sync

PYTHON_GUIDE_URL = (
    "https://www.spacemit.com/community/document/info?"
    "nodepath=software/SDK/bianbu/development/python.md&lang=zh"
)
OLLAMA_URL = (
    "https://www.spacemit.com/community/document/info?"
    "nodepath=ai/application_tools/ollama.md&lang=zh"
)
LLAMA_CPP_URL = (
    "https://www.spacemit.com/community/document/info?"
    "nodepath=ai/compute_stack/ai_compute_stack/llama.cpp.md&lang=zh"
)
LLM_SDK_URL = (
    "https://www.spacemit.com/community/document/info?"
    "nodepath=ai/application_tools/llmsdk.md&lang=zh"
)


def _reference(title: str, url: str, path: str) -> faq_sync.Reference:
    return faq_sync.Reference(title=title, url=url, canonical_path=path)


def _candidate(
    question: str,
    answer: str,
    references: tuple[faq_sync.Reference, ...] = (),
) -> faq_sync.Candidate:
    return faq_sync.Candidate(
        fingerprint="candidate-rendering",
        question=question,
        original_answer=answer,
        references=references,
    )


def _entry_reference_urls(entry: str) -> list[str]:
    """Read the URLs from the publisher-owned verified-reference section."""

    marker = "### 已核验的官方参考"
    if marker not in entry:
        return []
    section = entry.split(marker, 1)[1]
    return [
        line.rsplit("(", 1)[1].rstrip(")")
        for line in section.splitlines()
        if line.startswith("- [") and "](" in line
    ]


def test_preserves_pypi_urls_inside_inline_commands():
    """A FAQ must not turn a usable pip command into an incomplete fragment."""

    tuna = "https://pypi.tuna.tsinghua.edu.cn/simple"
    spacemit = "https://git.spacemit.com/api/v4/projects/33/packages/pypi/simple"
    answer = (
        f"设置主源：`pip config set global.index-url {tuna}`\n"
        f"设置额外源：`pip config set global.extra-index-url {spacemit}`"
    )

    rendered = faq_sync.sanitize_answer(answer)

    assert f"pip config set global.index-url {tuna}" in rendered
    assert f"pip config set global.extra-index-url {spacemit}" in rendered


def test_normalizes_markdown_url_inside_fenced_shell_command():
    """Markdown copied into a shell fence must become a copyable bare URL."""

    model_url = "https://www.modelscope.cn/models/acme/model/resolve/main/model.gguf"
    answer = f"```bash\nwget [{model_url}]({model_url}) -P ~/models\n```"

    rendered = faq_sync.sanitize_answer(answer)

    assert f"wget {model_url} -P ~/models" in rendered
    assert f"[{model_url}]({model_url})" not in rendered


def test_removes_orphaned_source_labels_from_normal_answer_body():
    """Raw source-list headings belong to the curated footer, never the body."""

    answer = (
        "配置完成后执行 pip config list 验证。\n\n"
        "官方文档链接：\n\n"
        "LLM SDK：\n"
    )

    rendered = faq_sync.sanitize_answer(answer)

    assert rendered == "配置完成后执行 pip config list 验证。"
    assert "官方文档链接" not in rendered
    assert "LLM SDK：" not in rendered


def test_python_and_pypi_entries_keep_only_the_current_python_guide():
    """Old release notes and generic hosting pages must not dilute Python FAQs."""

    references = (
        _reference(
            "bianbu_1.0.md",
            "https://www.spacemit.com/community/document/info?nodepath=software/SDK/bianbu/release_notes/history/bianbu_1.0.md&lang=zh",
            "software/SDK/bianbu/release_notes/history/bianbu_1.0.md",
        ),
        _reference(
            "包托管服务",
            "https://www.spacemit.com/community/document/info?nodepath=software/package-hosting.md&lang=zh",
            "software/package-hosting.md",
        ),
        _reference(
            "Python 使用指南",
            PYTHON_GUIDE_URL,
            "software/SDK/bianbu/development/python.md",
        ),
        _reference(
            "Python 使用指南（重复）",
            PYTHON_GUIDE_URL + "#duplicate",
            "software/SDK/bianbu/development/python.md",
        ),
    )
    candidate = _candidate(
        "Bianbu 如何设置 SpacemiT PyPI 源？",
        "在虚拟环境中配置主源和 SpacemiT 额外源。",
        references,
    )

    rendered = faq_sync._render_entry(candidate.question, candidate)

    assert _entry_reference_urls(rendered) == [PYTHON_GUIDE_URL]
    assert "bianbu_1.0.md" not in rendered
    assert "包托管服务" not in rendered


def test_k3_llm_entry_keeps_only_the_three_relevant_method_guides():
    """K3 LLM FAQ should omit unrelated Speech SDK and duplicate documents."""

    references = (
        _reference(
            "Speech SDK",
            "https://www.spacemit.com/community/document/info?nodepath=ai/application_tools/speechsdk.md&lang=zh",
            "ai/application_tools/speechsdk.md",
        ),
        _reference("LLM SDK", LLM_SDK_URL, "ai/application_tools/llmsdk.md"),
        _reference("llama.cpp 部署文档", LLAMA_CPP_URL, "ai/compute_stack/ai_compute_stack/llama.cpp.md"),
        _reference("Ollama 部署指南", OLLAMA_URL, "ai/application_tools/ollama.md"),
        _reference(
            "LLM SDK（重复）",
            LLM_SDK_URL + "#repeat",
            "ai/application_tools/llmsdk.md",
        ),
    )
    candidate = _candidate(
        "K3 Bianbu 4.0 上如何部署 LLM？",
        "可使用 Ollama、llama.cpp 或 LLM SDK 部署。",
        references,
    )

    rendered = faq_sync._render_entry(candidate.question, candidate)
    urls = _entry_reference_urls(rendered)

    assert set(urls) == {OLLAMA_URL, LLAMA_CPP_URL, LLM_SDK_URL}
    assert len(urls) == 3
    assert "Speech SDK" not in rendered


def test_unverified_or_missing_verification_never_gets_verified_reference_heading():
    """Only a literal ``verified: true`` source may appear under the footer."""

    raw_references = [
        {
            "title": "缺少验证标记",
            "url": "https://www.spacemit.com/community/document/missing-verification",
            "canonical_path": "community/document/missing-verification",
        },
        {
            "title": "显式未验证",
            "url": "https://www.spacemit.com/community/document/unverified",
            "canonical_path": "community/document/unverified",
            "verified": False,
        },
    ]
    candidate = _candidate(
        "K3 如何部署 LLM？",
        "按官方部署步骤执行。",
        faq_sync.canonical_references(raw_references),
    )

    rendered = faq_sync._render_entry(candidate.question, candidate)

    assert "### 已核验的官方参考" not in rendered
    assert "缺少验证标记" not in rendered
    assert "显式未验证" not in rendered
