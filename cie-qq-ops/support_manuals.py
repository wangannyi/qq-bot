"""Targeted upstream evidence for general pip invocation/configuration questions."""

import re

from support_reply import validate_answer

PIP_GUIDE = "https://pip.pypa.io/en/stable/user_guide/"
UV_COMPAT = "https://docs.astral.sh/uv/pip/compatibility/"
PIP_CONFIG = "https://pip.pypa.io/en/stable/cli/pip_config/"


def pip_manual_task(query):
    question = query.split("\n用户明确提供的环境：", 1)[0]
    question = question.split("\n引用内容（", 1)[0]
    if not re.search(r"(?<![a-z0-9])pip(?![a-z0-9])", question, re.I):
        return None
    # Application installation and error diagnosis retain their own sources.
    if re.search(r"报错|失败|spacemit-asr|相机|ROS|源码|脚本|代码", question, re.I):
        return None
    if re.search(r"(?<![a-z0-9])uv(?![a-z0-9])", question, re.I) and re.search(
        r"(?:源|配置).*(?:共用|共享|检查|分别)|(?:共用|共享).*(?:源|配置)", question
    ):
        return "config"
    if re.search(r"指定.*解释器|解释器.*(?:执行|调用).*pip", question, re.I):
        return "interpreter"
    return None


def pip_manual_candidates(query):
    task = pip_manual_task(query)
    if task == "interpreter":
        return [(PIP_GUIDE, "pip User Guide — Running pip")]
    if task == "config":
        return [(UV_COMPAT, "uv — pip compatibility"), (PIP_CONFIG, "pip config")]
    return []


def manual_blocks(url, blocks):
    """Select actual paragraphs, not Chinese-token matches in an English TOC."""
    needles = {
        PIP_GUIDE: ("executes pip using the Python interpreter",),
        UV_COMPAT: (
            "uv does not read configuration files",
            "Instead, uv supports its own environment variables",
        ),
        PIP_CONFIG: (
            "List the active configuration",
            "List the configuration files and values",
        ),
    }
    return [b for b in blocks if any(n in b for n in needles.get(url, ()))]


def pip_manual_answer(query, sources):
    task = pip_manual_task(query)
    if not task:
        return None

    def evidence(url, needle):
        for sid, source in enumerate(sources):
            if (
                source.get("verified")
                and source.get("source_kind") == "upstream"
                and source.get("page_url") == url
            ):
                for line in source["content"].splitlines():
                    if needle in line:
                        return {"source_id": sid, "quote": line}
        return None

    if task == "interpreter":
        span = evidence(PIP_GUIDE, "executes pip using the Python interpreter")
        if not span:
            return None
        conclusion = {
            "text": "用指定 Python 解释器的绝对路径加 -m pip，再跟 pip 子命令及参数。这样由该解释器执行 pip，不依赖 PATH 中的 pip。",
            "evidence": [span],
        }
        points = []
    else:
        separate = evidence(UV_COMPAT, "uv does not read configuration files")
        uv = evidence(UV_COMPAT, "Instead, uv supports its own environment variables")
        debug = evidence(PIP_CONFIG, "List the configuration files and values")
        active = evidence(PIP_CONFIG, "List the active configuration")
        if not all((separate, uv, debug, active)):
            return None
        # Require the names as well as the lead-in; changed upstream wording
        # must not silently validate an old, hard-coded claim.
        if not all(x in separate["quote"] for x in ("pip.conf", "PIP_INDEX_URL")):
            return None
        if not all(
            x in uv["quote"]
            for x in ("UV_INDEX_URL", "uv.toml", "[tool.uv.pip]", "pyproject.toml")
        ):
            return None
        conclusion = {
            "text": "配置不会自动共用：uv 不读取 pip 专用的 pip.conf 或 PIP_INDEX_URL。",
            "evidence": [separate],
        }
        points = [
            {
                "text": "pip：检查 pip.conf、PIP_INDEX_URL；pip config 的 debug 子命令列出配置文件及值，list 列出当前生效配置。",
                "evidence": [separate, debug, active],
            },
            {
                "text": "uv：分别检查 UV_INDEX_URL、uv.toml，以及 pyproject.toml 中的 [tool.uv.pip] 配置。",
                "evidence": [uv],
            },
        ]
    return validate_answer(
        {
            "status": "answered",
            "conclusion": conclusion,
            "points": points,
            "follow_up": "",
        },
        sources,
    )
