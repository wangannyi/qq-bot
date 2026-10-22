"""Independent raw-document retrieval; no website assistant or model calls."""

from __future__ import annotations

import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

import httpx
from retrieval import INDEX, retrieve, tokens
from support_manuals import (
    manual_blocks,
    pip_manual_answer,
    pip_manual_candidates,
    pip_manual_task,
)

UPSTREAM = {
    "docs.python.org",
    "packaging.python.org",
    "pip.pypa.io",
    "docs.ros.org",
    "docs.astral.sh",
    "www.kernel.org",
    "docs.kernel.org",
}
REPOSITORIES = (
    "https://github.com/ggml-org/",
    "https://github.com/ros2/",
    "https://github.com/spacemit-com/",
    "https://gitee.com/bianbu/",
)


def asr_package_sources(query, sources):
    """Focus package installation on the manual's published-package route.

    Keep literal source spans and prerequisites. Source builds, comparisons,
    stream demos and other platforms continue through established retrieval.
    """
    question = query.split("\n用户明确提供的环境：", 1)[0]
    if (
        not re.search(r"spacemit[-_]asr", question, re.I)
        or not re.search(r"安装|部署", question)
        or not re.search(r"(?<![a-z0-9])k3(?![a-z0-9])", query, re.I)
        or re.search(
            r"源码|编译|C\+\+|流式|麦克风|gateway|比较|区别|兼容|支持|能否",
            question,
            re.I,
        )
    ):
        return sources
    for source in sources:
        if source.get("source_kind") != "official" or not re.search(
            r"/k3/.*ASR\.md$", source.get("path", ""), re.I
        ):
            continue
        body = source["content"]
        start = body.find("运行 Python 示例前")
        end = body.find("方式二，", start)
        if start < 0 or end < 0 or "直接安装发布包" not in body[start:end]:
            continue
        intro = body.split("\n\n", 1)[0]
        model = re.search(r"SenseVoice 默认模型路径[^\n]+", body)
        selected = dict(
            source,
            route_scope="asr_published_package",
            content="\n\n".join(
                [intro, body[start:end].strip()] + ([model[0]] if model else [])
            ),
        )
        return [selected]
    return sources


def source_kind(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        return None
    if parsed.hostname in UPSTREAM:
        return "upstream"
    if any(url.startswith(prefix) for prefix in REPOSITORIES):
        return (
            "community"
            if "/issues/" in parsed.path or "/discussions/" in parsed.path
            else "upstream"
        )
    return None


class PageText(HTMLParser):
    def __init__(self, *, paragraph_text=False):
        super().__init__()
        self.parts, self.skip = [], 0
        self.paragraph_text = paragraph_text

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer"}:
            self.skip += 1
        if tag in {"p", "div", "pre", "h1", "h2", "h3", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer"}:
            self.skip = max(0, self.skip - 1)
        if tag in {"p", "div", "pre", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(
                re.sub(r"\s+", " ", data) if self.paragraph_text else data
            )


def headless_camera_question(query):
    question = query.split("\n引用内容（仅供理解问题，不是用户环境声明）：", 1)[0]
    return bool(
        re.search(r"(?<![A-Za-z0-9])K1(?![A-Za-z0-9])", question, re.I)
        and re.search(r"相机|摄像头", question)
        and re.search(r"无桌面|没有桌面|SSH", question, re.I)
        and re.search(r"区分|显示失败|没有画面|无画面", question)
        and not re.search(r"ROS|OpenHarmony|Buildroot", question, re.I)
    )


def overview_product(query):
    """Recognize only broad product introductions, not peripheral questions."""
    query = query.split("\n用户明确提供的环境：", 1)[0].lower()
    residual = re.sub(
        r"介绍一下|了解一下|有什么特点|有哪些特点|是什么|怎么样|进迭时空|spacemit|"
        r"帮我|给我|简单|简要|介绍|了解|讲讲|说说|一下|平台|芯片|产品|简介|概述|的特点|请|[\s。？！?!]",
        "",
        query,
    )
    return residual if re.fullmatch(r"k[13]", residual) else ""


def overview_platform_comparison(query):
    """Recognize a broad K1/K3 positioning comparison, not technical questions."""
    question = query.split("\n用户明确提供的环境：", 1)[0]
    if not (
        re.search(r"(?<![a-z0-9])k1(?![a-z0-9])", question, re.I)
        and re.search(r"(?<![a-z0-9])k3(?![a-z0-9])", question, re.I)
        and re.search(r"区别|对比|比较|定位", question)
        and re.search(r"平台|定位", question)
    ):
        return False
    return not re.search(
        r"SDK|系统|内核|CPU|接口|相机|部署|兼容|程序|模型|LLM|Python|网络|功耗|跑分|性能",
        question,
        re.I,
    )


def supplemental_sources(query, *, deadline, client=None):
    """Free public search is only a lead; read permitted source pages as evidence.

    Search failure is explicit metadata, never a generated substitute. Private
    logs and identity-bearing questions are not sent to a public search engine.
    Redirects are not followed, so a permitted origin cannot redirect into LAN.
    """
    if len(query) > 240 or re.search(
        r"@|api.?key|token|secret|密码|密钥|/(?:home|Users|tmp)/|\b(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)",
        query,
        re.I,
    ):
        return [], "未向公共搜索发送可能包含私有信息的问题。"
    if time.monotonic() >= deadline:
        return [], "补充检索未能在本轮时限内完成。"
    owns_client = client is None
    client = client or httpx.Client(trust_env=False, follow_redirects=False)
    found = []
    try:
        # Known upstream manuals avoid relying on a search service for common
        # standard-library questions. Read the actual page every time.
        candidates = pip_manual_candidates(query)
        for module in (
            "argparse",
            "asyncio",
            "pathlib",
            "subprocess",
            "venv",
            "threading",
            "multiprocessing",
        ):
            if not pip_manual_task(query) and re.search(
                r"(?<![a-z])" + module + r"(?![a-z])", query, re.I
            ):
                candidates.append(
                    (
                        f"https://docs.python.org/3/library/{module}.html",
                        f"Python {module}",
                    )
                )
        if "externally-managed" in query.lower():
            candidates.append(
                (
                    "https://packaging.python.org/en/latest/specifications/externally-managed-environments/",
                    "Externally Managed Environments",
                )
            )
        if not candidates:
            response = client.get(
                "https://www.bing.com/search?"
                + urlencode(
                    {
                        "q": query
                        + " (site:docs.python.org OR site:docs.ros.org OR site:github.com/spacemit-com OR site:docs.kernel.org)",
                        "format": "rss",
                    }
                ),
                timeout=max(0.2, min(3, deadline - time.monotonic())),
            )
            # Search may redirect between regional Bing endpoints only.
            if response.is_redirect:
                location = response.headers.get("location", "")
                parsed = urlsplit(location)
                if parsed.scheme == "https" and parsed.netloc in {
                    "cn.bing.com",
                    "www.bing.com",
                }:
                    response = client.get(
                        location, timeout=max(0.2, min(3, deadline - time.monotonic()))
                    )
            response.raise_for_status()
            if len(response.content) > 200_000:
                return [], "补充检索返回内容异常。"
            entries = ET.fromstring(response.content).findall("./channel/item")
            candidates = [
                (e.findtext("link") or "", e.findtext("title") or "")
                for e in entries[:10]
            ]
        for url, title in candidates:
            kind = source_kind(url)
            if not kind or time.monotonic() >= deadline:
                continue
            try:
                with client.stream(
                    "GET", url, timeout=max(0.2, min(3, deadline - time.monotonic()))
                ) as page:
                    page.raise_for_status()
                    chunks, size = [], 0
                    for chunk in page.iter_bytes():
                        size += len(chunk)
                        if size > 700_000 or time.monotonic() >= deadline:
                            break
                        chunks.append(chunk)
                parser = PageText(paragraph_text=bool(pip_manual_task(query)))
                parser.feed(b"".join(chunks).decode("utf-8", errors="replace"))
                blocks = [
                    p.strip() for p in "".join(parser.parts).split("\n") if p.strip()
                ]
                q = set(tokens(query))
                ranked = sorted(
                    range(len(blocks)),
                    key=lambda n: len(q & set(tokens(blocks[n]))),
                    reverse=True,
                )
                positions = {0, 1}
                for n in ranked[:8]:
                    positions.update(range(max(0, n - 1), min(len(blocks), n + 2)))
                selected, size = [], 0
                for n in sorted(positions):
                    if n < len(blocks) and size + len(blocks[n]) <= 5000:
                        selected.append(blocks[n])
                        size += len(blocks[n])
                content = "\n".join(selected)
                if pip_manual_task(query):
                    content = "\n".join(manual_blocks(url, blocks))
                    if len(content) > 5000:
                        continue
                if len(content) < (80 if pip_manual_task(query) else 100):
                    continue
                found.append(
                    {
                        "title": title or urlsplit(url).hostname,
                        "content": content,
                        "heading": "原文章节",
                        "path": url,
                        "page_url": url,
                        "source_kind": kind,
                        "fetched_at": "",
                        "updated_at": "",
                        "verified": True,
                    }
                )
            except (httpx.HTTPError, ValueError):
                continue
            if len(found) == 2:
                break
        return found, "" if found else "补充检索未取得可核对的原文。"
    except (httpx.HTTPError, ET.ParseError, ValueError):
        return [], "补充检索暂不可用。"
    finally:
        if owns_client:
            client.close()


_APPLICATION_SCOPE = re.compile(
    r"摄像头|相机|camera|uvc|v4l2|opencv|jupyter|thonny|gpio|onnx|"
    r"语音|图像|视觉|推理|大模型|机器人|(?<![a-z0-9])(?:ros2?|llm|asr|tts|ollama|pytorch|tensorflow)(?![a-z0-9])",
    re.I,
)
_VIRTUAL_ENVIRONMENT = re.compile(
    r"虚拟环境|(?<![a-z])(?:venv|virtualenv|virtual[ -]+environment)(?![a-z])", re.I
)
_GENERAL_ENV_GUIDE = re.compile(
    r"(?:(?:\d+(?:\.\d+)*[.、]?\s*)|(?:K[13]|Bianbu|Linux)\s*)*"
    r"(?:Python|venv|virtualenv|uv|conda|virtual\s+environment|虚拟环境)"
    r"(?:\s|使用|用户|开发|编程|环境|搭建|配置|指南|教程|入门|基础|安装|管理|操作|手册|介绍|及|和|与|包|工具|版本)*",
    re.I,
)


def generic_python_environment(query):
    """A general environment operation, without an application-specific task."""
    question = query.split("\n用户明确提供的环境：", 1)[0]
    question = question.split("\n引用内容（", 1)[0]
    return bool(_VIRTUAL_ENVIRONMENT.search(question)) and not bool(
        _APPLICATION_SCOPE.search(question)
    )


def environment_section(query, item):
    """Keep environment instructions in general manuals and the requested method.

    A camera tutorial may contain valid venv commands, but it is the wrong
    scope for a general Python question. Check the complete heading ancestry
    so an unrequested uv/Jupyter subsection cannot masquerade as a general
    'create environment' section. Application questions use normal retrieval.
    """
    scope = item["title"] + " / " + item["heading"]
    if not _GENERAL_ENV_GUIDE.fullmatch(item["title"].strip()):
        return False
    if _APPLICATION_SCOPE.search(scope):
        return False
    for tool in ("uv", "conda"):
        pattern = r"(?<![a-z])" + tool + r"(?![a-z])"
        if re.search(pattern, scope, re.I) and not re.search(pattern, query, re.I):
            return False
    return bool(_VIRTUAL_ENVIRONMENT.search(item["heading"])) or bool(
        re.search(
            r"(?:^|\n)\s*source\s+[^\n]+/bin/activate\s*(?:\n|$)",
            item.get("content", ""),
        )
    )


def search_sources(query, *, index=INDEX, external=False, deadline=None):
    # These general tool questions need upstream semantics. Incidental local
    # install examples previously suppressed fallback with misleading answers.
    # Keep the privacy gate, explicit opt-out and existing total time budget.
    if external and pip_manual_task(query):
        extra, warning = supplemental_sources(
            query, deadline=deadline or time.monotonic() + 6
        )
        complete = pip_manual_answer(query, extra) is not None
        return {
            "backend": "independent",
            "query": query,
            "sources": extra if complete else [],
            "warning": warning
            if complete
            else warning or "工具上游资料不完整，暂不能确认具体方法。",
            "last_sync": "",
        }
    # Product/version words identify scope, not topic. A query mentioning
    # model zoo must not be satisfied by an unrelated page containing 平台.
    topic_query = query.split("\n用户明确提供的环境：", 1)[0]
    topic_query = re.sub(
        r"(?<![a-z0-9])(?:bianbu|ubuntu|python)\s*v?\d+(?:\.\d+)*",
        lambda m: re.match(r"[a-z]+", m[0])[0],
        topic_query.lower(),
    )
    words = re.findall(r"[a-z][a-z0-9]*(?:[._+-][a-z0-9]+)*", topic_query)
    generic = {
        "spacemit",
        "bianbu",
        "k1",
        "k3",
        "muse",
        "pi",
        "pro",
        "v",
        "linux",
        "ubuntu",
        "risc-v",
        "riscv",
    }
    focus = list(
        dict.fromkeys(
            w for w in words if w not in generic and not re.fullmatch(r"v?\d+", w)
        )
    )
    if "zoo" in focus:
        focus = ["zoo", "modelzoo", "model_zoo", "model-zoo"]
    elif "wheel" in focus or "whl" in focus:
        focus = ["python", "pypi", "wheel", "whl"]
    elif focus == ["llm"]:
        focus += ["llama.cpp", "ollama", "llm-sdk"]
    elif len(focus) > 1:
        specific = [
            word
            for word in focus
            if word not in {"python", "pip", "ai", "spacemit-asr"}
        ]
        if specific:
            focus = specific
    # Inspection questions need tools and procedure evidence, not incidental
    # ABI package names or startup pages. Keep application-specific queries
    # (e.g. ROS networking) on their existing topic route.
    if (
        focus == ["abi"]
        and re.search(r"确认|检查|核对|查看|兼容", topic_query)
        and not re.search(r"内核|安全启动|签名|secure.?boot", topic_query)
    ):
        focus += ["mabi", "readelf"]
    elif (
        not focus
        and "bianbu" in topic_query
        and "联网" in topic_query
        and re.search(r"无法|失败|排查|接口|路由", topic_query)
    ):
        focus = ["联网", "ifconfig"]
    if (
        "bianbu" in topic_query
        and "内核版本" in topic_query
        and re.search(r"查看|查询|确认|核对", topic_query)
        and not re.search(r"安装|切换|升级|编译|驱动|ROS|ros|OpenHarmony", topic_query)
    ):
        focus = ["uname"]
    environment_task = generic_python_environment(query)
    section_filter = None
    if (
        "bianbu" in topic_query
        and "内核版本" in topic_query
        and re.search(r"查看|查询|确认|核对", topic_query)
        and not re.search(r"安装|切换|升级|编译|驱动|ROS|ros|OpenHarmony", topic_query)
    ):
        # Buildroot peripheral RT setup is not runtime Bianbu inspection.
        # Keep other Bianbu evidence available for release-version methods.
        section_filter = lambda item: item["path"].startswith("software/SDK/bianbu/")
    if environment_task:
        # Chinese '虚拟环境' is an intent, not just a generic Python keyword.
        # Focus before ranking so application tutorials cannot occupy the
        # per-document result slots or displace the actual environment steps.
        focus = list(dict.fromkeys(focus + ["venv", "virtualenv", "虚拟"]))
        requested_methods = re.findall(
            r"(?<![a-z])(?:uv|venv|virtualenv|conda)(?![a-z])", topic_query
        )
        if requested_methods:
            focus = list(dict.fromkeys(requested_methods))
        section_filter = lambda item: environment_section(topic_query, item)
    expanded = query + " " + " ".join(focus)
    result = retrieve(
        expanded,
        path=index,
        limit=20,
        prefer_competition=False,
        required_terms=focus or None,
        section_filter=section_filter,
        include_full_pages=not environment_task,
    )
    sources, seen = [], set()
    source_limit = (
        1
        if environment_task and not re.search(r"比较|对比|区别|compare", topic_query)
        else 2
    )
    # Keep a few complete relevant sections, including adjacent source lines
    # for script provenance and commands. Group sections by document.
    with (
        sqlite3.connect(f"file:{index}?mode=ro", uri=True)
        if index.exists()
        else _EmptyDB() as db
    ):
        if headless_camera_question(query) and db is not None:
            row = db.execute(
                "SELECT path,title,page_url,updated_at,fetched_at,source_kind,body "
                "FROM documents WHERE path=? AND source_kind='official'",
                ("software/SDK/bianbu/user_guide/LXQt/Camera_FAQ.md",),
            ).fetchone()
            if row:
                source = dict(
                    zip(
                        (
                            "path",
                            "title",
                            "page_url",
                            "updated_at",
                            "fetched_at",
                            "source_kind",
                            "content",
                        ),
                        row,
                    )
                )
                start = source["content"].find(
                    "### Q：执行最后一步启动命令后，日志显示正常，但屏幕没有画面，怎么办？"
                )
                if start >= 0:
                    source["content"] = source["content"][start:].split("\n---", 1)[0]
                    source.update(
                        heading="日志正常但无预览画面",
                        verified=not bool(result.get("warning")),
                    )
                    return {
                        "backend": "independent",
                        "query": query,
                        "sources": [source],
                        "warning": result.get("warning", ""),
                        "last_sync": result.get("last_sync", ""),
                    }
        if overview_platform_comparison(query) and db is not None:
            # General platform positioning is not supported by timer/driver
            # chapters. Use two canonical product overviews and let the answer
            # state that the comparison is limited to these examples.
            paths = (
                "ecohardware/k3_series/k3_pico/root_overview.md",
                "ecohardware/k1_series/k1_muse_pi_pro/root_overview.md",
            )
            comparison_sources = []
            for path in paths:
                row = db.execute(
                    "SELECT path,title,page_url,updated_at,fetched_at,source_kind,body "
                    "FROM documents WHERE path=? AND source_kind='official'",
                    (path,),
                ).fetchone()
                if not row or len(row[-1]) > 8000:
                    comparison_sources = []
                    break
                source = dict(
                    zip(
                        (
                            "path",
                            "title",
                            "page_url",
                            "updated_at",
                            "fetched_at",
                            "source_kind",
                            "content",
                        ),
                        row,
                    )
                )
                source.update(
                    heading=source["title"], verified=not bool(result.get("warning"))
                )
                comparison_sources.append(source)
            if len(comparison_sources) == 2:
                return {
                    "backend": "independent",
                    "query": query,
                    "sources": comparison_sources,
                    "warning": result.get("warning", ""),
                    "last_sync": result.get("last_sync", ""),
                }
        product = overview_product(query)
        if product and db is not None:
            # Use the product's canonical, current source document for an
            # introduction. Keyword frequency in a Wi-Fi/power-debug chapter
            # cannot establish that it is an overview of the chip itself.
            row = db.execute(
                "SELECT path,title,page_url,updated_at,fetched_at,source_kind,body "
                "FROM documents WHERE path=? AND source_kind='official'",
                (f"hardware/key_stone/{product}/{product}_docs/root_overview.md",),
            ).fetchone()
            if row and len(row[-1]) <= 8000:
                source = dict(
                    zip(
                        (
                            "path",
                            "title",
                            "page_url",
                            "updated_at",
                            "fetched_at",
                            "source_kind",
                            "content",
                        ),
                        row,
                    )
                )
                source.update(
                    heading=f"{product.upper()} 产品简介",
                    verified=not bool(result.get("warning")),
                )
                return {
                    "backend": "independent",
                    "query": query,
                    "sources": [source],
                    "warning": result.get("warning", ""),
                    "last_sync": result.get("last_sync", ""),
                }
        ordered = sorted(
            result.get("results", []), key=lambda item: item["source_kind"] == "faq"
        )
        if re.search(r"赛题|评分|赛事|比赛|固定测试集|pp128|tg128", query, re.I):
            event = [
                item
                for item in ordered
                if item["source_kind"] in {"event_rules", "event_supplement"}
            ]
            if event:
                ordered = event
        for item in ordered:
            if item["path"] in seen:
                continue
            if sources and item["score"] < sources[0]["score"] * 0.5:
                continue
            seen.add(item["path"])
            siblings = [s for s in result["results"] if s["path"] == item["path"]]
            if environment_task:
                # Installation, creation and activation may be separate child
                # headings. Complete this manual's relevant steps instead of
                # taking only the two highest-scoring (often creation) blocks.
                sections = db.execute(
                    "SELECT heading,content,ordinal FROM sections WHERE document_id=? ORDER BY ordinal",
                    (item["document_id"],),
                ).fetchall()
                siblings = [
                    dict(item, heading=heading, content=content, ordinal=ordinal)
                    for heading, content, ordinal in sections
                    if section_filter(dict(item, heading=heading, content=content))
                ]
            parts = [item.get("overview", "")]
            if (
                re.search(r"指南|入门|如何使用|怎么使用", query)
                and item["source_kind"] == "official"
            ):
                introductory = db.execute(
                    "SELECT heading,content FROM sections WHERE document_id=? ORDER BY ordinal LIMIT 7",
                    (item["document_id"],),
                ).fetchall()
                parts.extend(
                    heading + "\n" + content
                    for heading, content in introductory
                    if section_filter is None
                    or section_filter(dict(item, heading=heading, content=content))
                )
            for sibling in siblings if environment_task else siblings[:2]:
                parts.append(sibling["heading"] + "\n" + sibling["content"])
                adjacent = db.execute(
                    "SELECT heading,content FROM sections WHERE document_id=? AND ordinal BETWEEN ? AND ? ORDER BY ordinal",
                    (
                        sibling["document_id"],
                        sibling["ordinal"] - 1,
                        sibling["ordinal"] + 1,
                    ),
                ).fetchall()
                for heading, content in adjacent:
                    same_section = heading == sibling["heading"] or sibling[
                        "heading"
                    ].startswith(heading + " / ")
                    if (
                        same_section
                        and (
                            section_filter is None
                            or section_filter(
                                dict(item, heading=heading, content=content)
                            )
                        )
                        and content not in "\n".join(parts)
                        and len("\n".join(parts)) + len(content) < 6500
                    ):
                        parts.append(heading + "\n" + content)
            # Keep complete blocks. Never cut a command at a character limit.
            complete, size = [], 0
            for part in parts:
                if part and part not in complete and size + len(part) <= 8000:
                    complete.append(part)
                    size += len(part)
            source = dict(
                item,
                content="\n\n".join(complete),
                verified=not bool(result.get("warning")),
            )
            sources.append(source)
            if len(sources) >= source_limit:
                break
    warning = result.get("warning", "")
    if external and not sources:
        extra, note = supplemental_sources(
            query, deadline=deadline or time.monotonic() + 6
        )
        sources.extend(extra)
        warning = " ".join(x for x in (warning, note) if x)
    return {
        "backend": "independent",
        "query": query,
        "sources": sources,
        "warning": warning,
        "last_sync": result.get("last_sync", ""),
    }


class _EmptyDB:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False
