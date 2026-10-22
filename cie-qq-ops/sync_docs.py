#!/usr/bin/env python3
"""Refresh sources, then atomically publish a complete RAG generation."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from retrieval import INDEX, create_index

PROJECT_ROOT = Path(
    os.environ.get(
        "SPACEMIT_SUPPORT_BOT_ROOT",
        str(Path(__file__).resolve().parents[1] / "qq-support-bot"),
    )
).resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from qq_support_bot.knowledge import _walk_tree  # noqa: E402


def docx_text(path: Path) -> str:
    """Extract paragraphs and complete table rows in original document order."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with zipfile.ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find("w:body", ns)
    lines = []
    for node in body:
        if node.tag.endswith("}tbl"):
            for row in node.findall("w:tr", ns):
                lines.append(" | ".join("".join(t.text or "" for t in cell.findall(".//w:t", ns)) for cell in row.findall("w:tc", ns)))
        else:
            text = "".join(t.text or "" for t in node.findall(".//w:t", ns)).strip()
            if text:
                if re.match(r"^[一二三四五六七八九十]+、", text):
                    text = "# " + text
                elif re.match(r"^赛题[12][:：]", text):
                    text = "## " + text
                lines.append(text)
    return "\n\n".join(lines)


def local_documents() -> list[dict]:
    docs = []
    from support_paths import FAQ_REPO, FAQ_URL
    rules_value = os.environ.get("QQ_BOT_RULES_DOCX", "").strip()
    rules = Path(rules_value).expanduser() if rules_value else None
    if rules is not None:
        if not rules.is_file():
            raise ValueError("QQ_BOT_RULES_DOCX must name a readable organizer document")
        docs.append(dict(id="event:rules", path="event/cie-2026/rules",
                         title="Organizer-provided event rules", page_url="", source_url="",
                         updated_at="", source_kind="event_rules", body=docx_text(rules)))
    for name, kind in (("challenge-2-supplement.md", "event_supplement"), ("faq.md", "faq")):
        path = FAQ_REPO / name
        if path.is_file():
            docs.append(dict(id="event:" + name, path="event/cie-2026/" + name,
                             title="Event supplement" if kind == "event_supplement" else "Reviewed FAQ",
                             page_url=FAQ_URL, source_url="", updated_at="", source_kind=kind,
                             body=path.read_text(encoding="utf-8")))
    return docs


def sync(index: Path = INDEX) -> dict:
    index.parent.mkdir(parents=True, exist_ok=True)
    with (index.parent / "sync.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with httpx.Client(trust_env=False, timeout=25, follow_redirects=True) as client:
            tree = client.get("https://www.spacemit.com/api-server/document/get-doc-tree?language=zh")
            tree.raise_for_status()
            payload = tree.json().get("data")
            if not isinstance(payload, list):
                raise ValueError("Invalid official tree; retaining old generation")
            refs = _walk_tree(payload, "zh", ())
            if not refs:
                raise ValueError("Empty official tree; retaining old generation")
            def fetch(ref):
                for attempt in range(3):
                    try:
                        res = client.get(ref.source_url)
                        res.raise_for_status()
                        if not res.text.strip() or res.text.lstrip().lower().startswith(("<!doctype html", "<html")):
                            raise ValueError("Empty/HTML response instead of documentation")
                        return dict(id=ref.id, path=ref.path, title=ref.title, page_url=ref.page_url,
                                    source_url=ref.source_url, updated_at=ref.updated_at,
                                    source_kind="official", body=res.text)
                    except (httpx.HTTPError, ValueError):
                        if attempt == 2:
                            raise
                        time.sleep(attempt + 1)
            docs, failed = [], []
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(fetch, ref): ref for ref in refs}
                for future in as_completed(futures):
                    try:
                        docs.append(future.result())
                    except Exception:
                        failed.append(futures[future].path)
            if len(docs) < len(refs) * 0.95:
                return {"published": False, "failed": failed, "downloaded": len(docs)}
        docs.extend(local_documents())
        checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fd, staging = tempfile.mkstemp(prefix="docs-v2-", suffix=".sqlite", dir=index.parent)
        os.close(fd)
        try:
            create_index(Path(staging), docs, checked)
            import sqlite3
            with sqlite3.connect(staging) as conn:
                conn.execute("INSERT INTO metadata VALUES (?,?)", ("unavailable_sources", json.dumps(failed, ensure_ascii=False)))
            os.replace(staging, index)
        finally:
            Path(staging).unlink(missing_ok=True)
        return {"published": True, "documents": len(docs), "official_documents": len(refs) - len(failed), "checked_at": checked, "failed": failed}


def main() -> int:
    result = sync()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["published"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
