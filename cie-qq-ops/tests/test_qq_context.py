"""Replay quoted QQ clarifications and latest-user environment corrections."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from qq_input import parse_question, question_text

MENTION = "<@ABCDEF0123456789ABCDEF0123456789>"


def reply(body, quote):
    return f"[Quoted message]:\n{quote}\n\n{MENTION} {body}"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "enabled", lambda: True)
    runtime._contexts.clear()
    runtime._turns.clear()
    yield
    runtime._contexts.clear()
    runtime._turns.clear()


@pytest.mark.parametrize("body", ["bianbu 3.0", "V2.3.5", "你好", "你是谁", "介绍K1"])
def test_quoted_example_is_separate_from_author_body(body):
    quote = "待补充：K1 当前使用什么系统及版本（例如 Bianbu 4.0）？"
    parsed = parse_question(reply(body, quote))
    assert parsed.text == body and parsed.quoted == quote
    assert question_text(reply(body, quote)) == body


def test_quote_and_body_keep_multiline_code_and_paragraphs():
    quote = "先看看错误。\n\n旧示例：Bianbu 4.0\n\n第二段。"
    body = "这个报错怎么办？\n\n```\npython3 -m venv my_env\n\nsource my_env/bin/activate\n```"
    assert parse_question(reply(body, quote)).text == body
    assert parse_question(reply(body, quote)).quoted == quote


def test_quote_only_is_not_a_user_environment_statement():
    assert question_text("[Quoted message]:\nK3 Bianbu 4.0") == ""


def test_complete_reported_conversation_keeps_task_and_latest_version(monkeypatch):
    queries = []
    answers = iter(
        [
            {"status": "clarify", "follow_up": "K1 当前使用什么系统及版本？"},
            {"status": "partial", "follow_up": "资料未覆盖此版本的部署步骤。"},
            {"status": "answered", "follow_up": ""},
            {"status": "answered", "follow_up": ""},
        ]
    )

    def answer(query, **kwargs):
        queries.append(query)
        return {"text": "fixture", "answer": next(answers)}

    monkeypatch.setattr(runtime, "answer_question", answer)
    messages = [
        MENTION + " K1上怎么部署llm",
        reply("bianbu 3.0", "待补充：K1 当前使用什么系统及版本（例如 Bianbu 4.0）？"),
        reply("V2.3.5", "你提到 Bianbu 3.0 和 4.0，请确认完整版本（例如 4.0.1）？"),
        reply("V2.3.5", "结论：仅覆盖 Bianbu V2.3 系列，未涉及 3.0。"),
    ]
    for message in messages:
        runtime.begin("qq-user", message, "qqbot", "alice")
        runtime.execute(
            {},
            lambda r: pytest.fail("generic agent called"),
            session_id="qq-user",
            platform="qqbot",
        )
        runtime.finish("qq-user", "qqbot")
    assert all("K1上怎么部署llm" in query for query in queries)
    assert "bianbu 3.0" in queries[1]
    for query in queries[2:]:
        assert "bianbu 2.3.5" in query
        assert not any(token in query for token in ["3.0", "4.0", "引用", "<@"])
    assert queries[2] == queries[3]
    assert runtime._contexts[("qq-user", "alice")].environment["version"] == "2.3.5"


@pytest.mark.parametrize(
    "correction",
    ["V2.3.5", "2.3.5", "版本是V2.3.5", "系统版本为V2.3.5", "实际是V2.3.5"],
)
def test_version_correction_rewrites_old_version_in_original_task(correction):
    runtime.prepare("K1 Bianbu 3.0怎么部署LLM", "a", "alice")
    query, env = runtime.prepare(correction, "a", "alice")
    assert env["version"] == "2.3.5"
    assert "部署LLM" in query and "Bianbu 2.3.5" in query and "3.0" not in query


def test_switch_system_clears_old_version_and_updates_task():
    runtime.prepare("K1 Bianbu 3.0怎么部署LLM", "a", "alice")
    query, env = runtime.prepare("Buildroot", "a", "alice")
    assert env == {"board": "K1", "system": "Buildroot"}
    assert "部署LLM" in query and "Bianbu" not in query and "3.0" not in query


def test_switch_board_does_not_carry_os_from_original_task():
    runtime.prepare("K1 Bianbu 3.0怎么部署LLM", "a", "alice")
    query, env = runtime.prepare("K3", "a", "alice")
    assert env == {"board": "K3"}
    assert "部署LLM" in query and "K1" not in query and "Bianbu" not in query


def test_new_topic_ends_previous_task_but_keeps_explicit_environment():
    runtime.prepare("K1 Bianbu 3.0怎么部署LLM", "a", "alice")
    runtime.prepare("Python的venv怎么用", "a", "alice")
    query, env = runtime.prepare("3.11.9", "a", "alice")
    assert "venv" in query and "LLM" not in query
    assert env["version"] == "3.0"  # Python version is not the OS version.
    assert "3.11.9" in query


def test_explicit_package_version_followup_cannot_overwrite_os(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "answer_question",
        lambda *a, **k: {
            "text": "待补充：llama.cpp 的版本是多少？",
            "answer": {"status": "clarify", "follow_up": "llama.cpp 的版本是多少？"},
        },
    )
    runtime.begin("a", "K1 Bianbu 3.0怎么部署LLM", "qqbot", "alice")
    runtime.execute({}, None, session_id="a", platform="qqbot")
    query, env = runtime.prepare("2.3.5", "a", "alice")
    assert env["version"] == "3.0" and "本次补充：2.3.5" in query


def test_greeting_does_not_replace_pending_technical_task():
    runtime.prepare("K1上怎么部署LLM", "a", "alice")
    runtime.prepare("你好", "a", "alice")
    query, env = runtime.prepare("Bianbu 2.3.5", "a", "alice")
    assert "部署LLM" in query and env["version"] == "2.3.5"


def test_quoted_error_is_reference_only_and_does_not_supply_environment():
    runtime.begin(
        "a",
        reply("这个报错怎么解决", "K3 Bianbu 4.0\nRuntimeError: tensor mismatch"),
        "qqbot",
        "alice",
    )
    turn = runtime._turns["a"]
    assert turn["environment"] == {}
    assert "RuntimeError: tensor mismatch" in turn["query"]
    assert runtime._environment(turn["query"]) == {}
    assert "RuntimeError" not in runtime._contexts[("a", "alice")].question


def test_version_correction_needs_same_sender_and_session():
    runtime.prepare("K1 Bianbu 3.0怎么部署LLM", "a", "alice")
    for session, sender in [("a", "bob"), ("b", "alice"), ("a", "")]:
        query, env = runtime.prepare("V2.3.5", session, sender)
        assert env == {} and "LLM" not in query
