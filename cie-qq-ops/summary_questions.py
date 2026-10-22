"""Reconstruct support cases from QQ turns before counting daily questions.

Only same-user, same-chat turns may share context. Report-level deduplication
is separate, so another user's successful reply cannot close an open case.
"""

import re
import unicodedata
from dataclasses import dataclass, field, replace

CONTEXT_SECONDS = 1800
_ENV = re.compile(
    r"(?<![a-z0-9])(bianbu|ubuntu|buildroot|openharmony)(?:\s*v?(\d+(?:\.\d+){0,3}))?",
    re.I,
)
_FRAGMENT = re.compile(
    r"(?:(?:k[13]|bianbu|ubuntu|buildroot|openharmony|系统|版本号?|板子|开发板|当前|实际|我用的是|用的是|是|为|v|\d+(?:\.\d+){0,3})[\s,，。:：、/]*)+",
    re.I,
)
_NEW_QUESTION = re.compile(r"如何|怎么|怎样|为什么|介绍|有哪些|是什么|包含哪些|区别")
_FEEDBACK = re.compile(
    r"^(?:不对|答错|你答错|没有解决|没解决|还是不行|还是报错|不行|不是这个问题)"
)


@dataclass
class Turn:
    text: str
    timestamp: float
    session_id: str
    responses: list[str] = field(default_factory=list)


@dataclass
class Case:
    text: str
    scope: tuple
    turns: list[Turn] = field(default_factory=list)
    environment: dict = field(default_factory=dict)


def environment(text):
    facts = {}
    boards = set(re.findall(r"(?<![a-z0-9])k[13](?![a-z0-9])", text.lower()))
    if len(boards) == 1:
        facts["board"] = next(iter(boards)).upper()
    match = _ENV.search(text)
    if match:
        facts["system"] = match[1].casefold()
        if match[2]:
            facts["version"] = match[2]
    return facts


def same_problem(left, right):
    from faq_sync import questions_equivalent

    left, right = (
        unicodedata.normalize("NFKC", left),
        unicodedata.normalize("NFKC", right),
    )
    a, b = environment(left), environment(right)
    if any(a[key] != b[key] for key in a.keys() & b.keys()):
        return False
    # Keep distinct errors, package versions and comparison operands distinct.
    numbers = lambda text: set(
        re.findall(r"(?<![a-z0-9])v?\d+(?:\.\d+)+(?![a-z0-9])", text, re.I)
    )
    if numbers(left) and numbers(right) and numbers(left) != numbers(right):
        return False
    actions = (
        r"创建|新建|建立",
        r"激活|进入虚拟环境",
        r"退出(?:虚拟)?环境|deactivate",
        r"删除|移除|卸载",
        r"安装|部署",
        r"开启|启动|启用",
        r"关闭|停止|禁用",
    )
    left_actions = {
        i for i, pattern in enumerate(actions) if re.search(pattern, left, re.I)
    }
    right_actions = {
        i for i, pattern in enumerate(actions) if re.search(pattern, right, re.I)
    }
    if left_actions and right_actions and left_actions != right_actions:
        return False
    identifiers = lambda text: set(
        re.findall(r"[a-z][a-z0-9_.-]*\d[a-z0-9_.-]*", text.casefold())
    )
    if (
        identifiers(left)
        and identifiers(right)
        and identifiers(left) != identifiers(right)
    ):
        return False
    return questions_equivalent(left, right)


def followup(text, previous_reply):
    if _FRAGMENT.fullmatch(text) or _FEEDBACK.search(text):
        return True
    if _NEW_QUESTION.search(text):
        return bool(re.match(r"^(?:那|这个|它|上述|刚才|继续)", text))
    if re.match(
        r"^(?:那|这个|它|上述|刚才|继续|报错|提示|显示|输出|Traceback|ERROR|/dev/|```)",
        text,
        re.I,
    ):
        return True
    return bool(
        len(text) <= 160
        and re.search(r"请(?:提供|补充|贴出)|待补充：", previous_reply)
        and (
            (
                re.search(r"日志|报错|输出|命令", previous_reply)
                and re.search(
                    r"(?:^|\n)(?:sudo |python|pip |cat |uname |ls |[A-Za-z]+Error:)|not found|No such|失败",
                    text,
                )
            )
            or (
                "型号" in previous_reply
                and re.match(r"^(?:MUSE|BPI|RV4B|M1)", text, re.I)
            )
        )
    )


def response_issue(question, response):
    """Recognize observed off-topic failures, without re-answering the question."""
    # The original deployment request must not become a release-note answer
    # merely because its next turn was a version number.
    if re.search(r"llm|大模型|语言模型", question, re.I) and not re.search(
        r"llm|模型|llama|ollama|gguf|qwen|deepseek|large.language.model", response, re.I
    ):
        return "答复偏离原问题，属于机器人处理异常"
    if re.search(r"虚拟环境|venv|virtualenv", question, re.I) and not re.search(
        r"摄像头|相机|camera|opencv", question, re.I
    ):
        labels = re.findall(r"\[([^\]]+)\]\([^\n]+?\)", response)
        if any(re.search(r"摄像头|相机|camera", label, re.I) for label in labels):
            return "通用环境问题误用了相机教程，属于机器人选材异常"
    return ""


def collect_questions(rows, start_timestamp):
    from daily_summary import (
        STATUS_BOT_ISSUE,
        STATUS_HANDLED,
        STATUS_HUMAN_REQUIRED,
        STATUS_WAITING,
        Question,
        classify_response,
        is_support_question,
        sanitize,
    )

    cases, current, reply_targets = [], {}, {}
    for session_id, role, timestamp, content, chat_id, user_id in rows:
        # Legacy databases lack user/chat columns; a session remains the
        # narrowest safe scope. Never join an unknown user to a known one.
        scope = (chat_id, user_id) if chat_id and user_id else (session_id,)
        if role == "assistant":
            target = reply_targets.get(session_id)
            if target and str(content or "").strip():
                target.responses.append(str(content))
            continue
        text = sanitize(str(content or ""))
        case = current.get(scope)
        if case and timestamp - case.turns[-1].timestamp > CONTEXT_SECONDS:
            case = None
        previous_reply = (
            case.turns[-1].responses[-1] if case and case.turns[-1].responses else ""
        )
        continuation = bool(case and followup(text, previous_reply))
        if not text or (not continuation and not is_support_question(text)):
            reply_targets.pop(session_id, None)
            continue
        if _FRAGMENT.fullmatch(text) and not continuation:
            # A standalone '4.0.1' is not an independently actionable question.
            reply_targets.pop(session_id, None)
            continue
        if not continuation and not (case and same_problem(text, case.text)):
            case = Case(text=text, scope=scope)
            cases.append(case)
        assert case is not None
        facts = environment(text)
        if facts.get("board") and facts["board"] != case.environment.get("board"):
            case.environment.clear()
        if facts.get("system") and facts["system"] != case.environment.get("system"):
            case.environment.pop("version", None)
        version = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})[。！!\s]*", text, re.I)
        if version and case.environment.get("system"):
            facts["version"] = version[1]
        case.environment.update(facts)
        turn = Turn(text=text, timestamp=float(timestamp), session_id=str(session_id))
        case.turns.append(turn)
        current[scope] = case
        reply_targets[session_id] = turn

    entries = []
    for case in cases:
        in_window = [turn for turn in case.turns if turn.timestamp >= start_timestamp]
        if not in_window:
            continue
        last = case.turns[-1]
        status, reason = classify_response(last.responses)
        if _FEEDBACK.search(last.text) and not last.responses:
            if re.search(r"答错|不是这个问题|不对", last.text):
                status, reason = STATUS_BOT_ISSUE, "用户指出答复有误，尚未得到更正"
            else:
                status, reason = (
                    STATUS_HUMAN_REQUIRED,
                    "用户反馈问题仍未解决，等待技术支持跟进",
                )
        if status == STATUS_HANDLED and last.responses:
            question = (
                case.text
                if _FRAGMENT.fullmatch(last.text) or same_problem(last.text, case.text)
                else last.text
            )
            issue = response_issue(question, last.responses[-1])
            if issue:
                status, reason = STATUS_BOT_ISSUE, issue
        entries.append(
            (
                case.scope,
                Question(
                    timestamp=in_window[0].timestamp,
                    text=case.text,
                    status=status,
                    reason=reason,
                    session_id=last.session_id,
                    candidate_question=last.text,
                    candidate_timestamp=last.timestamp,
                    environment=tuple(sorted(case.environment.items())),
                ),
            )
        )

    def equivalent(previous, question):
        a, b = dict(previous.environment), dict(question.environment)
        return not any(
            a[key] != b[key] for key in a.keys() & b.keys()
        ) and same_problem(previous.text, question.text)

    # Resolve each user's latest outcome before merging identical questions
    # across users. Otherwise an old failure can survive after that same user
    # succeeds, merely because someone else also asked the question in between.
    latest_by_user = []
    for scope, question in entries:
        for index, (previous_scope, previous) in enumerate(latest_by_user):
            if previous_scope == scope and equivalent(previous, question):
                chosen = max(
                    (previous, question), key=lambda item: item.candidate_timestamp
                )
                latest_by_user[index] = (
                    scope,
                    replace(
                        chosen, timestamp=min(previous.timestamp, question.timestamp)
                    ),
                )
                break
        else:
            latest_by_user.append((scope, question))

    merged = []
    for scope, question in latest_by_user:
        match = None
        for index, (scopes, previous) in enumerate(merged):
            if equivalent(previous, question):
                match = index
                break
        if match is None:
            merged.append(({scope}, question))
            continue
        scopes, previous = merged[match]
        # Do not close another user's unresolved issue using this reply.
        priority = {
            STATUS_HUMAN_REQUIRED: 3,
            STATUS_WAITING: 2,
            STATUS_HANDLED: 1,
            STATUS_BOT_ISSUE: 0,
        }
        chosen = max((previous, question), key=lambda item: priority[item.status])
        merged[match] = (
            scopes | {scope},
            replace(chosen, timestamp=min(previous.timestamp, question.timestamp)),
        )
    return sorted((question for _, question in merged), key=lambda item: item.timestamp)
