#!/usr/bin/env python3
"""Safely stage reviewed-source FAQ candidates for the CIE FAQ repository.

The QQ gateway retains the full reply from the official community assistant in
``website-answers/candidates.sqlite``.  That database is intentionally only a
candidate store: an answer is not an FAQ record until it has been selected from
an *answered* daily question, materialised into the supplied ``faq.md`` and
put in the review queue.

This module has two deliberately separate responsibilities:

* :func:`stage_candidates` is the daily-job hand-off.  It is local,
  deterministic and idempotent.  It only changes the supplied FAQ file and the
  candidate status rows needed to record that hand-off; it never runs git.
* :func:`release_staged_faq` is an explicit, opt-in git release helper.  It
  creates a source branch in a temporary worktree, runs a structural review,
  and can push it for an independent reviewer.  Merging a target branch
  additionally requires an explicit reviewer-approval flag.  Nothing in this
  module calls it automatically.

The split keeps a scheduled report from silently publishing source-generated
text to the remote FAQ repository while still making a missed FAQ hand-off
observable and recoverable.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse

PENDING_REVIEW = "pending_review"
QUEUED_FOR_REVIEW = "queued_for_review"
HANDLED = "handled"

_CANDIDATE_COLUMNS = (
    "fingerprint, question, original_answer, references_json, qq_summary, "
    "fetched_at, status"
)
_REFERENCE_SECTION = re.compile(
    r"^(?:#{1,6}\s*)?(?:\*{1,3}\s*)?"
    r"(?:官方(?:参考)?(?:资料|文档|链接|资源)?(?:链接)?|"
    r"参考(?:资料|文档|链接|资源)?|相关(?:资料|文档|链接)?|"
    r"references?|sources?)"
    r"(?:\s*\*{1,3})?\s*[:：]?\s*$",
    re.IGNORECASE,
)
_INLINE_REFERENCE = re.compile(
    r"^(?:[-*+]|\d+[.、])?\s*(?:\*{1,3}\s*)?"
    r"(?:官方(?:参考)?(?:资料|文档|链接|资源)?(?:链接)?|"
    r"参考(?:资料|文档|链接|资源)?|相关(?:资料|文档|链接)?|"
    r"references?|sources?)"
    r"(?:\s*\*{1,3})?\s*[:：]",
    re.IGNORECASE,
)
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(((?:https?://|mailto:)[^)\s]+)\)")
_HTML_LINK = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"</?[^>]+>")
_URL = re.compile(r"https?://[^\s<>\]\)]+")
_INLINE_CODE = re.compile(r"(`+)([^`]*?)\1")
_HTML_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_NAMED_SECRET = re.compile(
    r"(?i)\b(api[_ -]?key|token|secret|password|passwd)\s*[:=：]\s*\S+"
)
_CONFLICT_MARKER = re.compile(r"^(?:<<<<<<<|=======|>>>>>>>)", re.MULTILINE)
_REF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# Automatic FAQ publication is allowed only for material that can be checked
# against the *current* local developer-document snapshot.  This is separate
# from a capture-time ``verified=True`` URL check: a URL can still resolve
# while the page content has changed or a generated summary has inverted a
# platform limitation.  Keep this path module-local so tests can substitute a
# disposable, minimal snapshot without touching the production corpus.
from support_paths import INDEX as OFFICIAL_DOCS_INDEX

_K3_TOKEN = re.compile(r"(?<![a-z0-9])k3(?![a-z0-9])", re.IGNORECASE)
_POSITIVE_ACCELERATION = re.compile(
    r"(?:支持|享受|可用|可使用)\s*(?:硬件)?加速|"
    r"自动(?:启用|开启)?\s*(?:硬件)?加速|"
    r"(?:硬件)?加速\s*(?:可用|已(?:开启|启用)|支持)",
    re.IGNORECASE,
)
_NEGATIVE_ACCELERATION = re.compile(
    r"(?:不|无|未|无法|不能|暂不)\s*"
    r"(?:支持|可(?:以)?|能够|能|自动|享受|开启|启用)?\s*(?:硬件)?加速",
    re.IGNORECASE,
)
_ONLY_K3_BIANBU = re.compile(
    r"(?<!不)(?:仅|只|只能|仅限(?:于)?|限于)\s*(?:在\s*)?"
    r"(?:k3\s*)?(?:的\s*)?bianbu(?:\s*lxqt/gnome)?"
    r"(?:\s*(?:环境|系统))?(?:\s*上)?(?:\s*(?:支持|可用|部署))?",
    re.IGNORECASE,
)
_K3_ONLY_BIANBU = re.compile(
    r"(?<![a-z0-9])k3(?![a-z0-9])[^。；;！？!?.\n]{0,30}"
    r"(?<!不)(?:仅|只|只能|仅限(?:于)?|限于)[^。；;！？!?.\n]{0,30}bianbu",
    re.IGNORECASE,
)
_TSINGHUA_PYPI = re.compile(
    r"(?:https?://)?pypi\.tuna\.tsinghua\.edu\.cn(?:/simple)?|"
    r"清华(?:大学)?[^。；;！？!?.\n]{0,32}(?:pypi|pip|源)",
    re.IGNORECASE,
)

# Words that describe the request form rather than its technical subject.  The
# set is intentionally small; aggressive stop-word removal tends to merge
# distinct Chinese technical questions.
_QUESTION_PREFIXES = (
    "请问",
    "想问一下",
    "我想问",
    "请教一下",
    "你好",
    "麻烦问一下",
)
_QUESTION_ALIASES = (
    ("大语言模型", "llm"),
    ("大模型", "llm"),
    ("怎样", "如何"),
    ("怎么", "如何"),
    ("部署方式", "部署"),
    ("配置方法", "配置"),
)
_QUESTION_FILLER = {"如何", "怎么", "请问", "一下", "使用", "部署", "配置", "安装"}
_PLATFORM_TERM = re.compile(
    r"(?:k\d+|muse\s*pi(?:\s*pro)?|bianbu\s*\d*(?:\.\d+)*|"
    r"openharmony\s*\d*(?:\.\d+)*)",
    re.IGNORECASE,
)
_CONTEXT_ONLY_QUESTION = re.compile(
    r"\s*(?:k[13](?:设备|开发板|板子)?|(?:bianbu|openharmony|buildroot)\s*"
    r"\d*(?:\.\d+){0,3}|\d+(?:\.\d+){0,3})\s*[。！.!？?]*\s*$",
    re.IGNORECASE,
)
_SOURCE_ONLY_LINE = re.compile(
    r"^\s*(?:[-*+]\s*)?"
    r"(?:官方(?:参考)?(?:资料|文档|链接|资源)?|"
    r"参考(?:资料|文档|链接|资源)?|"
    r"(?:相关操作)?(?:可)?(?:以)?参考(?:视频教程)?|"
    r"(?:更多)?详情(?:见|请见)?|"
    r"references?|sources?)\s*[:：]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Reference:
    """A canonical, checked official reference retained with a candidate."""

    title: str
    url: str
    updated_at: str = ""
    canonical_path: str = ""


@dataclass(frozen=True)
class Candidate:
    """A full official-source answer captured by ``website_bridge.archive``."""

    fingerprint: str
    question: str
    original_answer: str
    references: tuple[Reference, ...] = ()
    qq_summary: str = ""
    fetched_at: str = ""
    status: str = PENDING_REVIEW
    # These fields were added after the first candidate archive.  New rows use
    # them to bind a QQ message to the exact official-answer capture; legacy
    # rows retain the conservative text-only fallback below.
    source_query: str = ""
    session_hash: str = ""
    question_key: str = ""
    source_type: str = ""


@dataclass(frozen=True)
class HandledQuestion:
    """One answered QQ question selected for the daily FAQ hand-off."""

    timestamp: float
    text: str
    session_id: str = ""
    candidate_question: str = ""
    candidate_timestamp: float = 0


@dataclass(frozen=True)
class StageResult:
    """Outcome of a single materialisation attempt.

    ``added`` means a detailed candidate was appended to the local FAQ source.
    It does *not* mean it has been reviewed, committed, pushed or published.
    """

    added: int = 0
    duplicates: int = 0
    missing: int = 0
    skipped: int = 0
    failed: bool = False
    detail: str = ""
    staged_fingerprints: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "added": self.added,
            "duplicates": self.duplicates,
            "missing": self.missing,
            "skipped": self.skipped,
            "failed": self.failed,
            "detail": self.detail,
            "staged_fingerprints": list(self.staged_fingerprints),
        }


@dataclass(frozen=True)
class StaticReviewResult:
    """Structural review result; factual/source review remains independent."""

    approved: bool
    issues: tuple[str, ...] = ()
    headings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseResult:
    """Explicit git-release result, never produced merely by importing this module."""

    success: bool
    detail: str
    source_branch: str = ""
    source_commit: str = ""
    target_branch: str = ""
    merge_commit: str = ""
    review: StaticReviewResult | None = None


@dataclass(frozen=True)
class BranchReviewResult:
    """Independent-release gate result for one FAQ source branch.

    ``reviewer_approved`` is intentionally not inferred from a static check.
    It is a hand-off point for the separately assigned factual reviewer.
    """

    approved: bool
    detail: str
    static_review: StaticReviewResult | None = None


@dataclass(frozen=True)
class DailyPublishResult:
    """Outcome of an explicit worktree-based daily FAQ release attempt."""

    staged: StageResult
    published: bool = False
    detail: str = ""
    source_branch: str = ""
    source_commit: str = ""
    target_branch: str = ""
    merge_commit: str = ""
    review: BranchReviewResult | None = None
    release_failed: bool = False
    local_checkout_updated: bool = False

    @property
    def added(self) -> int:
        """Flatten the staged count for report renderers."""

        return self.staged.added

    @property
    def duplicates(self) -> int:
        """Flatten the staged deduplication count for report renderers."""

        return self.staged.duplicates

    @property
    def missing(self) -> int:
        return self.staged.missing

    @property
    def skipped(self) -> int:
        return self.staged.skipped

    @property
    def failed(self) -> bool:
        return self.release_failed or self.staged.failed


class FAQSyncError(RuntimeError):
    """A controlled, non-secret-bearing FAQ hand-off error."""


def _object_value(value: object, name: str, default: object = "") -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def normalize_question(value: str) -> str:
    """Create a stable, deliberately conservative FAQ question key."""

    from qq_input import question_text
    text = unicodedata.normalize("NFKC", question_text(value)).casefold().strip()
    # QQ mentions are transport metadata; do not make a bot rename create a
    # second FAQ question.  A user name following an @ is removed only at the
    # beginning, where QQ places mentions.
    text = re.sub(r"^\s*@[^\s:：，,]+\s*", "", text)
    for prefix in _QUESTION_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].lstrip(" ，,：:？?！!")
            break
    for old, new in _QUESTION_ALIASES:
        text = text.replace(old, new)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def _platform_terms(value: str) -> frozenset[str]:
    normalized = normalize_question(value)
    return frozenset(
        re.sub(r"\s+", "", match.group(0).casefold())
        for match in _PLATFORM_TERM.finditer(normalized)
    )


def _question_features(value: str) -> frozenset[str]:
    """Return character bigrams and ASCII words for conservative similarity."""

    normalized = normalize_question(value)
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    bigrams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    # A short CJK token can be a meaningful board/model name on its own.
    if len(chinese) == 1:
        bigrams.add(chinese)
    return frozenset((words | bigrams) - _QUESTION_FILLER)


def questions_equivalent(left: str, right: str) -> bool:
    """Compare FAQ question intent without merging different board platforms.

    This is not an NLP classifier.  It is a conservative guard against obvious
    duplicate FAQ headings such as “K3怎么部署大模型” and “K3 如何部署 LLM”.
    Platform identifiers are treated as hard discriminators before textual
    similarity is considered.
    """

    normalized_left = normalize_question(left)
    normalized_right = normalize_question(right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True

    left_platforms = _platform_terms(normalized_left)
    right_platforms = _platform_terms(normalized_right)
    if left_platforms and right_platforms and left_platforms.isdisjoint(right_platforms):
        return False

    features_left = _question_features(normalized_left)
    features_right = _question_features(normalized_right)
    if not features_left or not features_right:
        return False
    overlap = len(features_left & features_right)
    union = len(features_left | features_right)
    jaccard = overlap / union if union else 0.0
    sequence = SequenceMatcher(a=normalized_left, b=normalized_right).ratio()

    # Both signals are needed for normal phrases; a near-identical normalized
    # question can pass with a slightly lower feature overlap.
    return (sequence >= 0.84 and jaccard >= 0.45) or (
        sequence >= 0.74 and jaccard >= 0.72
    )


def _safe_reference(item: object) -> Reference | None:
    if not isinstance(item, Mapping):
        return None
    # Current bridge records explicit verification.  Old rows predate that
    # field, so accept only a first-party HTTPS URL in the legacy case.
    if item.get("verified") is False:
        return None
    url = str(item.get("url", "")).strip()
    parsed = urlparse(url)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if parsed.scheme != "https" or not host:
        return None
    if host != "spacemit.com" and not host.endswith(".spacemit.com"):
        return None
    title = re.sub(r"\s+", " ", str(item.get("title", "")).strip())
    if not title or title.casefold() in {"此处", "here", "link"}:
        title = str(item.get("canonical_path", "")).strip() or "官方开发者文档"
    if len(title) > 120:
        title = title[:119].rstrip() + "…"
    return Reference(
        title=title,
        url=url,
        updated_at=str(item.get("updated_at", "")).strip(),
        canonical_path=str(item.get("canonical_path", "")).strip(),
    )


def canonical_references(raw: object) -> tuple[Reference, ...]:
    """Keep only explicit, first-party, capture-time-verified references.

    Candidates written before the bridge started recording ``verified`` have no
    evidence that their URL still resolves to the current developer document.
    They are useful as a lead for a human, but must not be presented below an
    FAQ heading named “已核验的官方参考”, nor pass the automated merge gate.
    """

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = []
    if not isinstance(raw, Sequence) or isinstance(raw, (bytes, bytearray, str)):
        return ()

    references: list[Reference] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping) or item.get("verified") is not True:
            continue
        reference = _safe_reference(item)
        if reference is None or reference.url in seen:
            continue
        seen.add(reference.url)
        references.append(reference)
    # ``website_bridge`` already presents references in relevance order.  Do
    # not replace that ranking with an inferred timestamp ordering here.
    return tuple(references)


def load_candidates(candidates_db: Path) -> list[Candidate]:
    """Read the candidate store without changing it.

    A missing or malformed database is an operational failure for the caller,
    rather than a signal to create a new empty database and hide the loss.
    """

    candidates_db = Path(candidates_db)
    if not candidates_db.is_file():
        raise FAQSyncError("FAQ候选库不存在或不是文件")
    try:
        with sqlite3.connect(f"file:{candidates_db}?mode=ro", uri=True) as connection:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(candidates)")}
            optional = tuple(
                name if name in columns else f"'' AS {name}"
                for name in ("source_query", "session_hash", "question_key", "source_type")
            )
            rows = connection.execute(
                f"SELECT {_CANDIDATE_COLUMNS}, {', '.join(optional)} FROM candidates"
            ).fetchall()
    except sqlite3.Error as exc:
        raise FAQSyncError("无法读取FAQ候选库") from exc

    candidates = [
        Candidate(
            fingerprint=str(row[0]),
            question=str(row[1] or ""),
            original_answer=str(row[2] or ""),
            references=canonical_references(row[3]),
            qq_summary=str(row[4] or ""),
            fetched_at=str(row[5] or ""),
            status=str(row[6] or PENDING_REVIEW),
            source_query=str(row[7] or ""),
            session_hash=str(row[8] or ""),
            question_key=str(row[9] or ""),
            source_type=str(row[10] or ""),
        )
        for row in rows
        if str(row[0] or "").strip()
    ]
    # The same question can have newer source material.  Make selection stable
    # and favour the latest fetch, then a deterministic fingerprint tie-break.
    return sorted(candidates, key=lambda item: (item.fetched_at, item.fingerprint), reverse=True)


def _is_reference_boundary(line: str) -> bool:
    stripped = line.strip()
    return bool(_REFERENCE_SECTION.match(stripped) or _INLINE_REFERENCE.match(stripped))


def _redact_secrets(value: str) -> str:
    value = _SECRET.sub("[API_KEY已隐藏]", value)
    return _NAMED_SECRET.sub(lambda match: match.group(1) + "=[已隐藏]", value)


def _markdown_target(match: re.Match[str]) -> str:
    """Return the destination of a Markdown link after decoding entities."""

    return html.unescape(match.group(2)).strip()


def _normalise_command_text(value: str) -> str:
    """Make copied command text executable without hiding its endpoint/URL.

    The community assistant occasionally wraps a download URL in Markdown even
    inside a fenced shell block.  That looks reasonable in a web chat but is
    invalid when pasted into a terminal.  Commands retain their URLs (unlike
    narrative prose) and Markdown wrappers are converted to their target.
    """

    value = html.unescape(value)
    value = _HTML_BREAK.sub("；", value)
    value = _MARKDOWN_LINK.sub(_markdown_target, value)
    value = _HTML_LINK.sub(lambda match: _HTML_TAG.sub("", match.group(1)).strip(), value)
    return _redact_secrets(value)


def _local_endpoint(url: str) -> str | None:
    """Return a copyable display form for a localhost service endpoint."""

    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return None
    display = parsed.netloc + parsed.path
    if parsed.query:
        display += "?" + parsed.query
    return display or host


def _remove_links(value: str) -> str:
    """Remove unreviewed prose links while preserving inline command literals.

    Only the curated, capture-time-verified sources are emitted as clickable
    FAQ references.  Inline code is different: PyPI source URLs and local API
    endpoints are part of commands and must remain copyable.  Tokenising those
    spans first prevents a broad URL scrub from corrupting a valid command.
    """

    value = html.unescape(value)
    code_spans: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        code_spans.append(match.group(1) + _normalise_command_text(match.group(2)) + match.group(1))
        return f"\ue000FAQCODE{len(code_spans) - 1}\ue001"

    value = _INLINE_CODE.sub(stash_code, value)
    value = _HTML_BREAK.sub("；", value)
    value = _HTML_LINK.sub(lambda match: _HTML_TAG.sub("", match.group(1)).strip(), value)

    def prose_link_label(match: re.Match[str]) -> str:
        label = _HTML_TAG.sub("", match.group(1)).strip()
        destination = _markdown_target(match)
        if label.startswith(("http://", "https://")):
            local = _local_endpoint(destination)
            return f"`{local}`" if local else ""
        return label

    value = _MARKDOWN_LINK.sub(prose_link_label, value)

    def prose_url(match: re.Match[str]) -> str:
        local = _local_endpoint(match.group(0))
        return f"`{local}`" if local else ""

    value = _URL.sub(prose_url, value)
    for index, code in enumerate(code_spans):
        value = value.replace(f"\ue000FAQCODE{index}\ue001", code)
    return value


def _is_empty_or_source_only_line(line: str) -> bool:
    """Recognise source-list residue left after a prose URL is removed."""

    if not line.strip():
        return False
    if re.fullmatch(r"\s*#{1,6}\s*", line):
        return True
    plain = _HTML_TAG.sub("", line)
    plain = re.sub(r"[`*_]+", "", plain).strip()
    return bool(_SOURCE_ONLY_LINE.fullmatch(plain))


def sanitize_answer(raw_answer: str) -> str:
    """Retain detailed source prose and commands, not unreviewed reference lists.

    The candidate body belongs in a detailed FAQ entry, but its trailing model
    generated ``参考资料`` list must not become a publication authority.  Links
    are therefore removed from the raw answer and replaced only by the
    canonical checked references retained in :class:`Candidate`.
    """

    text = _THINK_BLOCK.sub("", str(raw_answer or "")).replace("\r\n", "\n")
    lines: list[str] = []
    active_fence = ""
    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        fence = _FENCE.match(raw_line)
        if fence:
            marker = fence.group(1)[0]
            if not active_fence:
                active_fence = marker
            elif marker == active_fence:
                active_fence = ""
            lines.append(raw_line.rstrip())
            continue
        if not active_fence and _is_reference_boundary(raw_line):
            break
        if not active_fence and stripped in {"正在思考中✓", "思考完成✓"}:
            continue
        # Download URLs and endpoint arguments inside a fenced command are
        # executable source detail, not a trailing reference list.  Preserve
        # their destination while removing web-only Markdown wrappers so a
        # reviewed FAQ entry does not turn a complete command into a broken
        # fragment.
        line = (
            _normalise_command_text(raw_line).rstrip()
            if active_fence
            else _redact_secrets(_remove_links(raw_line)).rstrip()
        )
        if not active_fence and _is_empty_or_source_only_line(line):
            continue
        # A candidate answer can have a top-level heading copied from the web
        # assistant.  Keep its hierarchy below the FAQ question heading.
        if not active_fence:
            heading = re.match(r"^(\s*)(#{1,6})(\s+.+)$", line)
            if heading:
                # FAQ questions are level two.  Source answers often start
                # their parallel methods at either H2 or H3, so preserve the
                # section *meaning* without producing distracting H4/H5
                # jumps in the consolidated FAQ.
                depth = 3
                line = heading.group(1) + ("#" * depth) + heading.group(3)
        lines.append(line)

    # Preserve code fences and paragraph spacing while avoiding a large blank
    # tail after a removed reference section.
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    answer = "\n".join(lines)
    answer = re.sub(r"\n{4,}", "\n\n\n", answer)
    return answer.strip()


def _faq_headings(faq_text: str) -> tuple[str, ...]:
    return tuple(match.group(1).strip() for match in _HEADING.finditer(faq_text))


def _archive_question_key(value: str) -> str:
    """Match the stable key stored by ``website_bridge.archive``."""

    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).casefold())


def _session_scope(session_id: str) -> str:
    return hashlib.sha256(str(session_id).encode()).hexdigest() if session_id else ""


def _is_standalone_faq_question(question: str) -> bool:
    """Reject a terse follow-up such as ``Bianbu 4`` as an FAQ heading.

    The full contextual query is saved separately by the bridge; only a
    substantive user question is eligible for an independent FAQ entry.
    """

    return not bool(_CONTEXT_ONLY_QUESTION.fullmatch(str(question)))


def _find_candidate(
    question: HandledQuestion,
    candidates: Iterable[Candidate],
    *,
    statuses: frozenset[str] = frozenset({PENDING_REVIEW}),
) -> Candidate | None:
    """Prefer exact per-session archival correlation over textual fallback."""

    eligible = [
        candidate
        for candidate in candidates
        if candidate.status in statuses
        and candidate.question.strip()
        and candidate.original_answer.strip()
    ]
    session_hash = _session_scope(question.session_id)
    if question.candidate_question and question.candidate_question != question.text:
        # A merged clarification keeps the original FAQ heading, but its
        # archived answer belongs to the final supplement. Require all three
        # correlations; never match another user's bare version number.
        from summary_questions import same_problem
        for candidate in eligible:
            if candidate.session_hash != session_hash or not session_hash:
                continue
            if normalize_question(candidate.question) != normalize_question(question.candidate_question):
                continue
            try:
                captured = datetime.fromisoformat(candidate.fetched_at).timestamp()
            except (ValueError, TypeError):
                continue
            if not 0 <= captured - question.candidate_timestamp <= 300:
                continue
            original = candidate.source_query.split("\n用户明确提供的环境：", 1)[0]
            original = original.split("；本次补充：", 1)[0]
            if same_problem(original, question.text):
                return candidate
        # A different archived turn is not a substitute for this final reply.
        return None
    question_key = _archive_question_key(question.text)
    if session_hash and question_key:
        for candidate in eligible:
            if (
                candidate.session_hash == session_hash
                and candidate.question_key == question_key
            ):
                return candidate
    for candidate in eligible:
        if normalize_question(candidate.question) == normalize_question(question.text):
            return candidate
    for candidate in eligible:
        if questions_equivalent(candidate.question, question.text):
            return candidate
    return None


def _queued_batch_for_questions(
    questions: Iterable[object], candidates_db: Path
) -> tuple[str, ...]:
    """Find queued candidate rows for a deterministically named retry batch."""

    candidates = load_candidates(candidates_db)
    fingerprints: list[str] = []
    seen: set[str] = set()
    for question in _handled_questions(questions):
        if not _is_standalone_faq_question(question.text):
            continue
        candidate = _find_candidate(
            question, candidates, statuses=frozenset({QUEUED_FOR_REVIEW})
        )
        if candidate is not None and candidate.fingerprint not in seen:
            seen.add(candidate.fingerprint)
            fingerprints.append(candidate.fingerprint)
    return tuple(fingerprints)


def _reference_identity(reference: Reference) -> str:
    """A stable document identity across title tweaks and URL query strings."""

    if reference.canonical_path:
        return reference.canonical_path.casefold().strip()
    parsed = urlparse(reference.url)
    return (parsed.netloc + parsed.path).casefold().strip()


def _is_python_guide(reference: Reference) -> bool:
    identity = _reference_identity(reference)
    title = reference.title.casefold()
    return identity.endswith("/development/python.md") or "python 使用指南" in title


def _llm_reference_kind(reference: Reference) -> str:
    identity = _reference_identity(reference)
    title = reference.title.casefold()
    combined = f"{identity} {title}"
    if "ollama" in combined:
        return "ollama"
    if "llama.cpp" in combined or "llamacpp" in combined:
        return "llama.cpp"
    if "llmsdk" in combined or "llm sdk" in combined:
        return "llm-sdk"
    if "speech" in combined or "asr" in combined or "tts" in combined:
        return "speech"
    return ""


def _faq_references(question: str, candidate: Candidate) -> tuple[Reference, ...]:
    """Keep only current official documents that support this FAQ question.

    The raw official-community answer can enumerate every source it happened
    to retrieve.  FAQ readers need the small, topic-specific subset instead of
    a mixed set of stale release notes and adjacent product documents.
    """

    unique: list[Reference] = []
    seen: set[str] = set()
    for reference in candidate.references:
        key = _reference_identity(reference)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(reference)

    context = f"{question}\n{candidate.original_answer}".casefold()
    if "pypi" in context or re.search(r"\bpython\b|python使用", context):
        return tuple(reference for reference in unique if _is_python_guide(reference))

    if "llm" in context or "大模型" in context or "llama" in context:
        # Match the source answer's parallel implementation choices.  The
        # order makes the FAQ easier to scan and excludes Speech SDK unless
        # the question is actually about speech/ASR/TTS.
        selected: list[Reference] = []
        for kind in ("ollama", "llama.cpp", "llm-sdk"):
            reference = next(
                (item for item in unique if _llm_reference_kind(item) == kind),
                None,
            )
            if reference is not None:
                selected.append(reference)
        if selected:
            return tuple(selected)
        if not re.search(r"(?:speech|asr|tts|语音)", context):
            unique = [item for item in unique if _llm_reference_kind(item) != "speech"]

    # Other subjects retain a compact relevance-ranked fallback.  The bridge
    # has already verified these URLs; limiting this to three avoids a FAQ
    # answer acquiring unrelated historical material.
    return tuple(unique[:3])


def _render_entry(question: str, candidate: Candidate) -> str:
    answer = sanitize_answer(candidate.qq_summary if candidate.source_type == "independent_documents"
                             else candidate.original_answer)
    if not answer:
        return ""
    title = re.sub(r"\s+", " ", str(question).replace("\n", " ").strip())
    title = re.sub(r"^@[^\s:：，,]+\s*", "", title)
    title = title.lstrip("#").strip()
    if not title:
        return ""
    references = _faq_references(title, candidate)
    # FAQ publication must never turn a source answer with unproven/irrelevant
    # links into a factual record.  A human-required item remains in the
    # candidate store until the bridge can capture a current official source.
    if not references:
        return ""
    parts = [f"## {title}", "", answer]
    parts.extend(["", "### 已核验的官方参考", ""])
    parts.extend(f"- [{reference.title}]({reference.url})" for reference in references)
    return "\n".join(parts).rstrip() + "\n"


def _write_atomically(path: Path, content: str) -> None:
    """Replace exactly one existing regular file, preserving its mode."""

    if not path.is_file():
        raise FAQSyncError("FAQ目标文件不存在或不是普通文件")
    file_mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, file_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _mark_queued(candidates_db: Path, fingerprints: Sequence[str]) -> None:
    if not fingerprints:
        return
    placeholders = ",".join("?" for _ in fingerprints)
    try:
        with sqlite3.connect(candidates_db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE candidates SET status = ? "
                f"WHERE fingerprint IN ({placeholders}) "
                "AND status IN (?, ?)",
                (QUEUED_FOR_REVIEW, *fingerprints, PENDING_REVIEW, QUEUED_FOR_REVIEW),
            )
    except sqlite3.Error as exc:
        raise FAQSyncError("无法更新FAQ候选审核状态") from exc


def _handled_questions(questions: Iterable[object]) -> list[HandledQuestion]:
    selected: list[HandledQuestion] = []
    seen: set[str] = set()
    for item in questions:
        if str(_object_value(item, "status", "")) != HANDLED:
            continue
        text = str(_object_value(item, "text", "")).strip()
        normalized = normalize_question(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        timestamp_value = _object_value(item, "timestamp", 0.0)
        try:
            timestamp = float(timestamp_value)
        except (TypeError, ValueError):
            timestamp = 0.0
        selected.append(
            HandledQuestion(
                timestamp=timestamp,
                text=text,
                session_id=str(_object_value(item, "session_id", "")),
                candidate_question=str(_object_value(item, "candidate_question", "")),
                candidate_timestamp=float(_object_value(item, "candidate_timestamp", 0) or 0),
            )
        )
    return sorted(
        selected,
        key=lambda value: (value.timestamp, normalize_question(value.text), value.text),
    )


def stage_candidates(
    questions: Iterable[object], candidates_db: Path, faq_path: Path
) -> StageResult:
    """Materialise handled questions into one supplied FAQ file for review.

    No human-required question is selected, and its candidate status is never
    changed.  A candidate changes from ``pending_review`` to
    ``queued_for_review`` only after the FAQ file was atomically written.  If
    updating the database fails, the original FAQ file is restored so a report
    never claims a successful hand-off after a half-completed transaction.
    """

    faq_path = Path(faq_path)
    candidates_db = Path(candidates_db)
    handled = _handled_questions(questions)
    if not handled:
        return StageResult(detail="没有可进入FAQ审核队列的已处理问题")
    try:
        candidates = load_candidates(candidates_db)
        original_faq = faq_path.read_text(encoding="utf-8")
        if not faq_path.is_file():
            raise FAQSyncError("FAQ目标文件不存在或不是普通文件")
    except (OSError, UnicodeError, FAQSyncError) as exc:
        return StageResult(failed=True, detail=f"FAQ同步失败：{type(exc).__name__}")

    headings = list(_faq_headings(original_faq))
    entries: list[str] = []
    fingerprints: list[str] = []
    used_fingerprints: set[str] = set()
    duplicates = 0
    missing = 0
    skipped = 0

    for handled_question in handled:
        question = handled_question.text
        if not _is_standalone_faq_question(question):
            skipped += 1
            continue
        # Once a heading is in the target FAQ it is a duplicate regardless of
        # whether its candidate row has already advanced from pending to a
        # reviewed state.  Check this before candidate selection so queued
        # rows cannot be reused to create another branch.
        if any(questions_equivalent(question, heading) for heading in headings):
            duplicates += 1
            continue
        candidate = _find_candidate(handled_question, candidates)
        if candidate is None:
            missing += 1
            continue
        if candidate.fingerprint in used_fingerprints:
            duplicates += 1
            continue
        used_fingerprints.add(candidate.fingerprint)
        entry = _render_entry(question, candidate)
        if not entry:
            skipped += 1
            continue
        entries.append(entry)
        fingerprints.append(candidate.fingerprint)
        headings.append(question)

    if not entries:
        detail = "FAQ候选已去重" if duplicates else "未找到可归档的官网答复候选"
        if missing:
            detail += f"；{missing} 个已处理问题尚无候选"
        return StageResult(
            duplicates=duplicates,
            missing=missing,
            skipped=skipped,
            detail=detail,
        )

    suffix = "\n\n"
    rendered = original_faq.rstrip() + suffix + "\n".join(entries)
    try:
        _write_atomically(faq_path, rendered)
        try:
            _mark_queued(candidates_db, fingerprints)
        except FAQSyncError:
            # A staged FAQ entry without the matching queue status is not a
            # valid hand-off.  Restore the known pre-stage content before
            # returning the failure to the daily report.
            _write_atomically(faq_path, original_faq)
            raise
    except (OSError, FAQSyncError) as exc:
        return StageResult(
            duplicates=duplicates,
            missing=missing,
            skipped=skipped,
            failed=True,
            detail=f"FAQ同步失败：{type(exc).__name__}",
        )

    return StageResult(
        added=len(entries),
        duplicates=duplicates,
        missing=missing,
        skipped=skipped,
        detail=f"已新增 {len(entries)} 条FAQ候选，等待独立审核",
        staged_fingerprints=tuple(fingerprints),
    )


def static_review_faq(faq_path: Path) -> StaticReviewResult:
    """Run deterministic structural guards before a git release.

    This does not claim to verify technical facts.  The independent FAQ reviewer
    must still open official sources and approve factual claims before a merge.
    """

    path = Path(faq_path)
    issues: list[str] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return StaticReviewResult(False, ("FAQ文件不可读取",))
    if not content.startswith("# "):
        issues.append("缺少顶层Markdown标题")
    if _CONFLICT_MARKER.search(content):
        issues.append("存在Git冲突标记")
    if _SECRET.search(content) or _NAMED_SECRET.search(content):
        issues.append("疑似包含凭据")
    if re.search(r"^\s*#{1,6}\s*$", content, re.MULTILINE):
        issues.append("存在空Markdown标题")
    open_fence = ""
    for line in content.splitlines():
        fence = _FENCE.match(line)
        if not fence:
            continue
        marker = fence.group(1)[0]
        if not open_fence:
            open_fence = marker
        elif marker == open_fence:
            open_fence = ""
    if open_fence:
        issues.append("存在未闭合代码块")
    headings = _faq_headings(content)
    if not headings:
        issues.append("没有FAQ问题标题")
    for index, heading in enumerate(headings):
        if not heading.strip():
            issues.append("存在空FAQ标题")
            continue
        if any(questions_equivalent(heading, prior) for prior in headings[:index]):
            issues.append(f"存在重复或语义重复的问题标题：{heading}")
    # Link quality and factual support are intentionally left to the independent
    # reviewer.  Existing FAQ entries can contain copyable URLs inside commands;
    # a structural gate must not mistake those for a failed daily hand-off.
    return StaticReviewResult(not issues, tuple(issues), headings)


def _git(repo: Path, arguments: Sequence[str], *, timeout: int = 90) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FAQSyncError("FAQ git操作无法完成") from exc
    if completed.returncode != 0:
        # Git can echo remote URLs or remote text.  Do not surface it through a
        # bot/report path; callers receive a stable failure class instead.
        raise FAQSyncError("FAQ git操作失败")
    return completed.stdout.strip()


def _valid_ref_name(value: str) -> bool:
    return bool(_REF_NAME.fullmatch(value)) and not value.startswith(("-", "/"))


def _relative_faq_path(repo: Path, faq_path: Path) -> Path:
    try:
        relative = faq_path.resolve().relative_to(repo.resolve())
    except ValueError as exc:
        raise FAQSyncError("FAQ文件不在目标仓库中") from exc
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise FAQSyncError("FAQ相对路径无效")
    return relative


def deterministic_daily_branch(questions: Iterable[object]) -> str:
    """Return a repeatable, non-personal source branch name for one daily set."""

    handled = _handled_questions(questions)
    material = "\n".join(normalize_question(question.text) for question in handled)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:10]
    if handled:
        latest = max(question.timestamp for question in handled)
        day = datetime.fromtimestamp(latest, timezone.utc).strftime("%Y%m%d")
    else:
        day = "empty"
    return f"bot/faq-{day}-{digest}"


def _candidate_status_snapshot(candidates_db: Path) -> dict[str, str]:
    return {candidate.fingerprint: candidate.status for candidate in load_candidates(candidates_db)}


def _restore_pending_statuses(
    candidates_db: Path, fingerprints: Sequence[str], before: Mapping[str, str]
) -> None:
    """Undo only queue transitions made by an unpublished temporary release."""

    reset = [
        fingerprint
        for fingerprint in fingerprints
        if before.get(fingerprint) == PENDING_REVIEW
    ]
    if not reset:
        return
    placeholders = ",".join("?" for _ in reset)
    try:
        with sqlite3.connect(candidates_db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE candidates SET status = ? "
                f"WHERE fingerprint IN ({placeholders}) AND status = ?",
                (PENDING_REVIEW, *reset, QUEUED_FOR_REVIEW),
            )
    except sqlite3.Error as exc:
        raise FAQSyncError("无法回滚未发布FAQ候选状态") from exc


def _mark_published(candidates_db: Path, fingerprints: Sequence[str]) -> None:
    if not fingerprints:
        return
    placeholders = ",".join("?" for _ in fingerprints)
    try:
        with sqlite3.connect(candidates_db) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE candidates SET status = ? "
                f"WHERE fingerprint IN ({placeholders}) AND status = ?",
                ("published", *fingerprints, QUEUED_FOR_REVIEW),
            )
    except sqlite3.Error as exc:
        raise FAQSyncError("无法标记已发布FAQ候选") from exc


def _verify_clean_target_checkout(repo: Path, target_branch: str) -> None:
    """Refuse a release that would merge over a developer's FAQ checkout."""

    if _git(repo, ("status", "--porcelain")):
        raise FAQSyncError("FAQ仓库工作区不干净，拒绝发布")
    current_branch = _git(repo, ("branch", "--show-current"))
    if current_branch != target_branch:
        raise FAQSyncError("FAQ仓库未检出目标分支，拒绝发布")


def _fast_forward_target_checkout(repo: Path, remote: str, target_branch: str) -> None:
    """Bring the RAG source checkout to the just-published remote target."""

    _verify_clean_target_checkout(repo, target_branch)
    target_ref = f"{remote}/{target_branch}"
    _git(repo, ("fetch", remote, target_branch))
    _git(repo, ("merge", "--ff-only", target_ref))


def _recover_remote_merged_release(
    repo: Path,
    candidates_db: Path,
    questions: Iterable[object],
    *,
    source_branch: str,
    target_branch: str,
    remote: str,
) -> DailyPublishResult | None:
    """Repair candidate state when a prior run merged before a local write.

    A remote push can succeed immediately before a local SQLite write fails or
    the process is interrupted.  The deterministic source branch lets the
    next daily run prove that the branch is already an ancestor of the target,
    mark only its queued questions published, and resume from a truthful
    state instead of creating a duplicate FAQ branch.
    """

    target_ref = f"{remote}/{target_branch}"
    source_ref = f"{remote}/{source_branch}"
    try:
        _git(repo, ("rev-parse", "--verify", source_ref))
    except FAQSyncError:
        return None
    try:
        _git(repo, ("merge-base", "--is-ancestor", source_ref, target_ref))
    except FAQSyncError:
        return None
    try:
        fingerprints = _queued_batch_for_questions(questions, candidates_db)
        _mark_published(candidates_db, fingerprints)
        _fast_forward_target_checkout(repo, remote, target_branch)
    except FAQSyncError as exc:
        return DailyPublishResult(
            StageResult(
                added=len(fingerprints) if "fingerprints" in locals() else 0,
                detail="已检测到远端FAQ合并，正在修复本地状态",
                staged_fingerprints=fingerprints if "fingerprints" in locals() else (),
            ),
            published=True,
            detail=f"FAQ已在远端合并，但本地状态修复失败：{exc}",
            source_branch=source_branch,
            target_branch=target_branch,
            release_failed=False,
        )
    return DailyPublishResult(
        StageResult(
            added=len(fingerprints),
            detail="已恢复已合并FAQ的候选状态",
            staged_fingerprints=fingerprints,
        ),
        published=True,
        detail="已恢复先前成功合并的FAQ发布状态",
        source_branch=source_branch,
        target_branch=target_branch,
        local_checkout_updated=True,
    )


def review_branch(
    repo_path: Path,
    source_branch: str,
    target_branch: str,
    *,
    faq_relative_path: Path = Path("faq.md"),
    remote: str = "origin",
    reviewer_approved: bool = False,
) -> BranchReviewResult:
    """Perform the deterministic half of an independent source-branch review.

    Source correctness needs a person/independent reviewer to check the cited
    official documents.  This function deliberately cannot manufacture that
    judgement: without ``reviewer_approved=True`` it returns a waiting gate
    after structural checks.  A caller can use that explicit flag only after
    the separately assigned reviewer has approved the branch.
    """

    repo = Path(repo_path).resolve()
    relative = Path(faq_relative_path)
    if not all(_valid_ref_name(value) for value in (source_branch, target_branch, remote)):
        return BranchReviewResult(False, "Git分支或远端名称无效")
    if relative.is_absolute() or ".." in relative.parts:
        return BranchReviewResult(False, "FAQ相对路径无效")
    try:
        _git(repo, ("rev-parse", "--verify", source_branch))
        target_ref = f"{remote}/{target_branch}"
        _git(repo, ("rev-parse", "--verify", target_ref))
        changed = _git(repo, ("diff", "--name-only", f"{target_ref}...{source_branch}"))
        changed_files = tuple(line for line in changed.splitlines() if line.strip())
        if changed_files != (str(relative),):
            return BranchReviewResult(False, "审核分支包含FAQ范围外的改动")
        _git(repo, ("diff", "--check", f"{target_ref}...{source_branch}"))
    except FAQSyncError as exc:
        return BranchReviewResult(False, str(exc))

    with tempfile.TemporaryDirectory(prefix="cie-faq-branch-review-") as temporary_root:
        worktree = Path(temporary_root) / "review"
        attached = False
        try:
            _git(repo, ("worktree", "add", "--detach", str(worktree), source_branch))
            attached = True
            static = static_review_faq(worktree / relative)
            if not static.approved:
                return BranchReviewResult(False, "FAQ静态审核未通过", static)
        except FAQSyncError as exc:
            return BranchReviewResult(False, str(exc))
        finally:
            if attached:
                try:
                    _git(repo, ("worktree", "remove", "--force", str(worktree)))
                except FAQSyncError:
                    pass

    if not reviewer_approved:
        return BranchReviewResult(False, "结构检查已通过，等待独立事实审核", static)
    return BranchReviewResult(True, "独立审核已批准", static)


def _explicitly_verified_reference_urls(
    candidates_db: Path, fingerprints: Sequence[str]
) -> set[str]:
    """Read verified references belonging to this exact release batch only."""

    candidates_db = Path(candidates_db)
    if not candidates_db.is_file():
        raise FAQSyncError("FAQ候选库不存在或不是文件")
    if not fingerprints:
        return set()
    placeholders = ",".join("?" for _ in fingerprints)
    try:
        with sqlite3.connect(f"file:{candidates_db}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT references_json FROM candidates "
                f"WHERE fingerprint IN ({placeholders})",
                tuple(fingerprints),
            ).fetchall()
    except sqlite3.Error as exc:
        raise FAQSyncError("无法读取FAQ候选参考资料") from exc
    urls: set[str] = set()
    for (raw,) in rows:
        try:
            references = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(references, Sequence) or isinstance(references, (str, bytes, bytearray)):
            continue
        for item in references:
            if not isinstance(item, Mapping) or item.get("verified") is not True:
                continue
            reference = _safe_reference(item)
            if reference is not None:
                urls.add(reference.url)
    return urls


def _official_nodepath(url: str) -> str | None:
    """Return a canonical developer-document path for an official page URL.

    The FAQ can legitimately cite other first-party pages (for example a
    release download).  This additional gate intentionally applies only to
    the canonical ``/community/document/info?nodepath=...`` URLs: those have
    a corresponding document in the atomically refreshed local corpus and can
    therefore be checked without fetching a page during a daily publication.
    """

    try:
        parsed = urlparse(html.unescape(str(url)))
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (
        host == "spacemit.com" or host.endswith(".spacemit.com")
    ):
        return None
    if parsed.path.rstrip("/") != "/community/document/info":
        return None
    values = parse_qs(parsed.query, keep_blank_values=True).get("nodepath", ())
    if len(values) != 1:
        return None
    nodepath = unquote(values[0]).replace("\\", "/").strip().lstrip("/")
    if not nodepath or any(part in {"", ".", ".."} for part in nodepath.split("/")):
        return None
    return nodepath


def _current_official_documents(
    nodepaths: Iterable[str], *, index_path: Path | None = None
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Load exact current source bodies, returning fail-closed diagnostics.

    A candidate is intentionally rejected when its cited ``nodepath`` is not
    in the current snapshot.  Using an old answer merely because the upstream
    page still opens is the precise stale-source failure this gate prevents.
    """

    paths = tuple(dict.fromkeys(str(path) for path in nodepaths if str(path)))
    if not paths:
        return {}, ()
    snapshot = Path(index_path) if index_path is not None else OFFICIAL_DOCS_INDEX
    if not snapshot.is_file():
        return {}, ("当前官方文档快照不可用，拒绝自动合并",)
    placeholders = ",".join("?" for _ in paths)
    try:
        with sqlite3.connect(f"file:{snapshot.resolve()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT path, body FROM documents "
                f"WHERE path IN ({placeholders})",
                paths,
            ).fetchall()
    except sqlite3.Error:
        return {}, ("当前官方文档快照无法读取，拒绝自动合并",)
    documents = {
        str(path): str(body or "")
        for path, body in rows
        if str(path) and str(body or "").strip()
    }
    missing = tuple(path for path in paths if path not in documents)
    if missing:
        shown = "、".join(missing[:3])
        suffix = "等" if len(missing) > 3 else ""
        return documents, (f"当前官方文档快照缺少：{shown}{suffix}",)
    return documents, ()


def _source_says_k3_ollama_has_no_acceleration(source: str) -> bool:
    return bool(
        re.search(
            r"k3\s*bianbu[^\n]{0,100}(?:不|无|未|无法|不能|暂不)\s*支持\s*加速",
            source,
            re.IGNORECASE,
        )
    )


def _source_says_k3_buildroot_supported(source: str) -> bool:
    return bool(
        re.search(
            r"k3\s*buildroot[^\n]{0,100}(?<!不)支持",
            source,
            re.IGNORECASE,
        )
    )


def _claims_positive_acceleration(text: str) -> bool:
    """Identify an affirmative acceleration claim, not a copied caveat."""

    for sentence in re.split(r"(?<=[。；;！？!?])|\n+", text):
        if "加速" not in sentence:
            continue
        if _NEGATIVE_ACCELERATION.search(sentence):
            continue
        if _POSITIVE_ACCELERATION.search(sentence):
            return True
        if re.search(r"(?:硬件)?加速\s*(?:可用|已开启|自动开启|支持)", sentence, re.I):
            return True
    return False


def _ollama_acceleration_claim_scope(heading: str, body: str) -> str:
    """Keep only text that actually belongs to an Ollama method/statement.

    A K3 deployment FAQ often presents Ollama, llama.cpp and LLM SDK as
    parallel methods.  llama.cpp can correctly describe hardware acceleration
    even though the current K3 Ollama page says otherwise.  Treating an entire
    multi-method answer as an Ollama claim would therefore reject a truthful
    FAQ.  Markdown method sections and sentences that name Ollama provide a
    deterministic, conservative attribution boundary.
    """

    has_method_subsection = any(
        re.match(r"^#{1,6}\s+", line) for line in body.splitlines()
    )
    if "ollama" in heading.casefold() and not has_method_subsection:
        return f"{heading}\n{body}"

    lines = body.splitlines()
    scoped: list[str] = []
    for index, line in enumerate(lines):
        marker = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if marker is None or "ollama" not in marker.group(2).casefold():
            continue
        level = len(marker.group(1))
        end = len(lines)
        for following in range(index + 1, len(lines)):
            next_marker = re.match(r"^(#{1,6})\s+", lines[following])
            if next_marker is not None and len(next_marker.group(1)) <= level:
                end = following
                break
        scoped.append(heading)
        scoped.extend(lines[index:end])

    # A compact one-line answer may not use headings.  In that case, only a
    # sentence which explicitly names Ollama is attributable to it; a generic
    # "supports acceleration" sentence remains for independent review rather
    # than being incorrectly blamed on Ollama.
    for sentence in re.split(r"(?<=[。；;！？!?])|\n+", body):
        if "ollama" in sentence.casefold():
            scoped.append(f"{heading}\n{sentence}")
    return "\n".join(scoped)


def _claims_llama_cpp_only_k3_bianbu(text: str) -> bool:
    return bool(_ONLY_K3_BIANBU.search(text) or _K3_ONLY_BIANBU.search(text))


def _official_source_fidelity_issues(
    heading: str, body: str, urls: Iterable[str]
) -> tuple[str, ...]:
    """Return current-source contradictions for one newly appended FAQ item.

    This is deliberately narrow and deterministic.  It does not pretend to
    prove every generated technical sentence; it blocks the high-impact
    platform/support inversions already observed in production and rejects a
    release whenever an actual official document reference cannot be found in
    the current local snapshot.  References without ``nodepath`` are left to
    the existing capture-time verification gate for backward compatibility.
    """

    nodepaths = tuple(
        dict.fromkeys(
            nodepath
            for url in urls
            if (nodepath := _official_nodepath(url)) is not None
        )
    )
    documents, snapshot_issues = _current_official_documents(nodepaths)
    if snapshot_issues:
        return snapshot_issues

    claim_text = f"{heading}\n{body}"
    ollama_claim_text = _ollama_acceleration_claim_scope(heading, body)
    issues: list[str] = []
    for nodepath, source in documents.items():
        source_lower = source.casefold()
        if (
            _source_says_k3_ollama_has_no_acceleration(source)
            and _K3_TOKEN.search(ollama_claim_text)
            and _claims_positive_acceleration(ollama_claim_text)
        ):
            issues.append("当前Ollama文档明确K3 Bianbu不支持加速，但FAQ声称支持或自动加速")
        if (
            _source_says_k3_buildroot_supported(source)
            and _claims_llama_cpp_only_k3_bianbu(claim_text)
        ):
            issues.append("当前llama.cpp文档显示K3 Buildroot也支持，但FAQ声称仅K3 Bianbu支持")
        if (
            "pypi.tuna.tsinghua.edu.cn" not in source_lower
            and _TSINGHUA_PYPI.search(claim_text)
            and nodepath.casefold().endswith("/development/python.md")
        ):
            issues.append("当前Python文档未支持清华PyPI源，但FAQ引用了该源")
    return tuple(dict.fromkeys(issues))


def _unverified_batch_fingerprints(
    candidates_db: Path, fingerprints: Sequence[str]
) -> tuple[str, ...]:
    """Return release rows that lack a capture-time verified official link."""

    candidates_db = Path(candidates_db)
    if not fingerprints:
        return ()
    placeholders = ",".join("?" for _ in fingerprints)
    try:
        with sqlite3.connect(f"file:{candidates_db}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT fingerprint, references_json FROM candidates "
                f"WHERE fingerprint IN ({placeholders})",
                tuple(fingerprints),
            ).fetchall()
    except sqlite3.Error as exc:
        raise FAQSyncError("无法读取FAQ候选参考资料") from exc
    valid: set[str] = set()
    for fingerprint, raw in rows:
        try:
            references = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(references, Sequence) or isinstance(references, (str, bytes, bytearray)):
            continue
        if any(
            isinstance(item, Mapping)
            and item.get("verified") is True
            and _safe_reference(item) is not None
            for item in references
        ):
            valid.add(str(fingerprint))
    return tuple(fingerprint for fingerprint in fingerprints if fingerprint not in valid)


def _new_faq_sections(base_text: str, source_text: str) -> tuple[tuple[str, str], ...] | None:
    """Return only appended FAQ question sections, never raw candidate prose."""

    if not source_text.startswith(base_text):
        return None
    appended = source_text[len(base_text) :].lstrip("\n")
    if not appended:
        return ()
    sections: list[tuple[str, str]] = []
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", appended, re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(appended)
        sections.append((match.group(1).strip(), appended[match.end() : end]))
    return tuple(sections)


def auto_review_branch(
    repo_path: Path,
    source_branch: str,
    target_branch: str,
    candidates_db: Path,
    *,
    faq_relative_path: Path = Path("faq.md"),
    remote: str = "origin",
    candidate_fingerprints: Sequence[str] = (),
) -> BranchReviewResult:
    """Conservatively auto-approve only structurally safe, verified-reference FAQ diffs.

    This gate does **not** accept an answer because its generated prose sounds
    plausible.  It accepts a branch only when every newly appended FAQ item has
    a named reference whose exact URL was marked ``verified=True`` when the
    official-source candidate was captured.  Any legacy/unverified source,
    rewrite of prior FAQ material, unrelated file change, or missing reference
    stays in the independent-review queue.
    """

    structural = review_branch(
        repo_path,
        source_branch,
        target_branch,
        faq_relative_path=faq_relative_path,
        remote=remote,
        reviewer_approved=True,
    )
    if not structural.approved:
        return structural
    repo = Path(repo_path).resolve()
    relative = Path(faq_relative_path)
    try:
        target_text = _git(repo, ("show", f"{remote}/{target_branch}:{relative}"))
        source_text = _git(repo, ("show", f"{source_branch}:{relative}"))
        sections = _new_faq_sections(target_text, source_text)
        if not sections:
            return BranchReviewResult(False, "审核分支没有可验证的新增FAQ条目", structural.static_review)
        verified_urls = _explicitly_verified_reference_urls(
            candidates_db, candidate_fingerprints
        )
    except FAQSyncError as exc:
        return BranchReviewResult(False, str(exc), structural.static_review)

    for heading, body in sections:
        if "### 已核验的官方参考" not in body:
            return BranchReviewResult(False, f"新增FAQ缺少已核验参考：{heading}", structural.static_review)
        links = re.findall(r"^-\s+\[([^\]]+)\]\((https://[^)\s]+)\)\s*$", body, re.MULTILINE)
        if not links:
            return BranchReviewResult(False, f"新增FAQ缺少命名参考链接：{heading}", structural.static_review)
        for title, url in links:
            if not title.strip() or url not in verified_urls:
                return BranchReviewResult(
                    False,
                    f"新增FAQ含未验证参考：{heading}",
                    structural.static_review,
                )
        source_issues = _official_source_fidelity_issues(
            heading,
            body,
            (url for _, url in links),
        )
        if source_issues:
            return BranchReviewResult(
                False,
                f"新增FAQ未通过当前官方文档一致性检查：{heading}；{source_issues[0]}",
                structural.static_review,
            )
    return BranchReviewResult(True, "结构与已核验参考检查通过", structural.static_review)


def _review_gate_result(
    review_gate: Callable[[Path, str, str], object] | None,
    repo: Path,
    source_branch: str,
    target_branch: str,
    *,
    remote: str,
    faq_relative_path: Path,
    reviewer_approved: bool,
    candidates_db: Path | None = None,
    auto_approve_if_clean: bool = False,
    candidate_fingerprints: Sequence[str] = (),
) -> BranchReviewResult:
    if review_gate is None and auto_approve_if_clean:
        if candidates_db is None:
            return BranchReviewResult(False, "自动审核缺少FAQ候选库")
        return auto_review_branch(
            repo,
            source_branch,
            target_branch,
            candidates_db,
            faq_relative_path=faq_relative_path,
            remote=remote,
            candidate_fingerprints=candidate_fingerprints,
        )
    if review_gate is None:
        return review_branch(
            repo,
            source_branch,
            target_branch,
            faq_relative_path=faq_relative_path,
            remote=remote,
            reviewer_approved=reviewer_approved,
        )
    try:
        result = review_gate(repo, source_branch, target_branch)
    except Exception:
        return BranchReviewResult(False, "独立审核门禁执行失败")
    if isinstance(result, BranchReviewResult):
        return result
    if result is True:
        return BranchReviewResult(True, "独立审核门禁已批准")
    return BranchReviewResult(False, "独立审核门禁未批准")


def publish_daily_candidates(
    questions: Iterable[object],
    candidates_db: Path,
    repo_path: Path,
    *,
    faq_relative_path: Path = Path("faq.md"),
    source_branch: str = "",
    target_branch: str = "master",
    remote: str = "origin",
    reviewer_approved: bool = False,
    auto_approve_if_clean: bool = False,
    review_gate: Callable[[Path, str, str], object] | None = None,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> DailyPublishResult:
    """Opt in to the complete isolated FAQ release workflow.

    Unlike :func:`stage_candidates`, this function never writes the live
    checkout.  It first fetches the configured target and stages into a
    temporary detached worktree made from that target.  The branch is pushed
    for review; only an explicit independent approval can merge it.  Set
    ``auto_approve_if_clean=True`` only for the scheduled route when the
    deterministic structural-and-verified-reference gate is the designated
    independent reviewer.  Importing this module and normal scheduled daily
    reports do not invoke this function.
    """

    question_list = list(questions)
    # Calling this function from the explicit ``--faq-publish`` route is the
    # opt-in.  Library callers that want a non-mutating preview can pass either
    # ``dry_run=True`` or ``confirm=False``.  Importing the module does neither.
    if confirm is None:
        confirm = not dry_run
    no_stage = StageResult(detail="发布未执行：需要显式发布授权")
    if not confirm and not dry_run:
        return DailyPublishResult(no_stage, detail=no_stage.detail, target_branch=target_branch)
    repo = Path(repo_path).resolve()
    candidates_db = Path(candidates_db)
    relative = Path(faq_relative_path)
    if not all(_valid_ref_name(value) for value in (target_branch, remote)):
        return DailyPublishResult(no_stage, detail="Git目标分支或远端名称无效", target_branch=target_branch)
    if relative.is_absolute() or ".." in relative.parts:
        return DailyPublishResult(no_stage, detail="FAQ相对路径无效", target_branch=target_branch)
    if not source_branch:
        source_branch = deterministic_daily_branch(question_list)
    if not _valid_ref_name(source_branch):
        return DailyPublishResult(no_stage, detail="FAQ审核分支名称无效", target_branch=target_branch)

    if dry_run:
        # Dry runs use ordinary temporary copies, not Git worktrees.  That is
        # enough to prove selection, rendering and structural review while
        # guaranteeing no Git refs, candidate states or checked-out FAQ files
        # change as a side effect.
        try:
            if not repo.is_dir():
                raise FAQSyncError("FAQ仓库目录不存在")
            source_faq = repo / relative
            if not source_faq.is_file():
                raise FAQSyncError("FAQ目标文件不存在或不是普通文件")
            with tempfile.TemporaryDirectory(prefix="cie-faq-daily-dry-run-") as temporary_root:
                temporary = Path(temporary_root)
                preview_faq = temporary / relative
                preview_faq.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_faq, preview_faq)
                preview_db = temporary / "candidates.sqlite"
                with sqlite3.connect(
                    f"file:{candidates_db}?mode=ro", uri=True
                ) as source, sqlite3.connect(preview_db) as target:
                    source.backup(target)
                staged = stage_candidates(question_list, preview_db, preview_faq)
                review = static_review_faq(preview_faq) if not staged.failed else None
                if review is not None and not review.approved:
                    return DailyPublishResult(
                        staged,
                        detail="FAQ干运行未通过静态审核",
                        source_branch=source_branch,
                        target_branch=target_branch,
                        review=BranchReviewResult(False, "FAQ静态审核未通过", review),
                    )
                return DailyPublishResult(
                    staged,
                    detail="FAQ干运行完成：未写入候选库、仓库或远端分支",
                    source_branch=source_branch,
                    target_branch=target_branch,
                    review=BranchReviewResult(False, "干运行未进入独立审核", review),
                )
        except (FAQSyncError, OSError, sqlite3.Error):
            return DailyPublishResult(
                no_stage,
                detail="FAQ干运行无法完成",
                source_branch=source_branch,
                target_branch=target_branch,
            )

    try:
        top_level = Path(_git(repo, ("rev-parse", "--show-toplevel"))).resolve()
        if top_level != repo:
            return DailyPublishResult(no_stage, detail="目标目录不是仓库根目录", target_branch=target_branch)
        _verify_clean_target_checkout(repo, target_branch)
        _git(repo, ("fetch", "--prune", remote))
        target_ref = f"{remote}/{target_branch}"
        target_commit = _git(repo, ("rev-parse", target_ref))
        recovered = _recover_remote_merged_release(
            repo,
            candidates_db,
            question_list,
            source_branch=source_branch,
            target_branch=target_branch,
            remote=remote,
        )
        if recovered is not None:
            return recovered
        existing = subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{source_branch}"],
            check=False,
            timeout=30,
        )
        if existing.returncode == 0:
            return DailyPublishResult(
                no_stage,
                detail="确定性FAQ审核分支已存在，拒绝覆盖",
                source_branch=source_branch,
                target_branch=target_branch,
            )
        status_before = _candidate_status_snapshot(candidates_db)
    except FAQSyncError as exc:
        return DailyPublishResult(no_stage, detail=str(exc), target_branch=target_branch)
    except (OSError, subprocess.TimeoutExpired):
        return DailyPublishResult(no_stage, detail="FAQ发布预检无法完成", target_branch=target_branch)

    staged = no_stage
    source_commit = ""
    merge_commit = ""
    pushed = False
    remote_merged = False
    local_checkout_updated = False
    review: BranchReviewResult | None = None
    with tempfile.TemporaryDirectory(prefix="cie-faq-daily-publish-") as temporary_root:
        worktree = Path(temporary_root) / "source"
        attached = False
        try:
            # This order is intentional: the current target is fetched and
            # detached before any candidate can write a FAQ file.
            _git(repo, ("worktree", "add", "--detach", str(worktree), target_ref))
            attached = True
            _git(worktree, ("switch", "-c", source_branch, target_ref))
            staged = stage_candidates(question_list, candidates_db, worktree / relative)
            if staged.failed:
                return DailyPublishResult(
                    staged,
                    detail=staged.detail,
                    source_branch=source_branch,
                    target_branch=target_branch,
                )
            if not staged.added:
                return DailyPublishResult(
                    staged,
                    detail="没有新增FAQ候选，无需创建发布分支",
                    source_branch=source_branch,
                    target_branch=target_branch,
                )
            if auto_approve_if_clean:
                unverified = _unverified_batch_fingerprints(
                    candidates_db, staged.staged_fingerprints
                )
                if unverified:
                    _restore_pending_statuses(
                        candidates_db, staged.staged_fingerprints, status_before
                    )
                    return DailyPublishResult(
                        staged,
                        detail="FAQ候选缺少已核验的官方参考，未创建审核分支",
                        source_branch=source_branch,
                        target_branch=target_branch,
                    )
            static = static_review_faq(worktree / relative)
            if not static.approved:
                raise FAQSyncError("FAQ静态审核未通过")
            _git(worktree, ("diff", "--check"))
            _git(worktree, ("add", "--", str(relative)))
            _git(worktree, ("commit", "--no-verify", "-m", "docs: stage daily CIE FAQ"))
            source_commit = _git(worktree, ("rev-parse", "HEAD"))
            _git(repo, ("push", remote, f"refs/heads/{source_branch}:refs/heads/{source_branch}"))
            pushed = True
            review = _review_gate_result(
                review_gate,
                repo,
                source_branch,
                target_branch,
                remote=remote,
                faq_relative_path=relative,
                reviewer_approved=reviewer_approved,
                candidates_db=candidates_db,
                auto_approve_if_clean=auto_approve_if_clean,
                candidate_fingerprints=staged.staged_fingerprints,
            )
            if not review.approved:
                return DailyPublishResult(
                    staged,
                    detail="FAQ审核分支已推送，等待独立审核",
                    source_branch=source_branch,
                    source_commit=source_commit,
                    target_branch=target_branch,
                    review=review,
                )
            _git(repo, ("fetch", remote, target_branch, source_branch))
            if _git(repo, ("rev-parse", target_ref)) != target_commit:
                raise FAQSyncError("目标分支在审核期间已更新，拒绝自动合并")
            _git(worktree, ("switch", "--detach", target_ref))
            _git(worktree, ("merge", "--no-ff", "--no-edit", source_branch))
            _git(worktree, ("diff", "--check"))
            merge_commit = _git(worktree, ("rev-parse", "HEAD"))
            _git(repo, ("push", remote, f"{merge_commit}:refs/heads/{target_branch}"))
            remote_merged = True
            _git(repo, ("fetch", remote, target_branch))
            _git(repo, ("merge-base", "--is-ancestor", source_commit, target_ref))
            _fast_forward_target_checkout(repo, remote, target_branch)
            local_checkout_updated = True
            _mark_published(candidates_db, staged.staged_fingerprints)
            return DailyPublishResult(
                staged,
                published=True,
                detail="FAQ已通过独立审核并合并到目标分支",
                source_branch=source_branch,
                source_commit=source_commit,
                target_branch=target_branch,
                merge_commit=merge_commit,
                review=review,
                local_checkout_updated=local_checkout_updated,
            )
        except FAQSyncError as exc:
            if remote_merged:
                # The FAQ has already reached the remote target.  Preserve
                # that fact in the report even if a local state write or
                # fast-forward needs recovery on the next scheduled run.
                return DailyPublishResult(
                    staged,
                    published=True,
                    detail=f"FAQ已合并，但本地发布状态待修复：{exc}",
                    source_branch=source_branch,
                    source_commit=source_commit,
                    target_branch=target_branch,
                    merge_commit=merge_commit,
                    review=review,
                    local_checkout_updated=local_checkout_updated,
                )
            if not pushed:
                try:
                    _restore_pending_statuses(
                        candidates_db, staged.staged_fingerprints, status_before
                    )
                except FAQSyncError:
                    pass
            return DailyPublishResult(
                staged,
                detail=str(exc),
                source_branch=source_branch,
                source_commit=source_commit,
                target_branch=target_branch,
                merge_commit=merge_commit,
                review=review,
                release_failed=True,
                local_checkout_updated=local_checkout_updated,
            )
        finally:
            if attached:
                try:
                    _git(repo, ("worktree", "remove", "--force", str(worktree)))
                except FAQSyncError:
                    pass
            # A failed pre-push attempt must be genuinely retryable.  The
            # branch was created solely inside this temporary workflow, so it
            # is safe to discard only while it has never reached the remote.
            if not pushed:
                try:
                    subprocess.run(
                        ["git", "-C", str(repo), "branch", "-D", source_branch],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass


def release_staged_faq(
    repo_path: Path,
    *,
    faq_path: Path | None = None,
    source_branch: str = "",
    target_branch: str = "master",
    remote: str = "origin",
    push: bool = False,
    merge: bool = False,
    reviewer_approved: bool = False,
    confirm: bool = False,
) -> ReleaseResult:
    """Explicitly commit a staged FAQ file in an isolated git worktree.

    ``confirm=True`` is required for *any* git mutation.  ``push=True`` pushes
    only the newly-created source branch for independent review.  ``merge=True``
    additionally requires ``reviewer_approved=True`` and only fast-forwards the
    remote target through a locally created merge commit based on the latest
    fetched target.  The daily report must not call this helper implicitly.
    """

    repo = Path(repo_path).resolve()
    faq = (Path(faq_path) if faq_path is not None else repo / "faq.md").resolve()
    review = static_review_faq(faq)
    if not review.approved:
        return ReleaseResult(False, "静态FAQ审核未通过", review=review)
    if merge and not push:
        return ReleaseResult(False, "合并需要同时推送审核分支", review=review)
    if merge and not reviewer_approved:
        return ReleaseResult(False, "合并需要独立审核明确批准", review=review)
    if not confirm:
        return ReleaseResult(
            False,
            "发布未执行：需由调用方显式传入confirm=True",
            review=review,
        )
    if not source_branch:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        source_branch = f"bot/faq-{stamp}"
    if not all(_valid_ref_name(value) for value in (source_branch, target_branch, remote)):
        return ReleaseResult(False, "Git分支或远端名称无效", review=review)

    try:
        top_level = Path(_git(repo, ("rev-parse", "--show-toplevel"))).resolve()
        if top_level != repo:
            return ReleaseResult(False, "目标目录不是仓库根目录", review=review)
        relative_faq = _relative_faq_path(repo, faq)
        if _git(repo, ("status", "--porcelain")):
            return ReleaseResult(False, "FAQ仓库工作区不干净，拒绝混入发布", review=review)
        _git(repo, ("fetch", "--prune", remote, target_branch))
        target_ref = f"{remote}/{target_branch}"
        target_commit = _git(repo, ("rev-parse", target_ref))
        existing = subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{source_branch}"],
            check=False,
            timeout=30,
        )
        if existing.returncode == 0:
            return ReleaseResult(False, "本地审核分支已存在，拒绝覆盖", review=review)
    except FAQSyncError as exc:
        return ReleaseResult(False, str(exc), review=review)
    except (OSError, subprocess.TimeoutExpired):
        return ReleaseResult(False, "FAQ git预检无法完成", review=review)

    source_commit = ""
    merge_commit = ""
    with tempfile.TemporaryDirectory(prefix="cie-faq-release-") as temporary_root:
        worktree = Path(temporary_root) / "source"
        added_worktree = False
        try:
            _git(repo, ("worktree", "add", "--detach", str(worktree), target_ref))
            added_worktree = True
            _git(worktree, ("switch", "-c", source_branch, target_ref))
            worktree_faq = worktree / relative_faq
            worktree_faq.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(faq, worktree_faq)
            copied_review = static_review_faq(worktree_faq)
            if not copied_review.approved:
                return ReleaseResult(False, "临时审核工作区未通过静态审核", review=copied_review)
            _git(worktree, ("diff", "--check"))
            _git(worktree, ("add", "--", str(relative_faq)))
            no_change = subprocess.run(
                ["git", "-C", str(worktree), "diff", "--cached", "--quiet"],
                check=False,
                timeout=30,
            ).returncode == 0
            if no_change:
                return ReleaseResult(True, "FAQ内容与目标分支一致，无需发布", source_branch=source_branch, review=copied_review)
            _git(worktree, ("commit", "--no-verify", "-m", "docs: update CIE FAQ"))
            source_commit = _git(worktree, ("rev-parse", "HEAD"))
            if push:
                _git(repo, ("push", remote, f"refs/heads/{source_branch}:refs/heads/{source_branch}"))
            if merge:
                _git(repo, ("fetch", remote, target_branch, source_branch))
                refreshed_target = _git(repo, ("rev-parse", target_ref))
                if refreshed_target != target_commit:
                    raise FAQSyncError("目标分支在审核期间已更新，拒绝自动合并")
                _git(worktree, ("switch", "--detach", target_ref))
                _git(worktree, ("merge", "--no-ff", "--no-edit", source_branch))
                _git(worktree, ("diff", "--check"))
                merge_commit = _git(worktree, ("rev-parse", "HEAD"))
                _git(repo, ("push", remote, f"{merge_commit}:refs/heads/{target_branch}"))
                _git(repo, ("fetch", remote, target_branch))
                _git(repo, ("merge-base", "--is-ancestor", source_commit, target_ref))
        except FAQSyncError as exc:
            return ReleaseResult(
                False,
                str(exc),
                source_branch=source_branch,
                source_commit=source_commit,
                target_branch=target_branch,
                merge_commit=merge_commit,
                review=review,
            )
        finally:
            if added_worktree:
                try:
                    _git(repo, ("worktree", "remove", "--force", str(worktree)))
                except FAQSyncError:
                    # The TemporaryDirectory will remove only its own path.  A
                    # stale Git administrative record is safer to surface than
                    # a destructive cleanup attempt against an unknown path.
                    pass

    detail = "FAQ审核分支已创建"
    if push:
        detail = "FAQ审核分支已推送，等待独立审核"
    if merge:
        detail = "FAQ已通过独立审核并合并到目标分支"
    return ReleaseResult(
        True,
        detail,
        source_branch=source_branch,
        source_commit=source_commit,
        target_branch=target_branch,
        merge_commit=merge_commit,
        review=review,
    )


__all__ = [
    "BranchReviewResult",
    "Candidate",
    "DailyPublishResult",
    "FAQSyncError",
    "HANDLED",
    "PENDING_REVIEW",
    "QUEUED_FOR_REVIEW",
    "Reference",
    "ReleaseResult",
    "StageResult",
    "StaticReviewResult",
    "auto_review_branch",
    "canonical_references",
    "deterministic_daily_branch",
    "load_candidates",
    "normalize_question",
    "publish_daily_candidates",
    "questions_equivalent",
    "release_staged_faq",
    "review_branch",
    "sanitize_answer",
    "stage_candidates",
    "static_review_faq",
]
