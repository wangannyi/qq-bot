#!/usr/bin/env python3
"""Explicit, offline-delivery evaluation: calls configured model, never QQ.

Use an isolated HERMES_HOME. Outputs actual answers and source excerpts for
human review; successful JSON validation is not called semantic accuracy.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from support_reply import InvalidAnswer
from support_runtime import answer_question

CASES = [
    "bianbu如何设置spacemit pypi源",
    "model zoo包含哪些内容 在什么平台适用",
    "spacemit-asr部署方式",
    "K3 Bianbu4.0上如何部署llm",
    "bianbu上python使用指南",
    "怎么申请bianbu cloud",
    "K1部署llama.cpp需要哪些准备",
    "K3 Bianbu4.0支持Ollama加速吗",
    "赛题2有什么固定测试数据集，去哪里下载",
    "K1能直接用K3的系统镜像吗",
    "怎么部署LLM",
    "pip安装报externally-managed-environment怎么办",
    "Bianbu如何创建Python虚拟环境",
    "K3使用Python3.14还是3.12",
    "ASR的voice_env.sh在哪里，有什么用途",
    "SpacemiT AI Gateway和spacemit-asr是什么关系",
    "比赛的pp128和tg128是什么意思",
    "K3 Bianbu4.0上怎么安装ROS2",
    "使用SpacemiT源能保证所有Python包都有RISC-V wheel吗",
    "帮我查未发布的K9芯片参数",
]


def evaluate(question):
    started = time.monotonic()
    try:
        result = answer_question(question)
        return {
            "question": question,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "status": result["answer"]["status"],
            "answer": result["text"],
            "structured_answer": result["answer"],
            "sources": [
                {
                    "title": s["title"],
                    "path": s["path"],
                    "kind": s["source_kind"],
                    "url": s.get("page_url", ""),
                    "content": s["content"],
                }
                for s in result["sources"]
            ],
            "usage": result.get("usage", {}),
        }
    except Exception as exc:
        return {
            "question": question,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "status": "error",
            "error_type": type(exc).__name__,
            "error_code": str(exc)
            if isinstance(exc, InvalidAnswer)
            else "service_failure",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly allow configured model API calls",
    )
    parser.add_argument("--limit", type=int, default=len(CASES))
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error(
            "Pass --live to allow real model requests; no QQ messages will be sent"
        )
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        rows = list(pool.map(evaluate, CASES[: args.limit]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "concurrency": args.concurrency,
                "results": rows,
                "note": "Real model/source evaluation without QQ delivery; semantic accuracy requires review.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    for row in rows:
        print(
            json.dumps(
                {
                    k: v
                    for k, v in row.items()
                    if k not in {"sources", "structured_answer", "usage"}
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return int(any(row["status"] == "error" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
