import sqlite3

import pytest
import website_bridge as bridge
from test_summary_validation import fake_model, payload

RAW = """一、Ollama（Bianbu）
安装spacemit-ollama-toolkit；ollama pull下载模型。
二、llama.cpp（Bianbu、Buildroot）
安装llama.cpp-tools-spacemit；下载模型；llama-server启动。
[Ollama](https://www.spacemit.com/community/document/info?nodepath=ai/application_tools/ollama.md&lang=zh)
[llama.cpp.md](https://www.spacemit.com/community/document/info?nodepath=ai/compute_stack/ai_compute_stack/llama.cpp.md&lang=zh)
[LLM SDK](https://www.spacemit.com/community/document/info?nodepath=ai/application_tools/llmsdk.md&lang=zh)
"""
METHODS = [
    {"name": "Ollama", "condition": "适用于Bianbu。", "steps": ["安装spacemit-ollama-toolkit。", "拉取模型并运行。"]},
    {"name": "llama.cpp", "condition": "适用于Bianbu、Buildroot。", "steps": ["安装llama.cpp-tools-spacemit。", "下载模型并通过llama-server运行。"]},
]


@pytest.fixture(autouse=True)
def event_rules(tmp_path, monkeypatch):
    path = tmp_path / "rules.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE documents(body,source_kind)")
        conn.execute("INSERT INTO documents VALUES (?,?)", (
            "部署工具必须使用进迭时空开源的 llama.cpp。K1 8GB，Qwen3.5-2B-Instruct和Qwen3.5-4B-Instruct。", "event_rules"))
    monkeypatch.setattr(bridge, "EVENT_RULES_INDEX", path)


def test_methods_use_numbered_sections_restart_steps_and_keep_relevant_links_in_order(monkeypatch):
    fake_model(monkeypatch, [dict(payload(""), methods=METHODS)])
    sources = [dict(item, verified=True) for item in bridge.references(RAW)]
    answer = bridge.summarize("K3如何部署LLM", {"original_answer": RAW, "references": sources})
    assert "方法一：Ollama\n适用于Bianbu。\n1、" in answer
    assert "方法二：llama.cpp\n适用于Bianbu、Buildroot。\n1、" in answer
    assert "官网提示" not in answer and "3、" not in answer
    assert answer.index("[🔗Ollama 部署指南]") < answer.index("[🔗llama.cpp 部署指南]")
    assert "[🔗LLM SDK]" not in answer
    assert "赛事选择" in answer and "若用于赛题2" in answer
    assert "K3 通用部署方案不等于赛题交付方案" in answer


def test_mixed_tools_in_one_method_rejected():
    bad = [{"name": "Ollama", "steps": ["安装Ollama。", "也可安装llama.cpp工具包。"]}]
    with pytest.raises(ValueError, match="mixed_parallel_methods"):
        bridge.format_methods(bad, RAW)


def test_source_named_application_may_use_its_documented_dependencies():
    methods = [{"name": "LLMChat", "condition": "OpenHarmony应用。", "steps": ["下载Ollama及模型。", "使用LLMChat进行对话。"]}]
    assert "方法一：LLMChat\n" in bridge.format_methods(methods, RAW + "LLMChat使用Ollama。")
    methods[0]["name"] = "多模态大语言模型（VLM）"
    assert "方法一：多模态大语言模型（VLM）\n" in bridge.format_methods(methods, RAW + "多模态 VLM 部署。")


def test_complete_long_step_not_rejected_or_cut_to_old_single_reply_limit():
    step = "指定完整模型文件路径，" * 12
    methods = [{"name": "llama.cpp", "steps": ["安装工具包。", step]}]
    assert step in bridge.format_methods(methods, RAW)


def test_method_with_one_source_step_does_not_force_invented_extra_steps():
    assert "1、安装服务。" in bridge.format_methods([{"name": "LLM SDK", "steps": ["安装服务。"]}], "LLM SDK安装服务。")


def test_source_backed_long_condition_is_not_mistaken_for_bad_method_name():
    condition = "仅K3 Bianbu 4.0的LXQT/GNOME桌面环境支持，K3 OpenHarmony 5.0与K3 Buildroot暂不支持；建议spacemit-ollama-toolkit版本0.0.8及以上以支持完整功能；直接拉取q4_K_M格式模型可自动享受加速，手动制作需在ModelScope下载q4_0量化精度的gguf格式模型。"
    assert 150 < len(condition) <= 200
    source = "### 方式一：使用Ollama部署（推荐，操作简单）\n" + condition
    result = bridge.format_methods([{"name": "Ollama", "condition": condition,
                                     "steps": ["安装spacemit-ollama-toolkit。", "拉取模型。"]}], source)
    assert condition in result and result.startswith("方法一：Ollama\n")


def test_long_condition_returns_actionable_retry_reason():
    source = "### 方式一：使用Ollama部署（推荐，操作简单）"
    with pytest.raises(ValueError, match="method_condition_too_long"):
        bridge.format_methods([{"name": "Ollama", "condition": "条" * 201,
                                "steps": ["安装工具。"]}], source)


def test_three_parallel_methods_with_production_length_conditions_are_accepted(monkeypatch):
    ollama = "仅K3 Bianbu 4.0的LXQT/GNOME桌面环境支持，K3 OpenHarmony 5.0与K3 Buildroot暂不支持；建议spacemit-ollama-toolkit版本0.0.8及以上以支持完整功能；直接拉取q4_K_M格式模型可自动享受加速，手动制作需在ModelScope下载q4_0量化精度的gguf格式模型。"
    llama = "仅K3 Bianbu 4.0的LXQT/GNOME桌面环境支持，K3 OpenHarmony 5.0与K3 Buildroot暂不支持；支持Q4_K_M、Q4_0、Q4_1、Q2_K、Q3_K五种量化格式的模型加速；旧固件可能没有llama.cpp-tools-spacemit包，可改用llama-server。"
    assert 150 < len(ollama) <= 200 and 150 < len(llama) <= 200
    methods = [
        {"name": "Ollama", "condition": ollama, "steps": ["安装Ollama工具。", "拉取模型。"]},
        {"name": "llama.cpp", "condition": llama, "steps": ["安装llama.cpp工具。", "下载模型。"]},
        {"name": "LLM SDK", "condition": "仅K3 Bianbu 4.0的LXQT/GNOME桌面环境支持。", "steps": ["安装llm-sdk。"]},
    ]
    source = "方式一：使用Ollama部署\n方式二：使用llama.cpp部署\n方式三：使用LLM SDK部署\n" + RAW
    fake_model(monkeypatch, [dict(payload(""), methods=methods)])
    answer = bridge.summarize("k3 bianbu4.0上如何部署llm", {"original_answer": source,
                                                                "references": bridge.references(RAW)})
    assert "方法一：Ollama\n" in answer and "方法二：llama.cpp\n" in answer and "方法三：LLM SDK\n" in answer
    assert "摘要整理暂未完成" not in answer


def test_missing_rules_do_not_invent_competition_requirement(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "EVENT_RULES_INDEX", tmp_path / "missing.sqlite")
    assert bridge.competition_choice("K3如何部署LLM", METHODS) == ""


def test_url_as_source_label_uses_document_path_not_another_long_url():
    assert bridge.document_title({"title": "https://docs.spacemit.com/llama.cpp.md",
                                  "url": "https://docs.spacemit.com/llama.cpp.md"}) == "llama.cpp 部署指南"


def test_flat_parallel_methods_rejected():
    with pytest.raises(ValueError, match="parallel_methods_require_sections"):
        bridge.validate_summary(payload("安装Ollama，再安装llama.cpp。"), "K3如何部署LLM",
                                {"original_answer": RAW, "references": bridge.references(RAW)}, False, True)


def test_sourced_local_service_address_stays_in_method_and_overview_is_rejected():
    methods = [{"name": "LLM SDK", "condition": "K3 OpenHarmony暂不支持。",
                "steps": ["安装llm-sdk。", "访问http://localhost:8050/api/health。"]}]
    evidence = {"original_answer": RAW + "K3 OpenHarmony暂不支持。llm-sdk服务：http://localhost:8050/api/health。",
                "references": bridge.references(RAW)}
    value = dict(payload(""), methods=methods)
    body, _ = bridge.validate_summary(value, "K3如何部署LLM", evidence, False, True)
    assert body.startswith("方法一：LLM SDK\nK3 OpenHarmony暂不支持。\n1、安装llm-sdk。")
    assert "localhost:8050/api/health" in body and "http://" not in body
    with pytest.raises(ValueError, match="invalid_overview"):
        bridge.validate_summary(dict(payload("K3 OpenHarmony暂不支持。"), methods=methods),
                                "K3如何部署LLM", evidence, False, True)
    evidence["original_answer"] = RAW + "llm-sdk"
    with pytest.raises(ValueError, match="unsupported_markup_or_url"):
        bridge.validate_summary(value, "K3如何部署LLM", evidence, False, True)


@pytest.mark.parametrize("question,methods", [("赛题1怎么部署LLM", METHODS),
    ("非比赛，K3部署LLM", METHODS), ("K3部署LLM", METHODS[:1])])
def test_no_unneeded_challenge_two_recommendation(question, methods):
    assert bridge.competition_choice(question, methods) == ""
