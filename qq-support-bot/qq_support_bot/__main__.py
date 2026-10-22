from __future__ import annotations

import argparse
import asyncio
import json
import logging

from .config import Settings
from .database import Database
from .knowledge import sync_documents


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="进迭时空赛事 QQ 技术支持机器人")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("sync", help="同步进迭时空开发者文档")
    ask = subparsers.add_parser("ask", help="在命令行测试知识库问答")
    ask.add_argument("question")
    subparsers.add_parser("status", help="查看服务状态")
    subparsers.add_parser("bot", help="启动 QQ 机器人")
    subparsers.add_parser("daily", help="输出最近一天的问题日报")
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = Settings.load()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    database = Database(settings.db_path)
    if args.command == "sync":
        result = sync_documents(database, settings.doc_language, settings.doc_prefixes)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "ask":
        from .service import SupportService

        answer = asyncio.run(SupportService(settings).answer(args.question))
        print(answer.text)
    elif args.command == "status":
        from .service import SupportService

        print(SupportService(settings).status_text())
    elif args.command == "daily":
        print(database.daily_summary())
    elif args.command == "bot":
        from .bot import run

        run(settings)


if __name__ == "__main__":
    main()
