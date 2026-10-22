#!/usr/bin/env python3
"""Create and optionally deliver an anonymized summary of QQ questions."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from support_paths import FAQ_REPO, FAQ_URL, profile_dir

DEFAULT_DB = profile_dir() / "state.db"
DEFAULT_HERMES = Path(os.environ.get("QQ_BOT_HERMES_BIN", "hermes"))
DEFAULT_CANDIDATES_DB = profile_dir() / "website-answers/candidates.sqlite"
DEFAULT_FAQ_REPO = FAQ_REPO
DEFAULT_FAQ_PATH = DEFAULT_FAQ_REPO / "faq.md"
FAQ_REPOSITORY_URL = FAQ_URL
TZ = ZoneInfo("Asia/Shanghai")

SKIP_MESSAGES = {
    "你好",
    "您好",
    "嗨",
    "hi",
    "hello",
    "在吗",
    "谢谢",
    "感谢",
    "你是谁",
    "你叫什么",
    "你用的什么模型",
    "今日问题汇总",
    "今天问题汇总",
    "今日汇总",
    "问题汇总",
}
TOPICS = {
    "K1/开发板": ("k1", "开发板", "muse pi", "硬件"),
    "K3/开发板": ("k3",),
    "Bianbu/系统": ("bianbu", "系统", "刷机", "烧录", "镜像", "驱动"),
    "ROS/机器人": ("ros", "机器人", "导航", "slam", "雷达", "舵机"),
    "模型/推理": (
        "模型",
        "llm",
        "llama",
        "model zoo",
        "qwen",
        "deepseek",
        "推理",
        "量化",
        "gguf",
    ),
    "Python/开发工具": ("python", "pypi", "pip", "虚拟环境"),
    "性能测试": ("性能", "benchmark", "bench", "prefill", "decode", "token", "t/s"),
    "外设/多媒体": ("摄像头", "相机", "麦克风", "音频", "显示", "hdmi", "gpio"),
}

STATUS_HANDLED = "handled"
STATUS_HUMAN_REQUIRED = "human_required"
STATUS_BOT_ISSUE = "bot_issue"
STATUS_WAITING = "waiting"
HUMAN_REQUIRED_MARKERS = (
    "需要人工确认",
    "需要人工处理",
    "请联系赛事组织方",
    "请联系技术支持",
    "无法从官方资料确认",
)
FAILED_REPLY_MARKERS = (
    "model provider failed",
    "gateway shutting down",
    "interrupted current task",
    "i'll respond to your message shortly",
)


@dataclass(frozen=True)
class Question:
    timestamp: float
    text: str
    status: str = STATUS_HANDLED
    reason: str = ""
    # This is a local opaque database key, never rendered in a report.  It
    # joins a daily handled message to its archived official-source candidate.
    session_id: str = ""
    candidate_question: str = ""
    candidate_timestamp: float = 0
    # Explicit user environment is part of deduplication, not a new question.
    environment: tuple[tuple[str, str], ...] = ()


def sanitize(text: str) -> str:
    """Remove credentials and personal identifiers before reporting."""

    from qq_input import question_text

    value = " ".join(question_text(text).split())
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[API_KEY已隐藏]", value)
    value = re.sub(
        r"(?i)\b(api[_ -]?key|token|secret|password|passwd)\s*[:=：]\s*\S+",
        r"\1=[已隐藏]",
        value,
    )
    value = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[邮箱已隐藏]", value
    )
    return value[:300]


def is_support_question(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？!?、,.：:；;~～]+$", "", text.strip().lower())
    compact = re.sub(r"\s+", "", normalized)
    is_summary_request = (
        len(compact) <= 12
        and ("今日" in compact or "今天" in compact)
        and "汇总" in compact
        and "问" in compact
        and "题" in compact
    )
    if (
        not normalized
        or normalized.startswith(("/", "[system:"))
        or normalized in SKIP_MESSAGES
        or is_summary_request
    ):
        return False
    return len(normalized) >= 3


def load_questions(
    db_path: Path,
    start: datetime,
    end: datetime,
    *,
    groups_only: bool = False,
) -> list[Question]:
    from summary_questions import CONTEXT_SECONDS, collect_questions

    chat_filter = " AND s.chat_type = 'group'" if groups_only else ""
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
        chat = "s.chat_id" if "chat_id" in columns else "''"
        user = "s.user_id" if "user_id" in columns else "''"
        rows = connection.execute(
            f"""
            SELECT m.session_id, m.role, m.timestamp, m.content, {chat}, {user}
            FROM messages AS m
            JOIN sessions AS s ON s.id = m.session_id
            WHERE s.source = 'qqbot'
              {chat_filter}
              AND m.role IN ('user', 'assistant')
              AND m.active = 1
              AND m.timestamp >= ?
              AND m.timestamp < ?
            ORDER BY m.timestamp ASC, m.rowid ASC
            """,
            (start.timestamp() - CONTEXT_SECONDS, end.timestamp()),
        ).fetchall()
    return collect_questions(rows, start.timestamp())


def classify_response(responses: list[str]) -> tuple[str, str]:
    """Classify whether a question was answered or needs a human follow-up."""

    # A later correction supersedes an earlier failure in the same turn.
    replies = [response.strip() for response in responses if response.strip()]
    combined = replies[-1] if replies else ""
    if not combined:
        return STATUS_HUMAN_REQUIRED, "未检测到机器人有效回复"

    independent_plain = re.sub(r"[*_`>#]", "", combined)
    if any(
        marker in independent_plain
        for marker in ("查询超时：", "答复暂不可用：", "本次尚未开始处理")
    ):
        return STATUS_BOT_ISSUE, "问答服务超时、繁忙或异常，尚未提供有效答复"
    if not any(marker in independent_plain for marker in HUMAN_REQUIRED_MARKERS):
        if "待补充：" in independent_plain:
            return STATUS_WAITING, "等待用户补充必要条件"
        if any(marker in independent_plain for marker in ("待确认：", "资料不足：")):
            return STATUS_BOT_ISSUE, "机器人尚未形成有效答复，不能据此判定人工问题"

    if (
        "摘要整理暂未完成" in combined
        or "摘要服务暂不可用" in combined
        or "摘要未通过发送格式校验" in combined
    ):
        return STATUS_BOT_ISSUE, "摘要处理失败，需技术支持重试；不代表官方资料不足"

    lowered = combined.lower()
    if any(marker in lowered for marker in FAILED_REPLY_MARKERS):
        return STATUS_BOT_ISSUE, "机器人回复中断或模型调用失败"

    plain = re.sub(r"[*_`>#]", "", combined)
    plain = plain.replace("不需要人工确认", "")
    if any(marker in plain for marker in HUMAN_REQUIRED_MARKERS):
        return STATUS_HUMAN_REQUIRED, "官方资料不足，机器人已标记需要人工确认"

    return STATUS_HANDLED, ""


def classify(text: str) -> str:
    lowered = text.lower()
    scores = {
        topic: sum(1 for keyword in keywords if keyword in lowered)
        for topic, keywords in TOPICS.items()
    }
    topic, score = max(scores.items(), key=lambda item: item[1])
    return topic if score else "其他"


def _result_value(result: object, name: str, default: object = 0) -> object:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


def _result_count(result: object, name: str) -> int:
    """Read a release count from either a flattened or nested result."""

    value = _result_value(result, name, None)
    if value is None:
        staged = _result_value(result, "staged", None)
        value = _result_value(staged, name, 0) if staged is not None else 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def append_faq_status(lines: list[str], result: object | None) -> None:
    """Describe the actual FAQ hand-off result without claiming publication."""

    if result is None:
        return
    if bool(_result_value(result, "failed", False)):
        detail = str(_result_value(result, "detail", ""))
        lines.append("FAQ 更新：失败" + (f"（{detail}）" if detail else ""))
        return

    added = _result_count(result, "added")
    duplicates = _result_count(result, "duplicates")
    missing = _result_count(result, "missing")
    skipped = _result_count(result, "skipped")
    published = bool(_result_value(result, "published", False))
    detail = str(_result_value(result, "detail", ""))
    if published:
        lines.append(f"FAQ 更新：已审核合并 {added} 条")
    elif added:
        lines.append(f"FAQ 更新：已新增 {added} 条候选，尚未合并")
    elif duplicates:
        lines.append(f"FAQ 更新：未新增，已去重 {duplicates} 条")
    else:
        lines.append("FAQ 更新：无可入库的已处理候选")
    if missing:
        lines.append(f"FAQ 待补充：{missing} 条已处理问题没有官网答复候选")
    if skipped:
        lines.append(f"FAQ 跳过：{skipped} 条是上下文补充或不适合独立入库")
    if detail and detail not in {"", "无可入库的已处理候选"}:
        lines.append(f"FAQ 状态：{detail}")
    if published and not bool(_result_value(result, "local_checkout_updated", False)):
        lines.append("FAQ 本地同步：待修复，RAG 未刷新")
    if _result_value(result, "rag_synced", None) is True:
        lines.append("RAG 刷新：已完成")
    elif _result_value(result, "rag_synced", None) is False:
        lines.append("RAG 刷新：失败，FAQ 已保留，等待下次同步")


def append_faq_repository(lines: list[str]) -> None:
    """Append the stable public FAQ entry point to every daily report."""

    if FAQ_REPOSITORY_URL:
        lines.append(f"FAQ 仓库：[2026 CIE FAQ]({FAQ_REPOSITORY_URL})")


def build_summary(
    questions: list[Question],
    start: datetime,
    end: datetime,
    *,
    faq_sync: object | None = None,
) -> str:
    questions = [
        question for question in questions if question.status != STATUS_BOT_ISSUE
    ]
    title = f"CIE 技术支持日报（{end:%Y-%m-%d}）"
    lines = [title, f"统计区间：{start:%m-%d %H:%M}—{end:%m-%d %H:%M}"]
    handled = [question for question in questions if question.status == STATUS_HANDLED]
    human_required = [
        question for question in questions if question.status == STATUS_HUMAN_REQUIRED
    ]
    topic_counts = Counter(classify(question.text) for question in questions)
    lines.append(f"问题总数：{len(questions)}（已按内容去重）")
    lines.append(
        "主题分布："
        + (
            "、".join(f"{topic} {count}" for topic, count in topic_counts.most_common())
            or "无"
        )
    )
    lines.append("")
    append_question_section(lines, "已处理问题", handled)
    lines.append("")
    append_question_section(
        lines, "需要人工处理的问题", human_required, show_reason=True
    )
    waiting = [question for question in questions if question.status == STATUS_WAITING]
    if waiting:
        lines.append("")
        append_question_section(lines, "待补充信息的问题", waiting, show_reason=True)
    lines.append("")
    append_faq_status(lines, faq_sync)
    append_faq_repository(lines)
    return "\n".join(lines)


def append_question_section(
    lines: list[str],
    title: str,
    questions: list[Question],
    *,
    show_reason: bool = False,
    limit: int = 10,
) -> None:
    lines.append(f"{title}：{len(questions)}")
    if not questions:
        lines.append("无")
        return

    for index, question in enumerate(questions[:limit], start=1):
        time_text = datetime.fromtimestamp(question.timestamp, TZ).strftime("%H:%M")
        question_text = question.text[:120]
        if len(question.text) > 120:
            question_text += "…"
        lines.append(f"{index}、[{time_text}] {question_text}")
        if show_reason:
            lines.append(f"   原因：{question.reason or '需要技术支持人员复核'}")
    if len(questions) > limit:
        lines.append(f"另有 {len(questions) - limit} 个问题未展开。")


def deliver(
    summary: str,
    hermes_bin: Path,
    delivery_target: str = "qqbot",
) -> None:
    """Deliver a report to the configured Hermes recipient.

    The default preserves the existing QQ home-channel behavior.  An operator
    may supply an explicit target such as ``qqbot:<group_openid>`` for this
    scheduled report without changing other QQ notifications.
    """
    target = delivery_target.strip()
    if not target:
        raise ValueError("delivery target must not be empty")
    subprocess.run(
        [str(hermes_bin), "send", "--to", target, "--file", "-", "--quiet"],
        input=summary,
        text=True,
        check=True,
        timeout=60,
    )


def sync_faq(questions: list[Question], candidates_db: Path, faq_path: Path):
    """Stage handled official-source answers into the supplied FAQ file.

    This intentionally does not run git.  The scheduled publishing path uses
    an isolated worktree and a separate reviewer gate; the small function is
    also safe to test with temporary paths.
    """

    from faq_sync import stage_candidates

    return stage_candidates(questions, candidates_db, faq_path)


def _is_live_faq_checkout(path: Path, repo: Path) -> bool:
    """Avoid a manual report silently editing this bot's live FAQ checkout.

    ``/tmp`` can itself be a Git checkout on a developer machine, so a generic
    upward `.git` scan would incorrectly disable isolated test paths.
    """

    try:
        return (
            path.resolve().is_relative_to(repo.resolve()) and (repo / ".git").exists()
        )
    except OSError:
        return False


def _faq_sync_for_run(args: argparse.Namespace, questions: list[Question]):
    candidates_db = Path(getattr(args, "faq_candidates_db", DEFAULT_CANDIDATES_DB))
    faq_path = Path(getattr(args, "faq_path", DEFAULT_FAQ_PATH))
    faq_repo = Path(getattr(args, "faq_repo", DEFAULT_FAQ_REPO))
    if getattr(args, "faq_publish", False):
        from faq_sync import publish_daily_candidates

        return publish_daily_candidates(
            questions,
            candidates_db,
            faq_repo,
            target_branch=str(getattr(args, "faq_target_branch", "master")),
            dry_run=bool(getattr(args, "faq_dry_run", False)),
            auto_approve_if_clean=bool(
                getattr(args, "faq_auto_approve_if_clean", False)
            ),
        )
    if not getattr(args, "faq_stage", False):
        return None  # Public installation defaults to a read-only preview.
    # A user-requested on-demand report is informational.  Do not mutate the
    # live checkout merely because its command did not include the explicit
    # release flag.  Temporary paths are used by unit tests and can stage.
    if _is_live_faq_checkout(faq_path, faq_repo):
        return {
            "added": 0,
            "duplicates": 0,
            "failed": False,
            "detail": "FAQ发布需运维显式执行审核命令",
        }
    return sync_faq(questions, candidates_db, faq_path)


def refresh_rag_after_faq_publish(result: object | None) -> object | None:
    """Refresh the FAQ-backed RAG index only after a verified local merge.

    FAQ publication remains truthful even when a later indexing operation is
    unavailable.  The returned mapping intentionally preserves the release
    outcome and adds a separate RAG receipt for the daily report.
    """

    if result is None or not bool(_result_value(result, "published", False)):
        return result
    if not bool(_result_value(result, "local_checkout_updated", False)):
        return result
    fields = (
        "added",
        "duplicates",
        "missing",
        "skipped",
        "failed",
        "published",
        "detail",
        "local_checkout_updated",
    )
    receipt = {field: _result_value(result, field, None) for field in fields}
    try:
        from sync_docs import sync

        sync_result = sync()
        receipt["rag_synced"] = bool(sync_result.get("published"))
    except Exception:
        receipt["rag_synced"] = False
    return receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument(
        "--today",
        action="store_true",
        help="Summarize from local midnight instead of a rolling hour window.",
    )
    parser.add_argument(
        "--groups-only",
        action="store_true",
        help="Exclude private QQ conversations from the report.",
    )
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--faq-stage", action="store_true", help="Explicitly stage candidates in an isolated FAQ path; never publish")
    parser.add_argument("--hermes-bin", type=Path, default=DEFAULT_HERMES)
    parser.add_argument(
        "--delivery-target",
        default="qqbot",
        help="Hermes recipient for the report; defaults to the configured QQ home channel.",
    )
    parser.add_argument("--faq-candidates-db", type=Path, default=DEFAULT_CANDIDATES_DB)
    parser.add_argument("--faq-repo", type=Path, default=DEFAULT_FAQ_REPO)
    parser.add_argument("--faq-path", type=Path, default=DEFAULT_FAQ_PATH)
    parser.add_argument(
        "--faq-publish",
        action="store_true",
        help="Run the isolated FAQ branch/review/publish path before sending the report.",
    )
    parser.add_argument(
        "--faq-dry-run",
        action="store_true",
        help="Run FAQ selection/review without writing git state or candidate status.",
    )
    parser.add_argument(
        "--faq-auto-approve-if-clean",
        action="store_true",
        help="Allow the independent verified-reference reviewer to merge a clean FAQ branch.",
    )
    parser.add_argument("--faq-target-branch", default="master")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    end = datetime.now(TZ)
    start = (
        end.replace(hour=0, minute=0, second=0, microsecond=0)
        if args.today
        else end - timedelta(hours=max(0.1, args.hours))
    )
    questions = load_questions(args.db, start, end, groups_only=args.groups_only)
    faq_sync = _faq_sync_for_run(args, questions)
    faq_sync = refresh_rag_after_faq_publish(faq_sync)
    summary = build_summary(questions, start, end, faq_sync=faq_sync)
    print(summary)
    if args.send:
        deliver(summary, args.hermes_bin, getattr(args, "delivery_target", "qqbot"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
