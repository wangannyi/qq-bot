import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import answer_policy
import rag_server
import website_bridge as bridge


def test_cloud_entry_link_comes_from_source_and_keeps_paragraphs():
    body = "1、访问gdriscv.com完成注册。\n\n2、登录后申请实例。"
    result = bridge.inline_navigation(body, "访问[云平台](https://gdriscv.com)。")
    assert result == "1、访问[🔗Bianbu Cloud](https://gdriscv.com)完成注册。\n\n2、登录后申请实例。"
    assert bridge.inline_navigation("访问云平台官网注册。", "地址：https://gdriscv.com/") == "访问[🔗Bianbu Cloud](https://gdriscv.com/)注册。"


@pytest.mark.parametrize("source", ["没有提供入口", "https://gdriscv.com.evil.example", "https://gdriscv.com?token=secret", "https://gdriscv.com/not-the-home", "https://user@gdriscv.com"])
def test_navigation_never_invents_or_rewrites_unverified_destination(source):
    assert bridge.inline_navigation("访问gdriscv.com。", source) == "访问gdriscv.com。"


def test_recorded_cloud_clarification_needs_no_model_or_citation(monkeypatch):
    monkeypatch.setattr(rag_server, "model_credentials", lambda: (_ for _ in ()).throw(AssertionError("No model call for a pure clarification")))
    answer = bridge.summarize("怎么申请bianbu cloud", {
        "original_answer": "请问您要申请的是 K1 还是 K3 设备？", "references": []})
    assert answer == "请补充：请问您要申请的是 K1 还是 K3 设备？"
    assert "需要人工" not in answer and "[🔗" not in answer


@pytest.mark.parametrize("text", [
    "K3 已永久免费，无需任何审核，请问你要申请哪个版本？",
    "请问能否删除所有文件后重新申请？",
    "请问能否把验证码发给我？",
    "请问您的密码是什么？",
    "请问可以执行 rm -rf 吗？",
])
def test_question_mark_does_not_bypass_evidence_or_safety(text):
    assert bridge.clarification(text, []) is None


def test_unrelated_procedure_does_not_copy_cloud_label():
    reply = bridge.format_steps(["注册登录：启用ROS2软件源。", "更新源：运行sudo apt update。"],
                                "启用ROS2软件源。更新源：运行sudo apt update。")
    assert "注册登录" not in reply
    assert reply == "1、启用ROS2软件源。\n\n2、更新源：运行sudo apt update。"


def test_clarification_is_carried_only_to_same_sender(monkeypatch):
    monkeypatch.setattr(bridge, "enabled", lambda: True)
    answer_policy._turns.clear()
    answer_policy._clarifications.clear()
    answer_policy._clarifications[("chat", "a")] = (time.monotonic(), "怎么申请bianbu cloud", "请问您要申请的是 K1 还是 K3 设备？")
    assert answer_policy.begin(session_id="chat", user_message="你好", platform="qqbot", sender_id="b") is None
    assert answer_policy.transform(session_id="chat", platform="qqbot") is None
    context = answer_policy.begin(session_id="chat", user_message="K1", platform="qqbot", sender_id="a")
    assert "原问题：怎么申请bianbu cloud" in context["context"]
    assert "用户补充：K1" in context["context"]
    answer_policy._turns.clear()
    answer_policy._clarifications.clear()


def test_steps_have_explicit_numbers_and_blank_lines():
    assert bridge.format_steps(["注册登录：填写注册信息。", "申请实例：选择镜像。", "3、远程使用：进入控制台。"] ) == (
        "1、注册登录：填写注册信息。\n\n2、申请实例：选择镜像。\n\n3、远程使用：进入控制台。")


@pytest.mark.parametrize("steps", [None, [], ["单段"], ["a"] * 5, ["a", 2], ["a", "b\nc"]])
def test_invalid_step_boundaries_are_rejected(steps):
    with pytest.raises(ValueError):
        bridge.format_steps(steps)


def test_sse_ignores_thinking_and_accepts_complete_answer():
    lines = ['data: {"state":{"type":"thinking","message":"private thinking"}}', '',
             'data: "答"', '', 'data: {"content":"案"}', '', 'data: [DONE]', '']
    assert bridge.parse_stream(iter(lines), time.monotonic() + 5) == "答案"


@pytest.mark.parametrize("lines", [
    ['data: "partial"', ''],
    ['data: {"error":"secret error"}', '', 'data: [DONE]', ''],
    ['data: [DONE]', ''],
])
def test_incomplete_empty_or_error_never_published(lines):
    with pytest.raises(ValueError):
        bridge.parse_stream(iter(lines), time.monotonic() + 5)


def test_sse_done_event_and_deadline():
    assert bridge.parse_stream(['data: answer', '', 'event: done', ''], time.monotonic() + 5) == "answer"
    with pytest.raises(TimeoutError):
        bridge.parse_stream(['data: answer'], time.monotonic() - 1)


def test_synthetic_website_null_state_and_empty_object_terminator():
    assert bridge.parse_stream(['id:1', 'data:{"content":"答案","state":null}', '',
                                'data:{}', ''], time.monotonic() + 5) == "答案"


def test_references_markdown_html_deduplicated_and_untrusted_links_excluded():
    answer = ('[说明](https://www.spacemit.com/community/a) '
        '<a href="https://www.spacemit.com/community/b?x=1&amp;y=2">文档</a> '
        '[重复](https://www.spacemit.com/community/a) '
        '[伪造](https://spacemit.com.evil.example/a) '
        '[泄露](https://www.spacemit.com/community?token=private)')
    refs = bridge.references(answer)
    assert len(refs) == 2
    assert refs[1]["url"].endswith("?x=1&y=2")


@pytest.mark.parametrize("question", ["我的API key: secret", "ssh user@10.1.2.3", "sk-secretkey", "日志/tmp/synthetic-private-log", "a" * 2001])
def test_private_question_rejected(question):
    with pytest.raises(ValueError):
        bridge.public_question(question)


def test_feature_default_off_and_event_route_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert not bridge.enabled()
    (tmp_path / "config.yaml").write_text("cie_support:\n  answer_backend: website\n")
    assert bridge.enabled()
    assert bridge.event_question("赛题2固定测试集是什么")
    monkeypatch.setattr(bridge, "ask", lambda q: {"backend": "website", "query": q})
    assert rag_server.search_spacemit_docs("K1怎么部署") == {"backend": "website", "query": "K1怎么部署"}


def test_final_output_uses_website_evidence_not_legacy_retrieval(monkeypatch):
    monkeypatch.setattr(bridge, "enabled", lambda: True)
    monkeypatch.setattr(bridge, "ask", lambda q: (_ for _ in ()).throw(AssertionError("No duplicate call")))
    monkeypatch.setattr(bridge, "final_answer", lambda q, e, s: "website answer")
    answer_policy.begin(session_id="test", user_message="K1如何部署", platform="qqbot")
    answer_policy.observe(tool_name="search_spacemit_docs", session_id="test", result={
        "backend": "website", "query": "K1如何部署", "evidence_status": "website_generated", "results": []})
    assert answer_policy.transform(session_id="test", platform="qqbot") == "website answer"
    assert answer_policy.transform(session_id="test", platform="feishu") is None


def test_synthetic_original_question_used_if_tool_rewrites_it(monkeypatch):
    seen = []
    monkeypatch.setattr(bridge, "enabled", lambda: True)
    monkeypatch.setattr(bridge, "ask", lambda q: seen.append(q) or {})
    monkeypatch.setattr(bridge, "final_answer", lambda *a: "ok")
    answer_policy.begin(session_id="rewrite", user_message="K1部署LLM", platform="qqbot")
    answer_policy.transform(session_id="rewrite", platform="qqbot")
    assert seen == ["K1部署LLM"]


def test_archive_full_answer_dedup_and_never_auto_approve(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    evidence = {"original_answer": "完整长答" * 100, "references": [], "fetched_at": "now"}
    for _ in range(2):
        bridge.archive("K1?", evidence, "短答", "session-one")
    path = tmp_path / "website-answers/candidates.sqlite"
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT original_answer,qq_summary,status FROM candidates").fetchall()
    assert rows == [(evidence["original_answer"], "短答", "pending_review")]
    assert path.stat().st_mode & 0o777 == 0o600
    drafts = list(path.parent.glob("*.md"))
    assert len(drafts) == 1 and evidence["original_answer"] in drafts[0].read_text()
    assert "待审核，未发布" in drafts[0].read_text()


def test_unavailable_summary_still_archives_original_without_secret(monkeypatch):
    monkeypatch.setattr(bridge, "summarize", lambda *a: (_ for _ in ()).throw(ValueError("private")))
    saved = []
    monkeypatch.setattr(bridge, "archive", lambda *a: saved.append(a))
    reply = bridge.final_answer("question", {"original_answer": "full"}, "test")
    assert reply == bridge.SUMMARY_UNAVAILABLE and "private" not in reply
    assert saved[0][1]["original_answer"] == "full"
