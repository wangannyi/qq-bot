from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import botpy
from botpy.message import C2CMessage, GroupMessage

from .config import Settings
from .knowledge import sync_documents
from .service import SupportService

LOGGER = logging.getLogger(__name__)


class SupportBot(botpy.Client):
    def __init__(self, service: SupportService, **kwargs):
        super().__init__(**kwargs)
        self.service = service
        self._background_tasks: list[asyncio.Task] = []

    async def on_ready(self):
        LOGGER.info("QQ 机器人已连接：%s", self.robot.name)
        if not self._background_tasks:
            self._background_tasks.append(asyncio.create_task(self._document_sync_loop()))
            if self.service.settings.daily_report_group_openid:
                self._background_tasks.append(asyncio.create_task(self._daily_report_loop()))

    async def _document_sync_loop(self):
        interval = max(1, self.service.settings.doc_sync_interval_hours) * 3600
        while True:
            await asyncio.sleep(interval)
            try:
                result = await asyncio.to_thread(
                    sync_documents,
                    self.service.database,
                    self.service.settings.doc_language,
                    self.service.settings.doc_prefixes,
                )
                LOGGER.info("定时文档同步完成：%s", result)
            except Exception:  # noqa: BLE001
                LOGGER.exception("定时文档同步失败")

    async def _daily_report_loop(self):
        settings = self.service.settings
        while True:
            now = datetime.now().astimezone()
            next_run = now.replace(
                hour=settings.daily_report_hour,
                minute=settings.daily_report_minute,
                second=0,
                microsecond=0,
            )
            if next_run <= now:
                next_run += timedelta(days=1)
            await asyncio.sleep((next_run - now).total_seconds())
            try:
                summary = self.service.database.daily_summary(settings.daily_report_group_openid)
                await self.api.post_group_message(
                    group_openid=settings.daily_report_group_openid,
                    msg_type=0,
                    content=summary,
                )
            except Exception:  # noqa: BLE001
                LOGGER.exception("发送技术支持日报失败")

    async def on_group_at_message_create(self, message: GroupMessage):
        try:
            quick = self.service.quick_answer(message.content, message.group_openid)
            if quick is not None:
                await message._api.post_group_message(
                    group_openid=message.group_openid,
                    msg_type=0,
                    msg_id=message.id,
                    msg_seq=1,
                    content=quick.text,
                )
                return
            await message._api.post_group_message(
                group_openid=message.group_openid,
                msg_type=0,
                msg_id=message.id,
                msg_seq=1,
                content="已收到，正在查询进迭时空官方文档，请稍候……",
            )
            author = getattr(message, "author", None)
            answer = await self.service.answer(
                message.content,
                message_id=message.id,
                group_openid=message.group_openid,
                user_openid=getattr(author, "member_openid", None),
                username=getattr(author, "username", None),
            )
            await message._api.post_group_message(
                group_openid=message.group_openid,
                msg_type=0,
                msg_id=message.id,
                msg_seq=2,
                content=answer.text,
            )
        except Exception:  # noqa: BLE001 - avoid dropping a QQ event without a reply
            LOGGER.exception("处理群消息失败")
            await message._api.post_group_message(
                group_openid=message.group_openid,
                msg_type=0,
                msg_id=message.id,
                content="机器人处理失败，已记录日志，请稍后再试或联系技术支持。",
            )

    async def on_c2c_message_create(self, message: C2CMessage):
        try:
            quick = self.service.quick_answer(message.content)
            if quick is not None:
                await message._api.post_c2c_message(
                    openid=message.author.user_openid,
                    msg_type=0,
                    msg_id=message.id,
                    msg_seq=1,
                    content=quick.text,
                )
                return
            await message._api.post_c2c_message(
                openid=message.author.user_openid,
                msg_type=0,
                msg_id=message.id,
                msg_seq=1,
                content="已收到，正在查询进迭时空官方文档，请稍候……",
            )
            author = getattr(message, "author", None)
            answer = await self.service.answer(
                message.content,
                message_id=message.id,
                user_openid=getattr(author, "user_openid", None),
                username=getattr(author, "username", None),
            )
            await message._api.post_c2c_message(
                openid=message.author.user_openid,
                msg_type=0,
                msg_id=message.id,
                msg_seq=2,
                content=answer.text,
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("处理好友消息失败")


def run(settings: Settings) -> None:
    if not settings.qq_app_id or not settings.qq_app_secret:
        raise SystemExit("缺少 QQ_APP_ID 或 QQ_APP_SECRET，请先在 .env 中配置 QQ 开放平台密钥")
    intents = botpy.Intents(public_messages=True)
    client = SupportBot(service=SupportService(settings), intents=intents)
    client.run(appid=settings.qq_app_id, secret=settings.qq_app_secret)
