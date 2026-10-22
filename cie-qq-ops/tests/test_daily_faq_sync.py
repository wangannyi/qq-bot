"""Regression contract for the daily-report to FAQ hand-off.

The report is only operationally complete when every eligible handled question
has been handed to the FAQ update/review path.  These tests use temporary files
only; they must never touch the real FAQ checkout or run git/network commands.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))
import daily_summary

TZ = daily_summary.TZ


def _candidate_database(path: Path, *rows: dict[str, object]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE candidates (
                fingerprint TEXT PRIMARY KEY,
                question TEXT,
                original_answer TEXT,
                references_json TEXT,
                qq_summary TEXT,
                fetched_at TEXT,
                status TEXT
            )"""
        )
        for index, row in enumerate(rows):
            reference = {
                "title": str(row.get("reference_title", "官方部署指南")),
                "url": str(
                    row.get(
                        "reference_url",
                        "https://www.spacemit.com/community/doc",
                    )
                ),
                # A FAQ source must be explicitly verified at capture time.
                # Do not let a missing key accidentally model a trusted source.
                "verified": row["verified"],
            }
            if "canonical_path" in row:
                reference["canonical_path"] = str(row["canonical_path"])
            connection.execute(
                "INSERT INTO candidates VALUES (?,?,?,?,?,?,?)",
                (
                    f"candidate-{index}",
                    row["question"],
                    row["original_answer"],
                    json.dumps([reference], ensure_ascii=False),
                    row.get("qq_summary", "简短 QQ 摘要"),
                    row.get("fetched_at", "2026-09-10T12:00:00+08:00"),
                    "pending_review",
                ),
            )


def _result_value(result: object, name: str) -> object:
    """Allow the production result to be a dataclass or a mapping."""

    if isinstance(result, dict):
        return result[name]
    return getattr(result, name)


def test_daily_faq_sync_writes_only_handled_candidate_and_preserves_detailed_source(tmp_path):
    """A handled question becomes one detailed FAQ entry; human follow-ups do not."""

    candidate_db = tmp_path / "candidates.sqlite"
    faq_path = tmp_path / "faq.md"
    faq_path.write_text("# CIE 技术问题 FAQ\n", encoding="utf-8")
    _candidate_database(
        candidate_db,
        {
            "question": "K3 如何部署 LLM？",
            "verified": True,
            "original_answer": (
                "适用于 K3 Bianbu 系统。\n\n"
                "1、安装 llama.cpp 工具。\n\n"
                "2、下载经过验证的 GGUF 模型。\n\n"
                "参考：[官方部署指南](https://www.spacemit.com/community/doc)"
            ),
        },
        {
            "question": "赛题指定哪个隐藏测试集？",
            "verified": True,
            "original_answer": "官网资料未说明具体测试集，需要赛事组确认。",
        },
    )
    now = datetime(2026, 9, 10, 20, 0, tzinfo=TZ)
    questions = [
        daily_summary.Question(now.timestamp(), "K3 如何部署 LLM？", daily_summary.STATUS_HANDLED),
        daily_summary.Question(
            (now + timedelta(seconds=1)).timestamp(),
            "赛题指定哪个隐藏测试集？",
            daily_summary.STATUS_HUMAN_REQUIRED,
            "官方资料不足，机器人已标记需要人工确认",
        ),
    ]

    result = daily_summary.sync_faq(questions, candidate_db, faq_path)

    assert _result_value(result, "added") == 1
    assert _result_value(result, "duplicates") == 0
    assert _result_value(result, "failed") is False
    published = faq_path.read_text(encoding="utf-8")
    assert published.count("## K3 如何部署 LLM？") == 1
    assert "1、安装 llama.cpp 工具。" in published
    assert "https://www.spacemit.com/community/doc" in published
    assert "赛题指定哪个隐藏测试集？" not in published

    with sqlite3.connect(candidate_db) as connection:
        statuses = dict(connection.execute("SELECT question, status FROM candidates"))
    assert statuses["K3 如何部署 LLM？"] == "queued_for_review"
    assert statuses["赛题指定哪个隐藏测试集？"] == "pending_review"


def test_daily_faq_sync_is_idempotent_for_same_question(tmp_path):
    """Running the same daily job again must not create a second FAQ heading."""

    candidate_db = tmp_path / "candidates.sqlite"
    faq_path = tmp_path / "faq.md"
    faq_path.write_text("# CIE 技术问题 FAQ\n", encoding="utf-8")
    _candidate_database(
        candidate_db,
        {
            "question": "Bianbu 如何设置 SpacemiT PyPI 源？",
            "verified": True,
            "reference_title": "Bianbu Python 使用指南",
            "reference_url": (
                "https://www.spacemit.com/community/document/info?"
                "nodepath=software/SDK/bianbu/development/python.md&lang=zh"
            ),
            "canonical_path": "software/SDK/bianbu/development/python.md",
            "original_answer": "配置 SpacemiT 的额外 PyPI 源后检查当前 pip 配置。",
        },
    )
    question = daily_summary.Question(
        datetime(2026, 9, 10, 20, 0, tzinfo=TZ).timestamp(),
        "Bianbu 如何设置 SpacemiT PyPI 源？",
        daily_summary.STATUS_HANDLED,
    )

    first = daily_summary.sync_faq([question], candidate_db, faq_path)
    second = daily_summary.sync_faq([question], candidate_db, faq_path)

    assert _result_value(first, "added") == 1
    assert _result_value(second, "added") == 0
    assert _result_value(second, "duplicates") == 1
    assert faq_path.read_text(encoding="utf-8").count("## Bianbu 如何设置 SpacemiT PyPI 源？") == 1


def test_daily_report_exposes_faq_sync_failure_without_claiming_success(tmp_path):
    """A failed FAQ write is visible in the delivered report and is not marked queued."""

    candidate_db = tmp_path / "candidates.sqlite"
    _candidate_database(
        candidate_db,
        {
            "question": "K1 如何运行 llama-bench？",
            "verified": True,
            "original_answer": "使用官方提供的 llama-bench 工具。",
        },
    )
    question = daily_summary.Question(
        datetime(2026, 9, 10, 20, 0, tzinfo=TZ).timestamp(),
        "K1 如何运行 llama-bench？",
        daily_summary.STATUS_HANDLED,
    )
    faq_directory = tmp_path / "faq-is-a-directory"
    faq_directory.mkdir()

    result = daily_summary.sync_faq([question], candidate_db, faq_directory)
    report = daily_summary.build_summary(
        [question],
        datetime(2026, 9, 9, 20, 0, tzinfo=TZ),
        datetime(2026, 9, 10, 20, 0, tzinfo=TZ),
        faq_sync=result,
    )

    assert _result_value(result, "failed") is True
    assert "FAQ" in report and "失败" in report
    assert "已新增 1" not in report
    with sqlite3.connect(candidate_db) as connection:
        status = connection.execute("SELECT status FROM candidates").fetchone()[0]
    assert status == "pending_review"


def test_daily_main_runs_faq_sync_before_delivery_and_reports_its_outcome(monkeypatch, tmp_path):
    """The scheduled entry point cannot silently omit FAQ synchronization."""

    now = datetime.now(TZ)
    question = daily_summary.Question(now.timestamp(), "K1 如何运行 llama-bench？")
    candidate_db = tmp_path / "candidates.sqlite"
    faq_path = tmp_path / "faq.md"
    calls: list[tuple[str, object]] = []
    args = SimpleNamespace(
        db=tmp_path / "state.db",
        hours=24.0,
        today=False,
        groups_only=True,
        send=True,
        hermes_bin=tmp_path / "hermes",
        delivery_target="qqbot:A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1",
        faq_candidates_db=candidate_db,
        faq_path=faq_path,
        faq_stage=True,
    )

    monkeypatch.setattr(daily_summary, "parse_args", lambda: args)
    monkeypatch.setattr(daily_summary, "load_questions", lambda *a, **k: [question])

    def fake_sync(questions, candidates_db_arg, faq_path_arg):
        calls.append(("sync", (questions, candidates_db_arg, faq_path_arg)))
        return {"added": 1, "duplicates": 0, "failed": False, "detail": "已进入审核队列"}

    monkeypatch.setattr(daily_summary, "sync_faq", fake_sync)
    monkeypatch.setattr(
        daily_summary,
        "deliver",
        lambda summary, hermes, delivery_target="qqbot": calls.append(
            ("deliver", summary, delivery_target)
        ),
    )

    assert daily_summary.main() == 0
    assert calls[0] == ("sync", ([question], candidate_db, faq_path))
    assert calls[1][0] == "deliver"
    assert "FAQ" in str(calls[1][1])
    assert "已新增 1" in str(calls[1][1])
    assert calls[1][2] == "qqbot:A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1A1"


def test_refresh_rag_runs_only_after_published_local_faq_merge(monkeypatch):
    """RAG indexing must not run for an unmerged or unsynchronised FAQ."""

    calls: list[str] = []
    sync_docs = ModuleType("sync_docs")

    def fake_sync():
        calls.append("sync")
        return {"published": True}

    sync_docs.sync = fake_sync
    monkeypatch.setitem(sys.modules, "sync_docs", sync_docs)

    not_published = {"published": False, "local_checkout_updated": True}
    not_local = {"published": True, "local_checkout_updated": False}
    published_local = {
        "added": 1,
        "published": True,
        "local_checkout_updated": True,
        "detail": "已审核合并",
    }

    assert daily_summary.refresh_rag_after_faq_publish(not_published) is not_published
    assert daily_summary.refresh_rag_after_faq_publish(not_local) is not_local
    receipt = daily_summary.refresh_rag_after_faq_publish(published_local)

    assert calls == ["sync"]
    assert receipt is not published_local
    assert _result_value(receipt, "published") is True
    assert _result_value(receipt, "local_checkout_updated") is True
    assert _result_value(receipt, "rag_synced") is True


def test_refresh_rag_failure_keeps_published_faq_result(monkeypatch):
    """A post-publish indexing outage never erases a successful FAQ release."""

    sync_docs = ModuleType("sync_docs")

    def failing_sync():
        raise RuntimeError("RAG temporarily unavailable")

    sync_docs.sync = failing_sync
    monkeypatch.setitem(sys.modules, "sync_docs", sync_docs)
    published_local = {
        "added": 1,
        "duplicates": 0,
        "failed": False,
        "published": True,
        "local_checkout_updated": True,
        "detail": "FAQ merged",
    }

    receipt = daily_summary.refresh_rag_after_faq_publish(published_local)

    assert _result_value(receipt, "published") is True
    assert _result_value(receipt, "local_checkout_updated") is True
    assert _result_value(receipt, "added") == 1
    assert _result_value(receipt, "failed") is False
    assert _result_value(receipt, "rag_synced") is False
