"""Two-turn QQ regression: deployment question -> system/version reply."""
import re
import time

import answer_policy as policy
import pytest
import website_bridge as bridge
from test_summary_validation import EVIDENCE, URL, fake_model, payload


@pytest.fixture(autouse=True)
def clean_state():
    policy._turns.clear()
    policy._clarifications.clear()
    yield
    policy._turns.clear()
    policy._clarifications.clear()


def first_turn(monkeypatch, sender="alice", session="group-a"):
    monkeypatch.setattr(bridge, "enabled", lambda: True)
    fake_model(monkeypatch, [payload("K3的Bianbu系统可通过llama.cpp部署。请提供当前系统及版本。")])
    monkeypatch.setattr(bridge, "archive", lambda *args: None)
    evidence = dict(EVIDENCE, backend="website", query="k3上如何部署llm",
                    results=[], evidence_status="website_generated")
    policy.begin(session_id=session, user_message="k3上如何部署llm", platform="qqbot", sender_id=sender)
    policy.observe(session_id=session, tool_name="search_spacemit_docs", result=evidence)
    return policy.transform(session_id=session, platform="qqbot")


@pytest.mark.parametrize("reply", ["bianbu 4", "bianbu4", "Bianbu 4.0", "4", "4.0"])
def test_generated_followup_preserves_original_intent(monkeypatch, reply):
    answer = first_turn(monkeypatch)
    # The public reply uses QQ Markdown; the follow-up contract must survive
    # rendering instead of requiring the pre-render Chinese numbering.
    readable = re.sub(r"[*]", "", answer)
    assert re.match(r"1[.、]", readable) and "待补充：" in readable
    assert "请提供当前系统及版本" not in answer  # replaced by one tracked prompt
    assert bridge.SYSTEM_FOLLOW_UP in answer
    context = policy.begin(session_id="group-a", user_message=reply, platform="qqbot", sender_id="alice")
    assert "原问题：k3上如何部署llm" in context["context"]
    assert f"用户补充：{reply}" in context["context"]
    sent = []
    monkeypatch.setattr(bridge, "ask", lambda q: sent.append(q) or {})
    monkeypatch.setattr(bridge, "final_answer", lambda q, e, s: "部署步骤")
    assert policy.transform(session_id="group-a", platform="qqbot") == "部署步骤"
    assert len(sent) == 1 and "k3上如何部署llm" in sent[0] and reply in sent[0]


def test_other_member_cannot_consume_followup(monkeypatch):
    first_turn(monkeypatch)
    assert policy.begin(session_id="group-a", user_message="bianbu 4", platform="qqbot", sender_id="bob") is None
    assert policy.begin(session_id="group-b", user_message="bianbu 4", platform="qqbot", sender_id="alice") is None
    assert policy.begin(session_id="group-a", user_message="bianbu 4", platform="qqbot", sender_id="alice") is not None


@pytest.mark.parametrize("reply", ["bianbu4是什么", "bianbu 4怎么重置密码", "怎么申请cloud", "bianbu 4; rm -rf /"])
def test_new_topic_not_forced_into_previous_deployment(monkeypatch, reply):
    first_turn(monkeypatch)
    assert policy.begin(session_id="group-a", user_message=reply, platform="qqbot", sender_id="alice") is None


def test_expired_and_unidentified_senders_do_not_inherit(monkeypatch):
    first_turn(monkeypatch)
    key = ("group-a", "alice")
    _, q, prompt = policy._clarifications[key]
    policy._clarifications[key] = (time.monotonic()-601, q, prompt)
    assert policy.begin(session_id="group-a", user_message="bianbu 4", platform="qqbot", sender_id="alice") is None
    first_turn(monkeypatch, sender="")
    assert ("group-a", "") not in policy._clarifications


@pytest.mark.parametrize("title,expected", [
    ("llama.cpp.md", "llama.cpp 部署指南"),
    ("cloud_user_guide.md", "Bianbu Cloud 使用指南"),
    ("llmsdk.md", "LLM SDK"),
    ("02_ROS2使用说明.md", "ROS2使用说明"),
    ("Bianbu 4.0 ROOTFS 制作", "Bianbu 4.0 ROOTFS 制作"),
])
def test_specific_document_names(title, expected):
    assert bridge.document_title({"title": title, "url": URL}) == expected


def test_generic_title_uses_actual_source_path():
    assert bridge.document_title({"title": "查看官网说明", "url": URL}) == "llama.cpp 部署指南"


def test_numbering_does_not_break_versions_packages_or_inline_urls():
    result = bridge.layout_answer("在Bianbu 4.0上使用llama.cpp。请提供当前系统及版本。", True)
    assert result == "1、在Bianbu 4.0上使用llama.cpp。\n\n待补充：" + bridge.SYSTEM_FOLLOW_UP


def test_layout_does_not_remove_a_version_requirement():
    body = "K3使用llama.cpp。请确认系统版本满足文档要求后再安装。"
    assert "请确认系统版本满足文档要求后再安装。" in bridge.layout_answer(body, True)


def test_abbreviated_download_command_retried_as_prose(monkeypatch):
    fake_model(monkeypatch, [
        dict(payload(""), steps=["安装llama.cpp。", "wget Qwen3.gguf到主目录。"]),
        dict(payload(""), steps=["安装llama.cpp。", "下载文档指定的Qwen3量化模型。"]),
    ])
    answer = bridge.summarize("K3 Bianbu 4如何部署llm", dict(EVIDENCE))
    assert "wget" not in answer
    assert "2、下载文档指定的Qwen3量化模型。" in answer
