"""Remove QQ addressing metadata before classifying or searching a question."""

import re
from dataclasses import dataclass

_MENTION = r"(?:<@!?[A-Za-z0-9_-]{1,128}>|&lt;@!?[A-Za-z0-9_-]{1,128}&gt;)"
_LEADING = re.compile(r"^(?:" + _MENTION + r"\s*)+")
_TRAILING = re.compile(r"(?:\s*" + _MENTION + r")+$")
_DISPLAY_NAME = re.compile(r"^@spacemit-bot(?![A-Za-z0-9_-])\s*", re.I)
_QUOTED_BODY = re.compile(r"\n\s*\n(?=" + _MENTION + r"|@spacemit-bot(?:\s|$))", re.I)


@dataclass(frozen=True)
class InboundQuestion:
    text: str
    quoted: str = ""


def parse_question(value: str) -> InboundQuestion:
    """Separate QQAdapter's quote envelope from the author's new message.

    Group replies retain an @bot token after the blank-line delimiter. Use
    that boundary so paragraphs in either the quote or body survive. A quote
    without an addressed body stays reference-only: guessing a boundary would
    turn the quoted speaker's environment into the current user's facts.
    """
    text = str(value or "").strip()
    quoted = ""
    if text.startswith("[Quoted message]:"):
        boundaries = list(_QUOTED_BODY.finditer(text))
        if not boundaries:
            return InboundQuestion("", text.removeprefix("[Quoted message]:").strip())
        boundary = boundaries[-1]
        quoted = text[: boundary.start()].removeprefix("[Quoted message]:").strip()
        text = text[boundary.end() :]
    text = _LEADING.sub("", text)
    text = _TRAILING.sub("", text).strip()
    return InboundQuestion(_DISPLAY_NAME.sub("", text).strip(), quoted)


def question_text(value: str) -> str:
    """Strip edge @bot tokens, preserving question text, code and addresses.

    QQ group messages reach the profile hook as ``<@openid> question``;
    these IDs are routing data and must never become retrieval keywords.
    Only edge tokens are removed, not arbitrary email/SSH addresses or
    quoted mention syntax inside the question.
    """
    return parse_question(value).text
