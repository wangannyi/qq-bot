from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    page_url TEXT NOT NULL,
    source_url TEXT NOT NULL,
    updated_at TEXT,
    content_hash TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    group_openid TEXT,
    user_openid TEXT,
    username TEXT,
    content TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'answered',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_created ON questions(created_at);
CREATE INDEX IF NOT EXISTS idx_questions_group ON questions(group_openid, created_at);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def record_question(
        self,
        *,
        message_id: str,
        content: str,
        answer: str,
        sources: list[dict[str, str]],
        confidence: float,
        status: str,
        group_openid: str | None = None,
        user_openid: str | None = None,
        username: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO questions (
                    message_id, group_openid, user_openid, username, content,
                    answer, sources_json, confidence, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    group_openid,
                    user_openid,
                    username,
                    content,
                    answer,
                    json.dumps(sources, ensure_ascii=False),
                    confidence,
                    status,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )

    def get_metadata(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO metadata (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def stats(self) -> dict[str, int | str | None]:
        with self.connect() as connection:
            documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            questions = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            row = connection.execute("SELECT value FROM metadata WHERE key = 'last_sync_at'").fetchone()
            last_sync = row["value"] if row else None
        return {
            "documents": documents,
            "chunks": chunks,
            "questions": questions,
            "last_sync": last_sync,
        }

    def daily_summary(self, group_openid: str | None = None, days: int = 1) -> str:
        since = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        params: list[str] = [since]
        group_clause = ""
        if group_openid:
            group_clause = " AND group_openid = ?"
            params.append(group_openid)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT content, status, created_at
                FROM questions
                WHERE created_at >= ?{group_clause}
                ORDER BY created_at DESC
                """,
                params,
            ).fetchall()
        unresolved = [row for row in rows if row["status"] != "answered"]
        lines = [
            f"技术支持日报（最近 {days} 天）",
            f"问题总数：{len(rows)}",
            f"待人工确认：{len(unresolved)}",
        ]
        if rows:
            lines.append("近期问题：")
            for index, row in enumerate(rows[:10], start=1):
                marker = "[待确认] " if row["status"] != "answered" else ""
                content = " ".join(row["content"].split())
                lines.append(f"{index}. {marker}{content[:80]}")
        return "\n".join(lines)
