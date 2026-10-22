"""Regression for the real QQ <@openid> envelope seen in production."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import answer_policy
import daily_summary
import faq_sync
import support_runtime as runtime
from qq_input import question_text
from retrieval import create_index
from support_sources import overview_product, search_sources

MENTION = "<@ABCDEF0123456789ABCDEF0123456789>"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "enabled", lambda: True)
    runtime._contexts.clear()
    runtime._turns.clear()
    yield
    runtime._contexts.clear()
    runtime._turns.clear()


@pytest.mark.parametrize(
    "message,expected",
    [
        (MENTION + " 你好", "你好"),
        (MENTION + "你是谁", "你是谁"),
        (MENTION + " 介绍k1平台", "介绍k1平台"),
        ("<@!123456>\u00a0K3如何部署LLM", "K3如何部署LLM"),
        ("&lt;@ABC123&gt; 你好", "你好"),
        (MENTION + " " + MENTION + " 你好", "你好"),
        ("你好 " + MENTION, "你好"),
        ("@spacemit-bot 介绍K1", "介绍K1"),
        (MENTION, ""),
    ],
)
def test_wire_addressing_is_not_question_content(message, expected):
    assert question_text(message) == expected


@pytest.mark.parametrize(
    "question",
    [
        "ssh user@host.example.com失败怎么办",
        "邮箱是 user@example.com",
        "解释 `<@123>` 这种格式",
        "[报错] pip安装失败",
        "@another-user 你好",
        "python3 -m venv my_env\nsource my_env/bin/activate",
    ],
)
def test_body_code_and_addresses_are_preserved(question):
    assert question_text(question) == question


@pytest.mark.parametrize(
    "body,expected",
    [
        ("你好", "你好，我可以帮你查"),
        ("你是谁", "我是 spacemit-bot"),
    ],
)
def test_greeting_and_identity_use_real_envelope_without_retrieval(
    body, expected, monkeypatch
):
    monkeypatch.setattr(
        runtime,
        "answer_question",
        lambda *a, **k: pytest.fail("operational input searched"),
    )
    answer_policy.begin(
        session_id="qq-group-user",
        user_message=MENTION + " " + body,
        platform="qqbot",
        sender_id="user-a",
    )
    result = runtime.execute(
        {},
        lambda r: pytest.fail("generic model called"),
        session_id="qq-group-user",
        platform="qqbot",
    )
    assert result.choices[0].message.content.startswith(expected)
    assert answer_policy.transform(
        session_id="qq-group-user", platform="qqbot"
    ).startswith(expected)


def test_technical_question_and_followup_keep_clean_context(monkeypatch):
    received = []

    def answer(query, **kwargs):
        received.append(query)
        return {
            "text": "待补充：你用什么系统？",
            "answer": {"status": "clarify", "follow_up": "你用什么系统？"},
        }

    monkeypatch.setattr(runtime, "answer_question", answer)
    for message in [MENTION + " K3如何部署LLM", MENTION + " Bianbu 4.0"]:
        runtime.begin("group-user", message, "qqbot", "user-a")
        runtime.execute(
            {},
            lambda r: pytest.fail("generic model called"),
            session_id="group-user",
            platform="qqbot",
        )
        runtime.finish("group-user", "qqbot")
    assert all("@" not in query and "ABCDEF" not in query for query in received)
    assert "K3如何部署LLM" in received[-1] and "Bianbu 4.0" in received[-1]


def test_daily_and_faq_match_the_clean_archived_question():
    raw = MENTION + " 介绍k1平台"
    assert daily_summary.sanitize(raw) == "介绍k1平台"
    assert faq_sync.normalize_question(raw) == faq_sync.normalize_question("介绍k1平台")
    assert not daily_summary.is_support_question(
        daily_summary.sanitize(MENTION + " 你好")
    )


@pytest.mark.parametrize("board", ["k1", "k3"])
def test_product_overview_uses_current_primary_document_not_driver_chapter(
    tmp_path, board
):
    path = f"hardware/key_stone/{board}/{board}_docs/root_overview.md"

    def doc(name, path, title, body):
        return dict(
            id=name,
            path=path,
            title=title,
            body=body,
            source_kind="official",
            page_url="https://www.spacemit.com/community/document/info?nodepath="
            + path,
            source_url="",
        )

    index = tmp_path / "docs.sqlite"
    create_index(
        index,
        [
            doc(
                "overview",
                path,
                "产品简介",
                f"# {board.upper()} 产品简介\n这里是当前芯片的硬件产品概述。",
            ),
            doc(
                "driver",
                f"software/{board}/wifi.md",
                f"{board} 平台介绍",
                "WIFI 平台介绍 " * 100,
            ),
        ],
        datetime.now(timezone.utc).isoformat(),
    )
    result = search_sources(f"介绍{board}平台", index=index)
    assert len(result["sources"]) == 1 and result["sources"][0]["path"] == path
    assert "当前芯片" in result["sources"][0]["content"]


@pytest.mark.parametrize(
    "question",
    ["介绍K1的WIFI驱动", "介绍K3平台功耗调试", "K1怎么部署LLM", "K1和K3的区别"],
)
def test_specific_questions_do_not_use_generic_overview_route(question):
    assert not overview_product(question)
