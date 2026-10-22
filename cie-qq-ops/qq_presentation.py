"""Small, deterministic QQ Custom Markdown presentation helpers.

The answer pipeline validates factual content as plain text first.  This module
only adds presentation around known structural lines afterwards, so enabling
QQ's newer Custom Markdown support cannot change a technical conclusion.

Keep the surface deliberately narrow: QQ documents headings, emphasis, links,
ordered lists and block quotes for C2C/group Custom Markdown.  Code fences and
tables are intentionally not introduced because they remain awkward on a
phone-sized conversation view.
"""
from __future__ import annotations

import re

_METHOD_HEADER = re.compile(r"(?m)^方法([一二三四])：([^\n*#]+)$")
_NUMBERED_STEP = re.compile(r"(?m)^(\d+)、\s*")
_NEEDS_HUMAN = re.compile(r"(?m)^需要人工确认：")
_WAITING_FOR = re.compile(r"(?m)^待补充：")
_EVENT_CHOICE = re.compile(r"(?m)^赛事选择（依据组织方赛题说明）：$")
_REFERENCES = re.compile(r"(?m)^参考文档：$")


def render_reply(content: str) -> str:
    """Add safe, compact Custom Markdown structure to a checked reply.

    The function is idempotent for the forms it emits.  It never creates a
    code block, table, raw URL or new factual wording; all links are supplied
    by the source-verification layer before this function is called.
    """

    text = str(content or "").strip()
    if not text:
        return ""

    text = _METHOD_HEADER.sub(
        lambda match: f"## 方法{match.group(1)}：**{match.group(2).strip()}**",
        text,
    )
    text = _EVENT_CHOICE.sub(
        "## 🎯 赛事选择\n**依据组织方赛题说明**",
        text,
    )
    text = _REFERENCES.sub("## 📚 参考文档", text)
    text = _NEEDS_HUMAN.sub("> ⚠️ **需要人工确认**：", text)
    text = _WAITING_FOR.sub("⚠️ **待补充**：", text)
    # QQ Custom Markdown documents ordered lists with ``1.`` syntax.  The
    # former Chinese comma stayed readable, but never became a real list.
    text = _NUMBERED_STEP.sub(lambda match: f"{match.group(1)}. ", text)
    return text
