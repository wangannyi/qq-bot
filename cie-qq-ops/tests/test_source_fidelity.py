"""Preserve the supplied K3 answer's limitations and multi-document provenance."""
import website_bridge as bridge
from test_summary_validation import fake_model, payload

RAW = """K3仅 **Bianbu LXQT/GNOME、Buildroot系统** 支持LLM部署，OpenHarmony系统暂不支持。
推荐llama.cpp：安装llama.cpp-tools-spacemit，旧固件没有包时可用llama-server。
下载Qwen3-30B-A3B的Q4_0量化模型。
性能测试用llama-bench，交互用llama-cli，服务用llama-server。
llm-sdk仅Bianbu系统支持，带REST API。
Ollama工具目前K3 Bianbu系统不支持硬件加速，因此优先推荐使用llama.cpp方案部署。
llama.cpp部署：https://www.spacemit.com/community/document/info?nodepath=ai/compute_stack/ai_compute_stack/llama.cpp.md&lang=zh
<a href="https://www.spacemit.com/community/document/info?nodepath=ai/application_tools/llmsdk.md&amp;lang=zh">llmsdk.md</a>
[K1教程](https://www.spacemit.com/community/document/info?nodepath=competition/01_K1_llm.md)
"""


def test_deployment_keeps_conditions_modes_notes_and_multiple_sources(monkeypatch):
    steps = [
        "K3支持的系统上使用llama.cpp，安装llama.cpp-tools-spacemit；旧固件没有包时可用llama-server。",
        "下载Qwen3-30B-A3B的Q4_0量化模型。",
        "使用llama-bench做性能测试、llama-cli交互，或llama-server启动服务。",
    ]
    condition = "Bianbu LXQT/GNOME、Buildroot支持；OpenHarmony暂不支持。"
    fake_model(monkeypatch, [dict(payload(""), methods=[
        {"name": "llama.cpp", "condition": condition, "steps": steps},
        {"name": "LLM SDK", "condition": "仅Bianbu系统支持，带REST API。", "steps": ["安装服务。", "访问服务接口。"]},
    ])])
    evidence = {"original_answer": RAW,
                "references": [dict(item, verified=True) for item in bridge.references(RAW)]}
    answer = bridge.summarize("k3上如何部署llm", evidence)
    assert "1、" + steps[0] in answer
    assert "3、" + steps[2] in answer
    assert "\n\n待补充：" in answer and "4、请补充" not in answer
    assert "官网提示" not in answer
    assert condition in answer
    assert "方法二：LLM SDK\n仅Bianbu系统支持，带REST API。\n1、" in answer
    assert "[🔗llama.cpp 部署指南]" in answer and "[🔗LLM SDK]" in answer
    assert "[🔗K1教程]" not in answer  # Cross-board tutorial is not a K3 deployment citation.
    assert evidence["follow_up"] == bridge.SYSTEM_FOLLOW_UP


def test_notes_are_from_current_answer_not_hardcoded_k3_facts():
    assert bridge.source_notes("K3可使用llama.cpp。") == []
    assert bridge.source_notes("**Buildroot暂不支持。**") == ["Buildroot暂不支持。"]


def test_source_ranking_uses_relevance_and_actual_dates_not_fetch_time():
    sources = [
        {"title": "llama.cpp旧版", "url": "https://docs.spacemit.com/llama-old", "updated_at": "2024-01-01"},
        {"title": "llama.cpp新版", "url": "https://docs.spacemit.com/llama-new", "updated_at": "2026-09-01"},
        {"title": "K1 llama.cpp", "url": "https://docs.spacemit.com/k1/llama", "updated_at": "2026-09-09"},
        {"title": "llama.cpp未知日期", "url": "https://docs.spacemit.com/llama-unknown", "fetched_at": "2099-01-01"},
    ]
    result = bridge.ranked_references("K3如何部署llm", sources, 0)
    assert [s["title"] for s in result] == ["llama.cpp新版", "llama.cpp旧版", "llama.cpp未知日期"]


def test_unrelated_boards_never_become_supporting_links():
    refs = bridge.references(RAW)
    assert len(refs) == 3
    assert len(bridge.ranked_references("K3如何部署llm", refs, 2)) == 2


def test_cloud_citations_exclude_unrelated_book_and_overview():
    sources = [
        {"title": "cloud_user_guide", "url": "https://docs.spacemit.com/cloud/guide"},
        {"title": "book_user_guide", "url": "https://docs.spacemit.com/k1/book"},
    ]
    assert bridge.ranked_references("怎么申请bianbu cloud", sources, 0) == sources[:1]


def test_table_caveat_is_readable_on_mobile():
    assert bridge.source_notes("| LLM SDK | K3 Buildroot | 不支持 |") == ["LLM SDK；K3 Buildroot；不支持"]


def test_conditional_steps_are_not_replaced_by_unnecessary_human_escalation(monkeypatch):
    fake_model(monkeypatch, [
        {"body": "需要人工确认：系统版本信息缺失。", "steps": [], "status": "needs_human", "source_id": None},
        dict(payload(""), steps=["安装llama.cpp工具包。", "下载Qwen3模型。", "使用llama-cli运行。"]),
    ])
    answer = bridge.summarize("K3如何部署llm", {"original_answer": RAW, "references": bridge.references(RAW)})
    assert answer.startswith("1、安装") and "待补充：" in answer


def test_tool_specific_system_heading_not_forwarded_as_global_incompatibility():
    answer = "### K1 Bianbu LXQT/GNOME（不支持）\n目前LLM SDK不支持该系统。"
    assert bridge.source_notes(answer) == ["K1 Bianbu LXQT/GNOME（不支持）：目前LLM SDK不支持该系统。"]
