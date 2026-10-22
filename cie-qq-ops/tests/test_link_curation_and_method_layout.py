"""Regression coverage for curated citations and parallel-method rendering."""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import website_bridge as bridge
from test_summary_validation import fake_model, payload

OLLAMA = "https://www.spacemit.com/community/document/info?nodepath=ai/application_tools/ollama.md&lang=zh"
# Same document as ``OLLAMA``; query-key order must not create a second QQ link.
OLLAMA_DUPLICATE = "https://www.spacemit.com/community/document/info?lang=zh&nodepath=ai/application_tools/ollama.md"
OLLAMA_OLD = "https://www.spacemit.com/community/document/info?nodepath=software/bianbu/ai/ollama.md&lang=zh"
LLAMA = "https://www.spacemit.com/community/document/info?nodepath=ai/compute_stack/ai_compute_stack/llama.cpp.md&lang=zh"
SDK = "https://www.spacemit.com/community/document/info?nodepath=ai/application_tools/llmsdk.md&lang=zh"
SPEECH = "https://www.spacemit.com/community/document/info?nodepath=ai/application_tools/speechsdk.md&lang=zh"
LLAMA_OLD = "https://www.spacemit.com/community/document/info?nodepath=ai/compute_stack/llamacpp.md&lang=zh"
SDK_OLD = "https://www.spacemit.com/community/document/info?nodepath=software/SDK/bianbu/ai/llmsdk.md&lang=zh"
# A website-assistant reply about configuring the SpacemiT PyPI source once
# cited this adjacent package-hosting page instead of the Bianbu Python guide.
# They are distinct documents and must never be treated as interchangeable.
DAK = "https://www.spacemit.com/community/document/info?nodepath=software/SDK/bianbu/development/dak.md&lang=zh"
PYTHON_DEVELOPMENT = "https://www.spacemit.com/community/document/info?nodepath=software/SDK/bianbu/development/python.md&lang=zh"

METHODS = [
    {"name": "Ollama", "condition": "适用于K3 Bianbu 4.0的LXQT/GNOME桌面环境。",
     "steps": ["更新软件源并安装spacemit-ollama-toolkit。", "拉取qwen3:0.6b模型。"]},
    {"name": "llama.cpp", "condition": "适用于K3 Bianbu 4.0的LXQT/GNOME桌面环境，支持硬件加速。",
     "steps": ["安装llama.cpp-tools-spacemit。", "下载GGUF模型并启动服务。"]},
]

SOURCE = """方式一：使用Ollama部署
适用于K3 Bianbu 4.0的LXQT/GNOME桌面环境。
更新软件源并安装spacemit-ollama-toolkit。
拉取qwen3:0.6b模型。
方式二：使用llama.cpp部署
适用于K3 Bianbu 4.0的LXQT/GNOME桌面环境，支持硬件加速。
安装llama.cpp-tools-spacemit。
下载GGUF模型并启动服务。
"""


def source(title, url):
    return {"title": title, "url": url}


def test_format_methods_uses_numbered_parallel_section_titles_and_restarts_steps():
    rendered = bridge.format_methods(METHODS, SOURCE)

    assert rendered.startswith(
        "方法一：Ollama\n适用于K3 Bianbu 4.0的LXQT/GNOME桌面环境。\n"
        "1、更新软件源并安装spacemit-ollama-toolkit。\n\n2、拉取qwen3:0.6b模型。\n\n"
    )
    assert "方法二：llama.cpp\n适用于K3 Bianbu 4.0的LXQT/GNOME桌面环境，支持硬件加速。\n1、安装llama.cpp-tools-spacemit。" in rendered
    # The second independent method begins at 1 rather than continuing the
    # preceding method's sequence.
    assert "方法二：llama.cpp" in rendered and "\n1、安装llama.cpp-tools-spacemit。" in rendered


def test_curated_references_canonical_deduplicates_and_keeps_source_order():
    sources = [
        source("Ollama部署LLM", OLLAMA),
        source("Ollama部署LLM（重复入口）", OLLAMA_DUPLICATE),
        source("llama.cpp部署文档", LLAMA),
    ]
    verifier = Mock(return_value=True)

    result = bridge.curated_references(
        "k3 bianbu4.0上如何部署llm", METHODS, sources, selected=0, verifier=verifier)

    assert [item["title"] for item in result] == ["Ollama部署LLM", "llama.cpp部署文档"]
    assert len(result) == 2
    assert result[0]["url"] == OLLAMA
    assert result[1]["url"] == LLAMA


def test_curated_references_uses_mock_verifier_to_drop_unreachable_and_irrelevant_links():
    sources = [
        source("Ollama部署文档（旧路径）", OLLAMA_OLD),
        source("Ollama部署LLM", OLLAMA),
        source("llama.cpp部署文档", LLAMA),
        source("LLM SDK", SDK),
        source("speechsdk", SPEECH),
    ]
    verifier = Mock(side_effect=lambda candidate: candidate["url"] != OLLAMA_OLD)

    result = bridge.curated_references(
        "k3 bianbu4.0上如何部署llm", METHODS, sources, selected=0, verifier=verifier)

    assert [item["title"] for item in result] == ["Ollama部署LLM", "llama.cpp部署文档"]
    assert all(item["url"] != OLLAMA_OLD for item in result)
    assert all(item["url"] not in (SDK, SPEECH) for item in result)
    assert verifier.called


def test_verified_references_maps_real_legacy_nodepaths_to_current_documents(monkeypatch):
    current_tree = [
        {"name": "Ollama", "path": "ai/application_tools/ollama.md", "updateTime": "2026-06-04T08:40:13Z"},
        {"name": "llama.cpp", "path": "ai/compute_stack/ai_compute_stack/llama.cpp.md", "updateTime": "2026-06-04T08:40:39Z"},
        {"name": "LLM SDK", "path": "ai/application_tools/llmsdk.md", "updateTime": "2026-06-04T08:40:06Z"},
    ]
    monkeypatch.setattr(bridge, "_current_document_index", lambda client: current_tree)
    legacy = [
        source("Ollama", OLLAMA_OLD),
        source("llama.cpp", LLAMA_OLD),
        source("LLM SDK", SDK_OLD),
    ]

    checked, notes = bridge.verified_references(legacy, Mock())

    assert notes == []
    assert [item["url"] for item in checked] == [OLLAMA, LLAMA, SDK]
    assert [item["canonical_path"] for item in checked] == [
        "ai/application_tools/ollama.md",
        "ai/compute_stack/ai_compute_stack/llama.cpp.md",
        "ai/application_tools/llmsdk.md",
    ]
    assert all(item["verified"] is True for item in checked)


def test_verified_references_adds_current_ollama_guide_when_original_mentions_it_without_link(monkeypatch):
    current_tree = [
        {"name": "Ollama", "path": "ai/application_tools/ollama.md", "updateTime": "2026-06-04T08:40:13Z"},
        {"name": "llama.cpp", "path": "ai/compute_stack/ai_compute_stack/llama.cpp.md", "updateTime": "2026-06-04T08:40:39Z"},
    ]
    monkeypatch.setattr(bridge, "_current_document_index", lambda client: current_tree)

    checked, notes = bridge.verified_references(
        [], Mock(), "方式一：使用Ollama部署（推荐，操作简单）。\n安装并运行Ollama。")

    assert notes == []
    assert checked == [{
        "title": "Ollama",
        "url": OLLAMA,
        "updated_at": "2026-06-04T08:40:13Z",
        "verified": True,
        "canonical_path": "ai/application_tools/ollama.md",
    }]


def test_verified_references_prefers_application_guide_over_newer_course_for_legacy_or_missing_ollama(monkeypatch):
    current_tree = [
        # This is the current operational guide. It must remain the canonical
        # deployment citation even if a course handout has a later timestamp.
        {"name": "Ollama", "path": "ai/application_tools/ollama.md", "updateTime": "2026-06-04T08:40:13Z"},
        {"name": "Ollama使用指南", "path": "courses/k3/ollama使用指南.md", "updateTime": "2026-09-09T08:40:13Z"},
    ]
    monkeypatch.setattr(bridge, "_current_document_index", lambda client: current_tree)
    original = "方式一：使用Ollama部署（推荐，操作简单）。"

    for source_list in ([source("Ollama（旧路径）", OLLAMA_OLD)], []):
        checked, _ = bridge.verified_references(source_list, Mock(), original)

        assert [item["url"] for item in checked] == [OLLAMA]
        assert [item["canonical_path"] for item in checked] == ["ai/application_tools/ollama.md"]


def test_curated_references_never_publishes_explicitly_unverified_source():
    sources = [
        dict(source("Ollama", OLLAMA), verified=True, canonical_path="ai/application_tools/ollama.md"),
        dict(source("llama.cpp", LLAMA), verified=False,
             canonical_path="ai/compute_stack/ai_compute_stack/llama.cpp.md"),
    ]

    result = bridge.curated_references("k3 bianbu4.0上如何部署llm", METHODS, sources, selected=0)

    assert [item["url"] for item in result] == [OLLAMA]
    assert all(item.get("verified") is not False for item in result)


def test_curated_references_pypi_question_replaces_wrong_dak_selection_with_python_guide():
    """A PyPI-source question must cite Bianbu's Python setup guide.

    ``selected=0`` models the website summarizer choosing the unrelated
    ``dak.md`` page.  The citation policy, rather than model selection, is
    the final authority for what reaches QQ.
    """
    sources = [
        dict(source("包托管服务", DAK), verified=True,
             canonical_path="software/SDK/bianbu/development/dak.md"),
        dict(source("Python 开发", PYTHON_DEVELOPMENT), verified=True,
             canonical_path="software/SDK/bianbu/development/python.md"),
    ]

    result = bridge.curated_references(
        "bianbu如何设置spacemit pypi源", [], sources, selected=0)

    assert [item["url"] for item in result] == [PYTHON_DEVELOPMENT]
    assert [item["canonical_path"] for item in result] == [
        "software/SDK/bianbu/development/python.md",
    ]


def test_curated_references_pypi_question_drops_dak_when_python_guide_is_unavailable():
    """Never publish an adjacent but unsupported document as a PyPI citation."""
    wrong_only = [dict(source("包托管服务", DAK), verified=True,
                       canonical_path="software/SDK/bianbu/development/dak.md")]

    result = bridge.curated_references(
        "bianbu如何设置spacemit pypi源", [], wrong_only, selected=0)

    assert result == []


def test_verified_references_pypi_question_injects_python_guide_before_curation(monkeypatch):
    """The real website reply may contain only the incorrect dak.md citation."""
    current_tree = [
        {"name": "包托管服务", "path": "software/SDK/bianbu/development/dak.md",
         "updateTime": "2026-08-04T08:40:13Z"},
        {"name": "Python 使用指南", "path": "software/SDK/bianbu/development/python.md",
         "updateTime": "2026-08-04T08:41:13Z"},
    ]
    monkeypatch.setattr(bridge, "_current_document_index", lambda client: current_tree)

    checked, notes = bridge.verified_references(
        [source("包托管服务", DAK)], Mock(),
        "配置 SpacemiT PyPI 的 extra-index-url。",
        question="bianbu如何设置spacemit pypi源",
    )
    result = bridge.curated_references(
        "bianbu如何设置spacemit pypi源", [], checked, selected=0)

    assert [item["canonical_path"] for item in checked] == [
        "software/SDK/bianbu/development/dak.md",
        "software/SDK/bianbu/development/python.md",
    ]
    assert [item["url"] for item in result] == [PYTHON_DEVELOPMENT]
    assert any("Python 使用指南" in note for note in notes)


def test_curated_references_non_pypi_question_keeps_selected_document_behavior():
    """Topic-specific correction must not override ordinary selected citations."""
    sources = [
        dict(source("包托管服务", DAK), verified=True,
             canonical_path="software/SDK/bianbu/development/dak.md"),
        dict(source("llama.cpp", LLAMA), verified=True,
             canonical_path="ai/compute_stack/ai_compute_stack/llama.cpp.md"),
        dict(source("Python 开发", PYTHON_DEVELOPMENT), verified=True,
             canonical_path="software/SDK/bianbu/development/python.md"),
    ]

    result = bridge.curated_references("K3上如何部署LLM", [], sources, selected=1)

    assert [item["url"] for item in result] == [LLAMA]


def test_ollama_acceleration_conflict_rejects_positive_acceleration_step_but_allows_neutral_caveat():
    freshness_note = (
        "当前 Ollama 官方文档的平台支持表与下载模型段落对 K3 加速的表述不一致；"
        "不要把 K3 上 Ollama 加速写为已确认结论，应明确该项需以官网后续说明或技术支持确认。"
    )
    original = "Ollama部署。可直接拉取加速模型。加速能力需确认。"
    evidence = {
        "original_answer": original,
        "references": [dict(source("Ollama", OLLAMA), verified=True)],
        "freshness_notes": [freshness_note],
    }
    positive = {
        "body": "", "status": "answered", "source_id": 0,
        "methods": [{"name": "Ollama", "condition": "加速表述不一致，能力需确认。",
                     "steps": ["可直接拉取加速模型。"]}],
    }
    neutral = {
        "body": "", "status": "answered", "source_id": 0,
        "methods": [{"name": "Ollama", "condition": "加速能力需确认。",
                     "steps": ["安装Ollama工具并拉取模型。"]}],
    }

    with pytest.raises(ValueError, match="current_doc_acceleration_conflict"):
        bridge.validate_summary(positive, "k3 bianbu4.0上如何部署llm", evidence, False, False)

    body, source_id = bridge.validate_summary(
        neutral, "k3 bianbu4.0上如何部署llm", evidence, False, False)
    assert source_id == 0
    assert "方法一：Ollama\n加速能力需确认。\n1、安装Ollama工具并拉取模型。" in body


def test_summary_uses_curated_links_once_with_parallel_method_layout(monkeypatch):
    sources = [
        source("Ollama部署LLM", OLLAMA),
        source("Ollama部署LLM（重复入口）", OLLAMA_DUPLICATE),
        source("llama.cpp部署文档", LLAMA),
        source("speechsdk", SPEECH),
    ]
    calls = []

    def curated(question, methods, received_sources, selected, verifier=None):
        calls.append((question, methods, received_sources, selected))
        return [sources[0], sources[2]]

    monkeypatch.setattr(bridge, "curated_references", curated)
    fake_model(monkeypatch, [dict(payload(""), methods=METHODS)])

    answer = bridge.summarize(
        "k3 bianbu4.0上如何部署llm",
        {"original_answer": SOURCE, "references": sources},
    )

    assert calls and calls[0][0] == "k3 bianbu4.0上如何部署llm"
    assert "方法一：Ollama\n适用于K3 Bianbu 4.0的LXQT/GNOME桌面环境。\n1、" in answer
    assert "方法二：llama.cpp\n适用于K3 Bianbu 4.0的LXQT/GNOME桌面环境，支持硬件加速。\n1、" in answer
    assert answer.count("[🔗Ollama 部署指南]") == 1
    assert answer.count("[🔗llama.cpp 部署指南]") == 1
    assert "Ollama部署LLM（重复入口）" not in answer
    assert "speechsdk" not in answer
