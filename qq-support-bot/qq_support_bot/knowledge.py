from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .database import Database

LOGGER = logging.getLogger(__name__)
TREE_URL = "https://www.spacemit.com/api-server/document/get-doc-tree?language={language}"
PAGE_URL = "https://www.spacemit.com/community/document/info?lang={language}&nodepath={path}"
SKIPPED_NAMES = {"banner.txt", "banner.webp"}
SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt")
CHUNKER_VERSION = "3"


@dataclass(frozen=True)
class DocumentRef:
    id: str
    path: str
    title: str
    source_url: str
    page_url: str
    updated_at: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    heading: str
    path: str
    page_url: str
    content: str
    score: float

    def citation(self) -> dict[str, str]:
        return {"title": self.title, "url": self.page_url, "path": self.path}


def _request_text(url: str, timeout: int = 30) -> str:
    parts = urllib.parse.urlsplit(url)
    safe_url = urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/%:@"),
            urllib.parse.quote(urllib.parse.unquote(parts.query), safe="=&?/:@,+"),
            parts.fragment,
        )
    )
    request = urllib.request.Request(safe_url, headers={"User-Agent": "SpacemiT-QQ-Support-Bot/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _walk_tree(nodes: Iterable[dict], language: str, prefixes: tuple[str, ...]) -> list[DocumentRef]:
    documents: list[DocumentRef] = []
    for node in nodes:
        children = node.get("children") or []
        if children:
            documents.extend(_walk_tree(children, language, prefixes))
        if node.get("isDirectory"):
            continue
        path = str(node.get("path") or "").strip("/")
        doc_name = str(node.get("docName") or "")
        source_url = str(node.get("storgeAddress") or "")
        if not path or not source_url or doc_name in SKIPPED_NAMES:
            continue
        if not path.lower().endswith(SUPPORTED_SUFFIXES):
            continue
        if prefixes and not any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
            continue
        page_path = urllib.parse.quote(path, safe="/")
        documents.append(
            DocumentRef(
                id=str(node.get("id") or hashlib.sha256(path.encode()).hexdigest()),
                path=path,
                title=str(node.get("name") or doc_name or path),
                source_url=source_url,
                page_url=PAGE_URL.format(language=language, path=page_path),
                updated_at=str(node.get("updateTime") or ""),
            )
        )
    return documents


def fetch_document_tree(language: str = "zh", prefixes: tuple[str, ...] = ()) -> list[DocumentRef]:
    payload = json.loads(_request_text(TREE_URL.format(language=urllib.parse.quote(language))))
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise RuntimeError("开发者网站返回了无法识别的文档树")
    return _walk_tree(data, language, prefixes)


def _split_markdown(text: str, max_chars: int = 650, overlap: int = 80) -> list[tuple[str, str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"!\[[^\]]*\]\([^\n)]*\)", "", text)
    text = re.sub(r"<img\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    sections: list[tuple[str, str]] = []
    heading = "正文"
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        content = "\n".join(buffer).strip()
        if not content:
            buffer = []
            return
        start = 0
        while start < len(content):
            end = min(len(content), start + max_chars)
            if end < len(content):
                boundary = content.rfind("\n", start, end)
                if boundary > start + max_chars // 2:
                    end = boundary
            part = content[start:end].strip()
            if part:
                sections.append((heading, part))
            if end >= len(content):
                break
            start = max(start + 1, end - overlap)
        buffer = []

    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            flush()
            heading = re.sub(r"\s+#+$", "", match.group(1)).strip()
        else:
            buffer.append(line)
    flush()
    return sections or [("正文", text[:max_chars])]


def sync_documents(database: Database, language: str = "zh", prefixes: tuple[str, ...] = ()) -> dict[str, int]:
    refs = fetch_document_tree(language, prefixes)
    LOGGER.info("发现 %d 篇可同步文档", len(refs))
    with database.connect() as connection:
        existing_rows = connection.execute("SELECT id, content_hash, updated_at FROM documents").fetchall()
    existing = {
        row["id"]: {"content_hash": row["content_hash"], "updated_at": row["updated_at"] or ""}
        for row in existing_rows
    }
    force_rebuild = database.get_metadata("chunker_version") != CHUNKER_VERSION
    refs_to_download = [
        ref
        for ref in refs
        if force_rebuild
        or ref.id not in existing
        or not ref.updated_at
        or existing[ref.id]["updated_at"] != ref.updated_at
    ]
    download_ids = {ref.id for ref in refs_to_download}
    LOGGER.info("需要下载 %d 篇文档（强制重建=%s）", len(refs_to_download), force_rebuild)
    downloaded: dict[str, str] = {}
    failures = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {pool.submit(_request_text, ref.source_url): ref for ref in refs_to_download}
        for future in as_completed(future_map):
            ref = future_map[future]
            try:
                downloaded[ref.id] = future.result()
            except Exception as error:  # noqa: BLE001 - one document must not stop a full sync
                failures += 1
                LOGGER.warning("同步失败 %s: %s", ref.path, error)

    changed = 0
    unchanged = len(refs) - len(refs_to_download)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with database.connect() as connection:
        for ref in refs:
            content = downloaded.get(ref.id)
            if content is None:
                if ref.id in existing and ref.id not in download_ids:
                    connection.execute("UPDATE documents SET synced_at = ? WHERE id = ?", (now, ref.id))
                continue
            digest = hashlib.sha256(b"chunker-v3\0" + content.encode("utf-8")).hexdigest()
            if existing.get(ref.id, {}).get("content_hash") == digest:
                unchanged += 1
                connection.execute(
                    "UPDATE documents SET synced_at = ?, updated_at = ? WHERE id = ?",
                    (now, ref.updated_at, ref.id),
                )
                continue
            changed += 1
            connection.execute(
                """
                INSERT INTO documents (
                    id, path, title, page_url, source_url, updated_at, content_hash, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    path=excluded.path,
                    title=excluded.title,
                    page_url=excluded.page_url,
                    source_url=excluded.source_url,
                    updated_at=excluded.updated_at,
                    content_hash=excluded.content_hash,
                    synced_at=excluded.synced_at
                """,
                (
                    ref.id,
                    ref.path,
                    ref.title,
                    ref.page_url,
                    ref.source_url,
                    ref.updated_at,
                    digest,
                    now,
                ),
            )
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (ref.id,))
            connection.executemany(
                "INSERT INTO chunks (document_id, ordinal, heading, content) VALUES (?, ?, ?, ?)",
                (
                    (ref.id, ordinal, heading, chunk)
                    for ordinal, (heading, chunk) in enumerate(_split_markdown(content))
                ),
            )
    if failures == 0:
        database.set_metadata("chunker_version", CHUNKER_VERSION)
        database.set_metadata("last_sync_at", now)
    return {
        "total": len(refs),
        "downloaded": len(refs_to_download),
        "changed": changed,
        "unchanged": unchanged,
        "failed": failures,
    }


_ASCII_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_.+/-]{1,}|\d+(?:\.\d+)*")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_SYNONYM_GROUPS = (
    ("刷写", "刷机", "烧录", "烧写", "镜像烧录"),
    ("报错", "错误", "失败", "异常"),
    ("llm", "大语言模型", "大模型", "语言模型"),
    ("部署", "落地", "安装"),
)


def _query_terms(query: str) -> list[tuple[str, float]]:
    lowered = query.lower()
    weighted: dict[str, float] = {}
    for token in _ASCII_TOKEN.findall(lowered):
        token_weight = 5.0 if re.fullmatch(r"[kp]\d+", token) else (4.0 if len(token) >= 3 else 2.0)
        weighted[token] = max(weighted.get(token, 0), token_weight)
    for run in _CJK_RUN.findall(lowered):
        for size, weight in ((4, 4.0), (3, 3.0), (2, 1.5)):
            for index in range(max(0, len(run) - size + 1)):
                token = run[index : index + size]
                weighted[token] = max(weighted.get(token, 0), weight)
    for group in _SYNONYM_GROUPS:
        if any(term in lowered for term in group):
            for term in group:
                weighted[term] = max(weighted.get(term, 0), 5.0)
    if any(term in lowered for term in _SYNONYM_GROUPS[0]):
        for term, weight in (("titan", 4.0), ("刷机工具", 5.0), ("烧录工具", 5.0), ("镜像", 2.0)):
            weighted[term] = max(weighted.get(term, 0), weight)
    return sorted(weighted.items(), key=lambda item: (-item[1], -len(item[0])))[:40]


def search(database: Database, query: str, limit: int = 5) -> list[SearchResult]:
    terms = _query_terms(query)
    if not terms:
        return []
    results: list[SearchResult] = []
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT d.title, d.path, d.page_url, c.heading, c.content
            FROM chunks c JOIN documents d ON d.id = c.document_id
            """
        ).fetchall()
    lowered_query = query.lower()
    normalized_query = "".join(lowered_query.split())
    required_groups = [group for group in _SYNONYM_GROUPS if any(term in lowered_query for term in group)]
    for row in rows:
        title = str(row["title"])
        path = str(row["path"])
        heading = str(row["heading"])
        content = str(row["content"])
        title_haystack = (title + " " + heading + " " + path).lower()
        content_haystack = content.lower()
        all_haystack = title_haystack + " " + content_haystack
        score = 0.0
        for term, weight in terms:
            if term in title_haystack:
                score += weight * 5
            occurrences = content_haystack.count(term)
            score += min(occurrences, 4) * weight
        compact_content = "".join(content_haystack.split())
        if len(normalized_query) >= 4 and normalized_query in compact_content:
            score += 30
        for group in required_groups:
            if not any(term in all_haystack for term in group):
                score *= 0.2
            elif any(term in title_haystack for term in group):
                # A heading/path that names every requested intent is much
                # stronger evidence than an incidental mention in the body.
                score += 24
        path_lower = path.lower()
        if "k1" in lowered_query:
            if "k1" in title_haystack:
                score *= 1.5
            elif "k1" not in all_haystack:
                score *= 0.1
            elif "k3" in path_lower and "k1" not in path_lower:
                score *= 0.1
        if "k3" in lowered_query and "k1" in path_lower and "k3" not in path_lower:
            score *= 0.1
        if path_lower.startswith("competition/"):
            score *= 1.25
        if "bianbu" in lowered_query:
            if "/bianbu/" in path_lower:
                score *= 1.4
            if "/openharmony/" in path_lower or "/buildroot/" in path_lower:
                score *= 0.1
        if not any(term in lowered_query for term in ("报错", "错误", "失败", "异常", "怎么办")):
            if any(term in heading for term in ("报错", "错误", "失败", "异常", "怎么办")):
                score *= 0.45
        if score > 0:
            results.append(
                SearchResult(
                    title=title,
                    heading=heading,
                    path=path,
                    page_url=str(row["page_url"]),
                    content=content,
                    score=score,
                )
            )
    results.sort(key=lambda item: item.score, reverse=True)
    selected: list[SearchResult] = []
    per_path: dict[str, int] = {}
    for result in results:
        if per_path.get(result.path, 0) >= 2:
            continue
        selected.append(result)
        per_path[result.path] = per_path.get(result.path, 0) + 1
        if len(selected) >= limit:
            break
    flash_requested = any(term in query.lower() for term in _SYNONYM_GROUPS[0])
    if flash_requested and selected:
        tool_result = next(
            (
                result
                for result in results
                if result.path == "tools/user_guide/flasher_user_guide.md"
                and result.heading == "单机烧录"
            ),
            None,
        )
        if tool_result and all(result.path != tool_result.path for result in selected):
            selected[-1] = tool_result
    return selected
