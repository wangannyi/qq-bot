"""Daily reporting counts user issues, not every QQ clarification turn."""

import hashlib
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import daily_summary as daily
import faq_sync
from summary_questions import collect_questions, same_problem

START = datetime(2026, 10, 9, 20, tzinfo=daily.TZ)
END = START + timedelta(days=1)


def cases(messages):
    rows = []
    for index, item in enumerate(messages):
        role, text = item[:2]
        user = item[2] if len(item) > 2 else "alice"
        rows.append(
            (
                "session-" + user,
                role,
                START.timestamp() + index * 10,
                text,
                "group",
                user,
            )
        )
    return collect_questions(rows, START.timestamp())


def test_later_valid_reply_replaces_earlier_ai_failure_and_duplicate():
    questions = cases(
        [
            ("user", "介绍k1平台"),
            ("assistant", "资料不足：未取得依据"),
            ("user", "介绍K3平台"),
            ("assistant", "结论：K3 产品简介。"),
            ("user", "介绍 K1 平台？"),
            ("assistant", "结论：K1 产品简介。"),
        ]
    )
    assert len(questions) == 2 and all(
        q.status == daily.STATUS_HANDLED for q in questions
    )
    assert "需要人工处理的问题：0" in daily.build_summary(questions, START, END)


def test_version_followups_form_one_question_with_final_answer():
    questions = cases(
        [
            ("user", "K3怎么部署LLM"),
            ("assistant", "待补充：K3 当前使用什么系统及版本？"),
            ("user", "4.0.1"),
            ("assistant", "待补充：K3 当前使用什么系统及版本？"),
            ("user", "[Quoted message]:\n示例 Bianbu 3.0\n\n<@ABC123> Bianbu 4.0.1"),
            ("assistant", "结论：K3 Bianbu 4.0.1 可使用 llama.cpp 部署 LLM。"),
        ]
    )
    assert len(questions) == 1
    question = questions[0]
    assert question.text == "K3怎么部署LLM" and question.status == daily.STATUS_HANDLED
    assert question.candidate_question == "Bianbu 4.0.1"
    assert dict(question.environment)["version"] == "4.0.1"
    assert "、[20:00] Bianbu" not in daily.build_summary(questions, START, END)


def test_off_topic_release_note_is_excluded_instead_of_counted_as_solved():
    questions = cases(
        [
            ("user", "K1怎么部署LLM"),
            ("assistant", "待补充：系统及版本？"),
            ("user", "Bianbu 3.0"),
            ("assistant", "待补充：请确认完整版本？"),
            ("user", "V2.3.5"),
            ("assistant", "结论：Bianbu 2.3 更新说明没有涉及 3.0。"),
        ]
    )
    assert len(questions) == 1 and questions[0].status == daily.STATUS_BOT_ISSUE
    assert dict(questions[0].environment)["version"] == "2.3.5"
    summary = daily.build_summary(questions, START, END)
    assert "问题总数：0" in summary and "V2.3.5" not in summary


def test_camera_reference_misjudgment_does_not_become_a_report_entry():
    questions = cases(
        [
            ("user", "K1如何创建Python虚拟环境"),
            (
                "assistant",
                "结论：用 venv 创建。\n[Python使用USB相机](https://example.com/camera)",
            ),
        ]
    )
    assert questions[0].status == daily.STATUS_BOT_ISSUE
    assert "K1如何创建" not in daily.build_summary(questions, START, END)


def test_real_human_request_and_waiting_are_distinct_from_ai_failures():
    questions = cases(
        [
            ("user", "赛题指定哪个隐藏测试集？"),
            ("assistant", "需要人工确认：请联系赛事组织方。"),
            ("user", "K3如何部署LLM"),
            ("assistant", "待补充：系统及版本？"),
            ("user", "介绍K1平台"),
            ("assistant", "查询超时：稍后重试。"),
        ]
    )
    summary = daily.build_summary(questions, START, END)
    assert "问题总数：2" in summary and "需要人工处理的问题：1" in summary
    assert "待补充信息的问题：1" in summary and "介绍K1" not in summary


def test_followups_do_not_attach_to_another_user():
    questions = cases(
        [
            ("user", "K3怎么部署LLM", "alice"),
            ("assistant", "待补充：系统及版本？", "alice"),
            ("user", "Bianbu 4.0.1", "bob"),
            ("assistant", "结论：版本说明。", "bob"),
        ]
    )
    assert len(questions) == 1 and questions[0].status == daily.STATUS_WAITING
    assert "version" not in dict(questions[0].environment)


def test_another_users_success_does_not_close_real_open_issue():
    questions = cases(
        [
            ("user", "K1摄像头如何启用", "alice"),
            ("assistant", "需要人工确认：请联系技术支持。", "alice"),
            ("user", "K1摄像头如何启用", "bob"),
            ("assistant", "结论：打开摄像头。", "bob"),
        ]
    )
    assert len(questions) == 1 and questions[0].status == daily.STATUS_HUMAN_REQUIRED


def test_new_technical_problem_during_clarification_is_not_swallowed():
    questions = cases(
        [
            ("user", "K3怎么部署LLM"),
            ("assistant", "待补充：系统及版本？"),
            ("user", "USB摄像头黑屏"),
            ("assistant", "结论：检查摄像头设备节点。"),
        ]
    )
    assert len(questions) == 2


def test_same_users_later_success_closes_their_issue_after_cross_user_dedup():
    questions = cases(
        [
            ("user", "K1摄像头如何启用", "alice"),
            ("assistant", "需要人工确认：请联系技术支持。", "alice"),
            ("user", "K1摄像头如何启用", "bob"),
            ("assistant", "结论：打开摄像头。", "bob"),
            ("user", "介绍K3平台", "alice"),
            ("assistant", "结论：K3 产品简介。", "alice"),
            ("user", "K1摄像头如何启用", "alice"),
            ("assistant", "结论：打开摄像头。", "alice"),
        ]
    )
    assert len(questions) == 2
    assert all(question.status == daily.STATUS_HANDLED for question in questions)


def test_substantive_followup_is_checked_against_its_own_requested_action():
    questions = cases(
        [
            ("user", "K3怎么部署LLM"),
            ("assistant", "结论：K3 使用 llama.cpp 部署 LLM。"),
            ("user", "那怎么配置pip源"),
            ("assistant", "结论：使用 pip config 配置额外源。"),
        ]
    )
    assert len(questions) == 1 and questions[0].status == daily.STATUS_HANDLED


def test_short_negative_feedback_reopens_original_issue_not_new_question():
    questions = cases(
        [
            ("user", "K1摄像头如何启用"),
            ("assistant", "结论：打开摄像头。"),
            ("user", "不行"),
        ]
    )
    assert len(questions) == 1 and questions[0].status == daily.STATUS_HUMAN_REQUIRED


@pytest.mark.parametrize(
    "left,right,equal",
    [
        ("K3怎么部署大模型", "K3 如何部署 LLM？", True),
        ("Ｋ１ 如何刷机？", "K1如何刷机", True),
        ("K1如何部署LLM", "K3如何部署LLM", False),
        ("K1 Bianbu 3.0如何部署LLM", "K1 Bianbu 4.0如何部署LLM", False),
        ("K1 Bianbu 2.3.5如何部署LLM", "K1 Bianbu 23.5如何部署LLM", False),
        ("K1怎么创建Python虚拟环境", "K1怎么删除Python虚拟环境", False),
        ("K1怎么开启USB摄像头", "K1怎么关闭USB摄像头", False),
        ("K1怎么部署Qwen2.5", "K1怎么部署Qwen3", False),
    ],
)
def test_conservative_dedup_keeps_platform_and_version_differences(left, right, equal):
    assert same_problem(left, right) == equal


def test_window_lookback_links_supplement_without_counting_unrelated_old_question(
    tmp_path,
):
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            "CREATE TABLE sessions(id,source,chat_type,chat_id,user_id); CREATE TABLE messages(session_id,role,timestamp,content,active); INSERT INTO sessions VALUES('s','qqbot','group','g','u');"
        )
        conn.executemany(
            "INSERT INTO messages VALUES('s',?,?,?,1)",
            [
                ("user", START.timestamp() - 30, "K3怎么部署LLM"),
                ("assistant", START.timestamp() - 20, "待补充：系统及版本？"),
                ("user", START.timestamp() + 10, "Bianbu 4.0.1"),
                (
                    "assistant",
                    START.timestamp() + 20,
                    "结论：K3 可用 llama.cpp 部署 LLM。",
                ),
            ],
        )
    questions = daily.load_questions(db, START, END, groups_only=True)
    assert len(questions) == 1 and questions[0].text == "K3怎么部署LLM"
    assert questions[0].timestamp >= START.timestamp()


def test_context_expires_and_orphan_version_is_not_a_question():
    rows = [
        ("s", "user", START.timestamp(), "K3怎么部署LLM", "g", "u"),
        ("s", "assistant", START.timestamp() + 1, "待补充：系统及版本？", "g", "u"),
        ("s", "user", START.timestamp() + 1801, "Bianbu 4.0.1", "g", "u"),
    ]
    questions = collect_questions(rows, START.timestamp())
    assert len(questions) == 1 and questions[0].status == daily.STATUS_WAITING


def test_faq_uses_same_session_final_supplement_without_renaming_original_question():
    question = faq_sync.HandledQuestion(
        START.timestamp(), "K3怎么部署LLM", "s", "Bianbu 4.0.1", START.timestamp() + 20
    )

    def candidate(
        scope, query="K3怎么部署LLM\n用户明确提供的环境：K3，Bianbu 4.0.1", age=25
    ):
        return faq_sync.Candidate(
            fingerprint=scope + str(age),
            question="Bianbu 4.0.1",
            original_answer="LLM 部署原文。",
            session_hash=hashlib.sha256(scope.encode()).hexdigest(),
            source_query=query,
            fetched_at=(START + timedelta(seconds=age)).isoformat(),
        )

    correct = candidate("s")
    assert (
        faq_sync._find_candidate(
            question,
            [
                candidate("other"),
                candidate("s", "K1怎么部署LLM"),
                candidate("s", age=800),
                correct,
            ],
        )
        is correct
    )
    assert faq_sync._find_candidate(question, [candidate("other")]) is None
    assert question.text == "K3怎么部署LLM"
