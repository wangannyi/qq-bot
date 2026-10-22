"""Regression cases for the September 9 K3 summary rejection."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import rag_server
import website_bridge as bridge

URL = "https://www.spacemit.com/community/document/info?nodepath=ai/compute_stack/ai_compute_stack/llama.cpp.md&lang=zh"
EVIDENCE = {
    "original_answer": "K3 Bianbu LXQT/GNOME环境原生支持llama.cpp部署。K1有独立教程。" + f"[llama.cpp.md]({URL})",
    "references": [{"id": 0, "title": "llama.cpp.md", "url": URL}],
}


def fake_model(monkeypatch, values):
    calls = []
    class Client:
        def __init__(self, **kwargs):
            kwargs["http_client"].close()
            self.chat = SimpleNamespace(completions=self)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def create(self, **kwargs):
            calls.append(json.loads(json.dumps(kwargs["messages"])))
            value = values[min(len(calls) - 1, len(values) - 1)]
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(value, ensure_ascii=False)), finish_reason="stop")])
    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setattr(rag_server, "model_credentials", lambda: ({
        "model": {"provider": "custom:test", "default": "test"},
        "custom_providers": [{"name": "test", "base_url": "https://example.invalid"}],
    }, "test-key"))
    return calls


def payload(body, source_id=0):
    return {"body": body, "steps": [], "status": "answered", "source_id": source_id}


def test_k3_question_accepts_k3_without_retry(monkeypatch):
    body = "K3在Bianbu LXQT/GNOME环境下可使用llama.cpp部署。请问你当前使用的系统及版本是什么？"
    calls = fake_model(monkeypatch, [payload(body)])
    answer = bridge.summarize("k3上如何部署llm", EVIDENCE)
    assert answer.startswith("1、K3在Bianbu LXQT/GNOME环境下可使用llama.cpp部署。")
    assert "\n\n待补充：" + bridge.SYSTEM_FOLLOW_UP in answer
    assert URL in answer
    assert len(calls) == 1


def test_empty_methods_array_keeps_a_direct_body_without_retry(monkeypatch):
    """``methods: []`` is the normal JSON shape for a non-method answer."""
    calls = fake_model(monkeypatch, [dict(payload("我是进迭时空社区的智能助手，可以协助查询产品技术资料。"),
                                      methods=[])])
    answer = bridge.summarize("你是谁", EVIDENCE)
    assert answer.startswith("我是进迭时空社区的智能助手")
    assert "摘要整理暂未完成" not in answer
    assert len(calls) == 1


def test_summary_strips_repeated_bullet_and_reference_heading_debris():
    value = dict(payload("• • • 参考文档：\n• K3在Bianbu环境下可使用llama.cpp部署。"), methods=[])
    body, source_id = bridge.validate_summary(value, "K3如何部署LLM", EVIDENCE, False, False)
    assert source_id == 0
    assert body == "K3在Bianbu环境下可使用llama.cpp部署。"
    assert "•" not in body and "参考文档" not in body


@pytest.mark.parametrize("question,wrong", [("K1如何部署LLM", "K3"), ("K3如何部署LLM", "K1")])
def test_cross_board_answer_is_rejected(monkeypatch, question, wrong):
    fake_model(monkeypatch, [payload(f"{wrong}在Bianbu下使用llama.cpp部署。")])
    answer = bridge.summarize(question, EVIDENCE)
    assert "llama.cpp部署" not in answer


def test_retry_has_specific_reason_and_previous_candidate(monkeypatch):
    good = "K3在Bianbu下可使用llama.cpp部署。你当前使用什么系统版本？"
    calls = fake_model(monkeypatch, [payload("答" * 241), payload(good)])
    assert bridge.summarize("K3如何部署LLM", EVIDENCE).startswith("1、K3在Bianbu下可使用llama.cpp部署。")
    assert "body_too_long" in calls[1][-1]["content"]
    assert calls[1][-2]["role"] == "assistant"


@pytest.mark.parametrize("value", [
    payload("K3使用llama.cpp。", 99), payload("K3使用llama.cpp。", True),
    payload("K3使用llama.cpp。", None), payload("`sudo do-something`"),
    [], {"body": 123, "status": "answered", "source_id": 0},
])
def test_invalid_payload_remains_blocked_and_observable(monkeypatch, caplog, value):
    fake_model(monkeypatch, [value])
    answer = bridge.summarize("K3如何部署LLM", EVIDENCE)
    assert answer.startswith("摘要整理暂未完成")
    assert "需要人工确认" not in answer
    assert "summary_rejected" in caplog.text
    assert "test-key" not in caplog.text and URL not in caplog.text


def test_real_missing_evidence_stays_human_required(monkeypatch):
    fake_model(monkeypatch, [{"body": "需要人工确认：原答未明确适用系统。", "steps": [],
                              "status": "needs_human", "source_id": None}])
    assert bridge.summarize("K3如何部署LLM", EVIDENCE).startswith("需要人工确认：")


def test_two_system_routes_are_not_published_for_broad_question(monkeypatch):
    fake_model(monkeypatch, [payload("K3可以在Bianbu或OpenHarmony上部署。")])
    assert not bridge.summarize("K3如何部署LLM", EVIDENCE).startswith("K3可以")


def test_plain_markup_is_normalized_without_losing_command(monkeypatch):
    fake_model(monkeypatch, [{"body": "", "status": "answered", "source_id": 0,
        "steps": ["**更新软件源**，执行`sudo apt update`。", "安装ROS2组件。"]}])
    evidence = dict(EVIDENCE, original_answer="更新软件源，执行sudo apt update。安装ROS2组件。" + f"[文档]({URL})")
    answer = bridge.summarize("K1如何安装ROS2", evidence)
    assert "1、更新软件源，执行sudo apt update。\n\n2、安装ROS2组件。" in answer
    assert "`" not in answer and "**" not in answer


def test_summary_failure_is_not_counted_as_handled_in_daily_report():
    from daily_summary import STATUS_BOT_ISSUE, classify_response
    status, reason = classify_response([bridge.SUMMARY_UNAVAILABLE])
    assert status == STATUS_BOT_ISSUE
    assert "摘要处理失败" in reason


def test_supported_negative_system_caveat_is_not_a_second_route(monkeypatch):
    body = "K3可在Bianbu下使用llama.cpp部署，Buildroot暂不支持。请确认系统版本。"
    fake_model(monkeypatch, [payload(body)])
    evidence = dict(EVIDENCE, original_answer=EVIDENCE["original_answer"] + "Buildroot暂不支持。")
    assert bridge.summarize("K3如何部署LLM", evidence).startswith("1、K3可在Bianbu下使用llama.cpp部署，Buildroot暂不支持。")
