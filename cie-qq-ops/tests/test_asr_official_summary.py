"""Regression coverage for official-community ASR summaries sent to QQ.

The website assistant's ASR answer has two *parallel* routes: a local
``spacemit-asr`` example and an already-installed AI Gateway.  A compact QQ
summary must retain that boundary, otherwise filenames such as
``voice_env.sh`` look like unexplained instructions.
"""
from __future__ import annotations

import copy
import re
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import answer_policy as policy
import rag_server
import website_bridge as bridge
from test_summary_validation import payload

QUESTION = "spacemit-asr部署方式"
ASR_URL = (
    "https://www.spacemit.com/community/document/info?"
    "nodepath=courses/AI/01_AI%E5%9F%BA%E7%A1%80%E5%AD%A6%E4%B9%A0%E5%8F%8A%E5%AE%9E%E8%B7%B5/"
    "23_ASR%E5%AE%9E%E4%BE%8B.md&lang=zh"
)
AI_LAB_URL = (
    "https://www.spacemit.com/community/document/info?"
    "nodepath=ai/solutions/aicomputer_solution/ailab.md&lang=zh"
)

# Synthetic two-route fixture authored for this test. It has both a
# local example route and a Gateway route, so treating every noun as one long
# instruction sequence loses the prerequisite for the second route.
RAW_OFFICIAL_ANSWER = f"""合成测试答复，非官方安装建议：
### 本地 spacemit-asr 示例
演示路线包含 voice_env.sh、llm-asr、libopenblas-dev；虚构的样例目录为 /usr/local/share/voice_test，测试标记包括 sensevoice、asr.py 和 llm_asr。
### AI Gateway 接口调用
另一条路线的前提是已安装并启动 SpacemiT AI Gateway；准备音频后调用 /v1/asr/recognize。
[AI Lab]({AI_LAB_URL})
[ASR实例]({ASR_URL})
"""

METHODS = [
    {
        "name": "spacemit-asr 本地示例",
        "condition": "适用于在K1本地运行ASR示例。",
        "steps": [
            "运行voice_env.sh完成llm-asr和libopenblas-dev的安装。",
            "由该脚本在/usr/local/share/voice_test准备示例音频和sensevoice模型权重。",
            "创建asr.py，通过llm_asr加载模型并识别音频。",
        ],
    },
    {
        "name": "AI Gateway 接口调用",
        "condition": "仅在已安装并启动SpacemiT AI Gateway时使用。",
        "steps": [
            "确认Gateway服务已启动。",
            "准备要识别的音频文件。",
            "调用/v1/asr/recognize接口提交音频进行识别。",
        ],
    },
]


def evidence():
    return {
        "original_answer": RAW_OFFICIAL_ANSWER,
        "references": [dict(item, verified=True) for item in bridge.references(RAW_OFFICIAL_ANSWER)],
    }


def test_asr_deployment_question_is_sent_unchanged_to_official_website_bridge(monkeypatch):
    """SpacemiT ASR must not fall back to local RAG or an arbitrary answer."""
    calls = []
    official = types.ModuleType("website_bridge")
    official.enabled = lambda: True
    official.event_question = lambda question: False

    def ask(question):
        calls.append(question)
        return {
            "backend": "website",
            "query": question,
            "original_answer": "官网 ASR 答复",
            "references": [],
        }

    official.ask = ask
    official.final_answer = lambda question, source, session_id: "依据官网答复的QQ摘要"
    official.clarification = lambda *args: None
    monkeypatch.setitem(sys.modules, "website_bridge", official)

    policy._turns.clear()
    policy._clarifications.clear()
    policy.begin(session_id="asr-official", user_message=QUESTION, platform="qqbot")

    assert policy.is_technical_question(QUESTION)
    assert policy.is_technical_question("ASR部署方式")
    assert policy.transform(session_id="asr-official", platform="qqbot") == "依据官网答复的QQ摘要"
    assert calls == [QUESTION]


def test_parallel_asr_summary_is_source_only_and_keeps_contextual_methods(monkeypatch):
    """The known official two-route answer never needs a second model call.

    Source-only rendering is intentional: a generator previously flattened
    the routes into a dense paragraph, which hid the fact that Gateway is an
    existing-installation route rather than a prerequisite for the local demo.
    """
    def unexpected_summary_provider():
        pytest.fail("parallel ASR source-only renderer must not request summarizer credentials")

    monkeypatch.setattr(rag_server, "model_credentials", unexpected_summary_provider)

    answer = bridge.summarize(QUESTION, evidence())

    assert answer.count("方法一：") == 1
    assert answer.count("方法二：") == 1
    assert "方法三：" not in answer
    assert re.search(r"方法一：[^\n]*spacemit-asr", answer, re.I)
    assert re.search(r"方法二：[^\n]*AI Gateway", answer, re.I)
    assert re.search(r"AI Gateway[^\n]*(?:已安装|已部署|已启动)|(?:已安装|已部署|已启动)[^\n]*AI Gateway", answer)

    # Every identifier is paired with its purpose; none is sent as an orphan.
    assert re.search(r"(?:运行|使用)[^\n]*voice_env\.sh[^\n]*(?:安装|环境|依赖)", answer)
    path_line = next(line for line in answer.splitlines() if "/usr/local/share/voice_test" in line)
    assert re.search(r"(?:准备|下载|存放|音频|模型)", path_line)
    assert re.search(r"(?:创建|运行)[^\n]*asr\.py[^\n]*(?:加载|识别|模型)", answer)
    assert re.search(r"(?:调用|提交)[^\n]*/v1/asr/recognize[^\n]*(?:识别|音频)", answer)

    # The local example and Gateway route retain their supporting official
    # document entries, rather than a generic "查看官网说明" link.
    assert "[🔗ASR实例]" in answer
    assert ASR_URL in answer
    assert "[🔗AI Lab]" in answer
    assert AI_LAB_URL in answer


def test_asr_validate_summary_rejects_a_flat_parallel_candidate():
    """If a model path is ever reintroduced, it still cannot flatten routes."""
    flat = payload(
        "运行voice_env.sh安装依赖，在/usr/local/share/voice_test准备模型后执行asr.py；"
        "若已安装AI Gateway，则确认服务并调用/v1/asr/recognize。"
    )

    with pytest.raises(ValueError, match="parallel_asr_methods_require_sections"):
        bridge.validate_summary(flat, QUESTION, evidence(), procedural=False, broad_deployment=False)


def test_asr_method_rejects_a_bare_script_or_path_step():
    """A file/path/API name alone has no actionable context in a QQ reply."""
    bad = copy.deepcopy(METHODS)
    bad[0]["steps"] = ["voice_env.sh", "/usr/local/share/voice_test", "asr.py"]

    with pytest.raises(ValueError):
        bridge.format_methods(bad, RAW_OFFICIAL_ANSWER)
