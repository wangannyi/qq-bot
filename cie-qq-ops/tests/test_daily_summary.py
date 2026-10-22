from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_summary import (
    STATUS_HANDLED,
    STATUS_HUMAN_REQUIRED,
    Question,
    build_summary,
    is_support_question,
    load_questions,
    sanitize,
)

TZ = ZoneInfo("Asia/Shanghai")


class DailySummaryTests(unittest.TestCase):
    def test_summary_trigger_is_not_counted_as_question(self):
        self.assertFalse(is_support_question("今日问题汇总"))
        self.assertFalse(is_support_question("问题汇总？"))
        self.assertFalse(is_support_question("今日问汇总题"))
        self.assertFalse(is_support_question("[System: continue previous response]"))

    def test_sanitize_hides_api_keys(self):
        self.assertEqual(sanitize("key: sk-abcdefghijk123456"), "key: [API_KEY已隐藏]")

    def test_load_questions_filters_non_questions_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, source TEXT, chat_type TEXT
                );
                CREATE TABLE messages (
                    session_id TEXT, role TEXT, content TEXT,
                    timestamp REAL, active INTEGER
                );
                INSERT INTO sessions VALUES
                    ('qq', 'qqbot', 'group'),
                    ('dm', 'qqbot', 'dm'),
                    ('cli', 'cli', 'dm');
                """
            )
            now = datetime.now(TZ)
            rows = [
                ("qq", "user", "你好", now.timestamp(), 1),
                ("qq", "user", "K1 如何运行 llama-bench？", now.timestamp(), 1),
                ("qq", "user", "K1 如何运行 llama-bench？", now.timestamp(), 1),
                ("dm", "user", "私聊中的问题", now.timestamp(), 1),
                ("cli", "user", "不应出现", now.timestamp(), 1),
            ]
            connection.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", rows)
            connection.commit()
            connection.close()

            questions = load_questions(
                path,
                now - timedelta(hours=1),
                now + timedelta(hours=1),
                groups_only=True,
            )
            self.assertEqual([item.text for item in questions], ["K1 如何运行 llama-bench？"])

    def test_summary_contains_topic_counts(self):
        now = datetime.now(TZ)

        summary = build_summary(
            [Question(now.timestamp(), "K1 的 prefill 性能怎么测试？")],
            now - timedelta(hours=24),
            now,
        )
        self.assertIn("问题总数：1", summary)
        self.assertIn("性能测试 1", summary)
        self.assertIn("已处理问题：1", summary)
        self.assertIn("需要人工处理的问题：0", summary)

    def test_load_questions_classifies_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, source TEXT, chat_type TEXT
                );
                CREATE TABLE messages (
                    session_id TEXT, role TEXT, content TEXT,
                    timestamp REAL, active INTEGER
                );
                INSERT INTO sessions VALUES ('qq', 'qqbot', 'group');
                """
            )
            now = datetime.now(TZ)
            rows = [
                ("qq", "user", "K1 如何运行 llama-bench？", now.timestamp(), 1),
                ("qq", "assistant", "请按官方教程运行。", now.timestamp() + 1, 1),
                ("qq", "user", "赛题 2 指定哪个测试集？", now.timestamp() + 2, 1),
                (
                    "qq",
                    "assistant",
                    "**需要人工确认**\n已确认：规则要求固定测试集。\n待确认：数据集名称。",
                    now.timestamp() + 3,
                    1,
                ),
            ]
            connection.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", rows)
            connection.commit()
            connection.close()

            questions = load_questions(
                path,
                now - timedelta(hours=1),
                now + timedelta(hours=1),
                groups_only=True,
            )

            self.assertEqual(questions[0].status, STATUS_HANDLED)
            self.assertEqual(questions[1].status, STATUS_HUMAN_REQUIRED)
            self.assertIn("人工确认", questions[1].reason)

    def test_summary_separates_handled_and_human_required(self):
        now = datetime.now(TZ)
        summary = build_summary(
            [
                Question(now.timestamp(), "K1 怎么刷机？", STATUS_HANDLED),
                Question(
                    now.timestamp() + 1,
                    "比赛指定哪个测试集？",
                    STATUS_HUMAN_REQUIRED,
                    "官方资料不足，机器人已标记需要人工确认",
                ),
            ],
            now - timedelta(hours=24),
            now,
        )

        self.assertIn("已处理问题：1", summary)
        self.assertIn("K1 怎么刷机？", summary)
        self.assertIn("需要人工处理的问题：1", summary)
        self.assertIn("比赛指定哪个测试集？", summary)
        self.assertIn("原因：官方资料不足", summary)

    def test_summary_uses_spaced_sections_and_faq_repository_link(self):
        """The delivered daily report keeps the agreed mobile-friendly layout."""

        start = datetime(2026, 9, 9, 20, 0, tzinfo=TZ)
        end = datetime(2026, 9, 10, 20, 0, tzinfo=TZ)
        summary = build_summary(
            [
                Question(end.timestamp(), "K1 怎么刷机？", STATUS_HANDLED),
                Question(
                    end.timestamp() + 60,
                    "赛题 2 指定哪个测试集？",
                    STATUS_HUMAN_REQUIRED,
                    "官方资料不足，机器人已标记需要人工确认",
                ),
            ],
            start,
            end,
            faq_sync={
                "added": 0,
                "duplicates": 0,
                "failed": False,
                "published": True,
                "detail": "已恢复先前成功合并的FAQ发布状态",
                "local_checkout_updated": True,
                "rag_synced": True,
            },
        )

        self.assertEqual(
            summary,
            "CIE 技术支持日报（2026-09-10）\n"
            "统计区间：09-09 20:00—09-10 20:00\n"
            "问题总数：2（已按内容去重）\n"
            "主题分布：K1/开发板 1、其他 1\n"
            "\n"
            "已处理问题：1\n"
            "1、[20:00] K1 怎么刷机？\n"
            "\n"
            "需要人工处理的问题：1\n"
            "1、[20:01] 赛题 2 指定哪个测试集？\n"
            "   原因：官方资料不足，机器人已标记需要人工确认\n"
            "\n"
            "FAQ 更新：已审核合并 0 条\n"
            "FAQ 状态：已恢复先前成功合并的FAQ发布状态\n"
            "RAG 刷新：已完成\n"
            "FAQ 仓库：[2026 CIE FAQ](https://example.invalid/faq)",
        )

    def test_empty_summary_keeps_the_same_report_template(self):
        """No-question days must not switch to a different report layout."""

        start = datetime(2026, 9, 9, 20, 0, tzinfo=TZ)
        end = datetime(2026, 9, 10, 20, 0, tzinfo=TZ)
        summary = build_summary([], start, end)

        self.assertEqual(
            summary,
            "CIE 技术支持日报（2026-09-10）\n"
            "统计区间：09-09 20:00—09-10 20:00\n"
            "问题总数：0（已按内容去重）\n"
            "主题分布：无\n"
            "\n"
            "已处理问题：0\n"
            "无\n"
            "\n"
            "需要人工处理的问题：0\n"
            "无\n"
            "\n"
            "FAQ 仓库：[2026 CIE FAQ](https://example.invalid/faq)",
        )


if __name__ == "__main__":
    unittest.main()
