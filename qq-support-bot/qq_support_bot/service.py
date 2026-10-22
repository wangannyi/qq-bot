from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from .config import Settings
from .database import Database
from .knowledge import SearchResult, search
from .llm import LLMClient

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answer:
    text: str
    sources: list[dict[str, str]]
    confidence: float
    status: str


class SupportService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings.db_path)
        self.llm = LLMClient(
            settings.llm_base_url,
            settings.llm_model,
            settings.llm_timeout_seconds,
        )

    def status_text(self) -> str:
        stats = self.database.stats()
        model_status = "正常" if self.llm.health() else "不可用"
        return (
            "机器人状态\n"
            f"本地模型：{model_status}\n"
            f"文档：{stats['documents']} 篇 / {stats['chunks']} 个片段\n"
            f"已记录问题：{stats['questions']}\n"
            f"最近同步：{stats['last_sync'] or '尚未同步'}"
        )

    def quick_answer(self, question: str, group_openid: str | None = None) -> Answer | None:
        """Return deterministic answers for commands and casual conversation.

        These messages must not enter document retrieval: short conversational
        phrases otherwise produce accidental Chinese n-gram matches.
        """
        question = " ".join(question.strip().split())
        intent = re.sub(r"[\s，。！？!?、,.：:；;~～]+$", "", question.lower())
        if not intent:
            return Answer("请在 @机器人 后写出你的问题。", [], 0, "needs_human")
        if intent in {"/帮助", "帮助", "help", "/help"}:
            return Answer(
                "使用方法：在群里 @机器人 并描述问题。\n"
                "请尽量附上开发板型号、Bianbu 版本、执行命令和完整报错。\n"
                "命令：/状态、/日报",
                [],
                1,
                "answered",
            )
        if intent in {"/状态", "状态"}:
            return Answer(self.status_text(), [], 1, "answered")
        if intent in {"/日报", "日报"}:
            return Answer(self.database.daily_summary(group_openid), [], 1, "answered")
        if intent in {
            "你是谁",
            "你叫什么",
            "你叫什么名字",
            "介绍一下你自己",
            "自我介绍",
            "你是干什么的",
            "你能做什么",
            "你会什么",
        }:
            return Answer(
                "我是进迭时空机器人比赛技术支持助手，运行在 K1 开发板上。\n"
                "我会查询进迭时空官方开发者文档，帮助解答 K1、MUSE Pi Pro、Bianbu、ROS 和本地模型部署等问题。\n"
                "提问时请尽量附上开发板型号、系统版本、执行命令和完整报错；资料不足时，我会明确标记需要人工确认。",
                [],
                1,
                "answered",
            )
        if intent in {"你好", "您好", "嗨", "hi", "hello", "在吗", "早上好", "下午好", "晚上好"}:
            return Answer(
                "你好，我是进迭时空机器人比赛技术支持助手。请直接描述问题，最好附上开发板型号、系统版本、执行命令和完整报错。",
                [],
                1,
                "answered",
            )
        if intent in {"谢谢", "感谢", "多谢", "thank you", "thanks"}:
            return Answer("不客气。如果问题还没解决，请把执行命令和完整报错发给我。", [], 1, "answered")
        return None

    async def answer(
        self,
        question: str,
        *,
        message_id: str | None = None,
        group_openid: str | None = None,
        user_openid: str | None = None,
        username: str | None = None,
    ) -> Answer:
        question = " ".join(question.strip().split())
        quick = self.quick_answer(question, group_openid)
        if quick is not None:
            return quick

        results = search(self.database, question, self.settings.retrieval_limit)
        if not results:
            answer = "现有官方资料中没有检索到足够依据，需要人工确认。请补充开发板型号、系统版本、完整命令和完整报错。"
            response = Answer(answer, [], 0, "needs_human")
        elif not self.llm.health():
            top = results[0]
            answer = (
                "本地模型暂时不可用，但检索到可能相关的官方资料：\n"
                f"{top.title} - {top.heading}\n{top.page_url}\n"
                "需要人工确认后再给出具体结论。"
            )
            response = Answer(answer, [top.citation()], 0.2, "needs_human")
        else:
            generated = await self.llm.complete(question, results)
            sources = _unique_sources(results)
            text = _ensure_sources(generated.answer, sources)
            response = Answer(
                text=text[: self.settings.max_answer_chars],
                sources=sources,
                confidence=generated.confidence,
                status="needs_human" if generated.needs_human else "answered",
            )

        self.database.record_question(
            message_id=message_id or f"local-{uuid.uuid4()}",
            content=question,
            answer=response.text,
            sources=response.sources,
            confidence=response.confidence,
            status=response.status,
            group_openid=group_openid,
            user_openid=user_openid,
            username=username,
        )
        return response


def _unique_sources(results: list[SearchResult], maximum: int = 1) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for result in results:
        if result.page_url in seen:
            continue
        sources.append(result.citation())
        seen.add(result.page_url)
        if len(sources) >= maximum:
            break
    return sources


def _ensure_sources(answer: str, sources: list[dict[str, str]]) -> str:
    if not sources or any(source["url"] in answer for source in sources):
        return answer
    lines = [answer.rstrip(), "", "参考："]
    for index, source in enumerate(sources, start=1):
        lines.append(f"[{index}] {source['title']} {source['url']}")
    return "\n".join(lines)
