"""Evidence-backed answer candidates, compatible with the existing FAQ queue."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from support_reply import render_answer


def archive_answer(question, result, session_id, *, profile=None):
    answer = result["answer"]
    if answer["status"] not in {"answered", "partial"}:
        return
    profile = Path(profile or os.environ["HERMES_HOME"])
    folder = profile / "website-answers"  # Preserve the existing daily job's path.
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / "candidates.sqlite"
    scope = hashlib.sha256(session_id.encode()).hexdigest()
    query = result.get("query", question)
    fingerprint = hashlib.sha256(
        (scope + "\0" + " ".join(query.casefold().split())).encode()
    ).hexdigest()
    refs = []
    # Preserve the existing official-only automatic FAQ publication boundary.
    # Mixed/upstream/community evidence is archived intact for manual review.
    official_only = all(
        result["sources"][sid]["source_kind"] == "official"
        for sid in answer["source_ids"]
    )
    for sid in answer["source_ids"]:
        source = result["sources"][sid]
        refs.append(
            {
                "title": source["title"],
                "url": source.get("page_url", ""),
                "canonical_path": source["path"],
                "updated_at": source.get("updated_at", ""),
                "source_kind": source["source_kind"],
                "source_verified": source.get("verified", False),
                "verified": source.get("verified", False) and official_only,
                "verification": "document_snapshot"
                if source["source_kind"] == "official"
                else "fetched_source",
            }
        )
    # Keep actual source excerpts in the historical original_answer column.
    # A generated summary must never be represented as an official original.
    excerpts = []
    for statement in [answer.get("conclusion")] + answer["points"]:
        for span in (statement or {}).get("evidence", []):
            source = result["sources"][span["source_id"]]
            excerpt = source["title"] + "\n" + span["quote"]
            if excerpt not in excerpts:
                excerpts.append(excerpt)
    original = "\n\n".join(excerpts)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path, timeout=1) as conn:
        path.chmod(0o600)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS candidates (
            fingerprint TEXT PRIMARY KEY, question TEXT, original_answer TEXT,
            references_json TEXT, qq_summary TEXT, fetched_at TEXT, status TEXT,
            source_query TEXT NOT NULL DEFAULT '', session_hash TEXT NOT NULL DEFAULT '',
            question_key TEXT NOT NULL DEFAULT '')""")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}
        for name in (
            "source_query",
            "session_hash",
            "question_key",
            "source_type",
            "evidence_json",
        ):
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE candidates ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                )
        conn.execute(
            """INSERT INTO candidates (
            fingerprint,question,original_answer,references_json,qq_summary,fetched_at,status,
            source_query,session_hash,question_key,source_type,evidence_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
            question=excluded.question,original_answer=excluded.original_answer,
            references_json=excluded.references_json,qq_summary=excluded.qq_summary,
            fetched_at=excluded.fetched_at,status=excluded.status,
            source_query=excluded.source_query,session_hash=excluded.session_hash,
            question_key=excluded.question_key,source_type=excluded.source_type,
            evidence_json=excluded.evidence_json""",
            (
                fingerprint,
                question,
                original,
                json.dumps(refs, ensure_ascii=False),
                result["text"],
                now,
                "pending_review",
                query,
                scope,
                re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", question.casefold()),
                "independent_documents",
                json.dumps(
                    {"answer": answer, "sources": result["sources"]}, ensure_ascii=False
                ),
            ),
        )
    draft = folder / (fingerprint + ".md")
    draft.write_text(
        "# FAQ 候选（待审核）\n\n来源：独立检索原文后生成，未调用官网机器人。\n"
        "原文证据保存在候选库；不能把生成答复视为官方原文。\n\n## 问题\n\n"
        + question
        + "\n\n## 答复\n\n"
        + render_answer(answer, result["sources"], markdown=False)
        + "\n\n## 引用原文\n\n"
        + original
        + "\n",
        encoding="utf-8",
    )
    draft.chmod(0o600)
