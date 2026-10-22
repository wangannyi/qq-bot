"""Validate evidence-bound answers and render one compact QQ message."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from support_commands import command_lines


class InvalidAnswer(ValueError):
    """A model response cannot be safely presented as a grounded answer."""


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def safe_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and not re.search(r"[\s<>\x00-\x1f]", url)
    )


def _text(value, limit=180):
    if isinstance(value, str):
        # Formatting mistakes need not discard an otherwise grounded answer.
        value = re.sub(r"`([^`\n]+)`", r"\1", value)
        value = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", value)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InvalidAnswer("invalid_text")
    if re.search(r"[\r\n`*#]|<[a-z/!]|\[[^\]]*\]\(", value, re.I):
        raise InvalidAnswer("unexpected_markup")
    return value.strip()


def validate_answer(payload: dict, sources: list[dict]) -> dict:
    """Require verbatim evidence spans; commands must be whole source lines.

    This establishes provenance, not a claim that string matching can prove
    semantic correctness. Real question evaluation remains necessary.
    """
    if not isinstance(payload, dict):
        raise InvalidAnswer("invalid_schema")
    status = payload.get("status")
    if status not in {"answered", "partial", "clarify", "insufficient"}:
        raise InvalidAnswer("invalid_status")
    points = payload.get("points", [])
    if not isinstance(points, list) or len(points) > 3:
        raise InvalidAnswer("too_many_points")
    follow_up = payload.get("follow_up", "")
    if not isinstance(follow_up, str):
        raise InvalidAnswer("invalid_follow_up")
    if follow_up:
        follow_up = _text(follow_up, 100)
    cited = []

    def statement(value):
        if not isinstance(value, dict):
            raise InvalidAnswer("invalid_statement")
        text = _text(value.get("text"))
        validate_health_inference(text)
        validate_camera_inference(text)
        spans = value.get("evidence", [])
        if not isinstance(spans, list) or not 1 <= len(spans) <= 3:
            raise InvalidAnswer("missing_evidence")
        selected = []
        for span in spans:
            if not isinstance(span, dict):
                raise InvalidAnswer("invalid_evidence")
            sid, quote = span.get("source_id"), span.get("quote")
            if type(sid) is not int or not 0 <= sid < len(sources):
                raise InvalidAnswer("unknown_source")
            if not isinstance(quote, str) or len(plain(quote)) < 8:
                raise InvalidAnswer("short_evidence")
            if plain(quote) not in plain(sources[sid]["content"]):
                raise InvalidAnswer("quote_not_in_source")
            selected.append(sid)
            if sid not in cited:
                cited.append(sid)
        # Service endpoints may be essential to a short procedure. Permit
        # only exact source-owned URLs, never a model-invented link.
        for url in re.findall(r"https?://[a-zA-Z0-9./_~%?#=&:+-]+", text):
            if not any(url in sources[sid]["content"] for sid in selected):
                raise InvalidAnswer("unproven_url")
        command = value.get("command", "")
        if not isinstance(command, str):
            raise InvalidAnswer("invalid_command")
        if command:
            if len(command) > 240 or re.search(r"[\r\n`<]|>(?!=)", command):
                raise InvalidAnswer("invalid_command")
            # Never turn a source example into an invented or truncated command.
            lines = [
                line.strip()
                for sid in selected
                for line in command_lines(sources[sid]["content"])
            ]
            if command.strip() not in lines:
                raise InvalidAnswer("command_not_in_source")
            if re.search(r"\bpip(?:3)? config set \S+\s*$", command):
                raise InvalidAnswer("incomplete_command")
        return {"text": text, "command": command.strip(), "evidence": spans}

    if status in {"answered", "partial"}:
        conclusion = statement(payload.get("conclusion"))
        checked = [statement(point) for point in points]
        commands = {conclusion["command"]} if conclusion["command"] else set()
        for point in checked:
            if point["command"] in commands:
                point["command"] = ""
            elif point["command"]:
                commands.add(point["command"])
        if status == "partial" and not follow_up:
            raise InvalidAnswer("missing_limitation")
        if status == "answered" and follow_up:
            raise InvalidAnswer("unexpected_follow_up")
    else:
        # An ungrounded explanation supplied by a model is never published.
        conclusion, checked = None, []
        if status == "clarify" and not follow_up:
            raise InvalidAnswer("missing_question")
    body_size = sum(
        len(p["text"]) + len(p["command"])
        for p in ([conclusion] if conclusion else []) + checked
    )
    if body_size + len(follow_up) > 450:
        raise InvalidAnswer("answer_too_long")
    linked = [sid for sid in cited if safe_url(sources[sid].get("page_url", ""))]
    if len(linked) > 2:
        raise InvalidAnswer("too_many_citations")
    return {
        "status": status,
        "conclusion": conclusion,
        "points": checked,
        "follow_up": follow_up,
        "source_ids": cited,
    }


def validate_health_inference(text: str) -> None:
    """Block a known invalid inference, not a general semantic proof.

    A failed health probe alone cannot establish that a process is stopped.
    Keep explicit uncertainty and negation, including quoted bad conclusions
    in an explanation of why they are invalid.
    """
    if not re.search(r"health|健康检查|健康接口", text, re.I):
        return
    for sentence in re.split(r"[。！？；;]", text):
        for match in re.finditer(
            r"(?:异常|失败|超时|不通|无响应)[，,\s]*"
            r"(?:(?:就|即|则|便)(?:是|说明|表示|证明)?|说明|表示|证明|意味着|可以?判定|可以?确定)[，,\s]*"
            r"(?:服务|进程|llama-server)(?:一定|必定|就是|已经|尚|还|已|\s)*"
            r"(?:未启动|没有启动|没启动|未起|没起|未运行|没有运行|停止|退出|关闭|宕机)",
            sentence,
            re.I,
        ):
            prefix = sentence[: match.start()]
            suffix = sentence[match.end() :]
            if re.search(
                r"不能|不可|无法|不应|不代表|并非|不等于|不意味着|不能说明", prefix
            ):
                continue
            if re.search(
                r"(?:说法|推断|判断|结论).{0,4}(?:不成立|错误|不可靠|不受支持)", suffix
            ):
                continue
            raise InvalidAnswer("unsupported_service_down_inference")


def validate_camera_inference(text: str) -> None:
    """Reject the known topic-presence to capture-failure inference."""
    for sentence in re.split(r"[。！？；;]", text):
        if not (
            re.search(r"话题.{0,8}(?:存在|可见)", sentence)
            and re.search(r"无图像|没有图像", sentence)
        ):
            continue
        for match in re.finditer(
            r"(?:说明|证明|意味着|判定|确定).{0,20}(?:采集|帧传递).{0,24}"
            r"(?:失败|异常|未产出|没有产出|未产生|没有产生)",
            sentence,
        ):
            prefix = sentence[: match.start()]
            suffix = sentence[match.end() :]
            if re.search(r"不能|不可|无法|不应|不代表|并非|不等于|可能|尚未", prefix):
                continue
            if re.search(
                r"(?:说法|推断|判断|结论).{0,4}(?:不成立|错误|不可靠|不受支持)", suffix
            ):
                continue
            raise InvalidAnswer("unsupported_camera_capture_inference")


def render_answer(answer: dict, sources: list[dict], *, markdown=True) -> str:
    """Same content in Markdown and plain text; URLs are never discarded."""
    status = answer["status"]
    if status == "clarify":
        return "待补充：" + answer["follow_up"]
    if status == "insufficient":
        return "资料不足：目前检索到的资料不足以可靠回答这个问题，暂不提供推测性结论。"
    conclusion = answer["conclusion"]
    heading = conclusion["text"]
    parts = [(f"结论：**{heading}**" if markdown else "【重点】" + heading)]
    if conclusion.get("command"):
        parts.append(conclusion["command"])
    for i, point in enumerate(answer["points"], 1):
        part = f"{i}. {point['text']}"
        if point.get("command"):
            part += "\n" + point["command"]
        parts.append(part)
    if answer.get("follow_up"):
        label = "待确认" if status == "partial" else "待补充"
        parts.append(f"{label}：{answer['follow_up']}")
    refs, seen = [], set()
    for sid in answer["source_ids"]:
        item = sources[sid]
        url = item.get("page_url", "")
        if not safe_url(url) or url in seen:
            continue
        seen.add(url)
        title = re.sub(r"[\[\]()*`<>\r\n]", "", item["title"])[:65]
        kind = item.get("source_kind")
        if kind == "community":
            title = "社区经验·" + title
        elif kind == "upstream":
            title = "上游文档·" + title
        elif kind == "faq":
            title = "已审核 FAQ·" + title
        refs.append(f"[🔗{title}]({url})" if markdown else f"{title}\n{url}")
    if refs:
        parts.append("\n".join(refs))
    elif any(
        sources[sid].get("source_kind") in {"event_rules", "event_supplement"}
        for sid in answer["source_ids"]
    ):
        parts.append("依据：组织方赛题及补充说明。")
    return "\n\n".join(parts)
