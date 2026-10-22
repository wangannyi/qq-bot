"""A delivered diagnosis must retain source uncertainty and component scope."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from support_reply import InvalidAnswer, validate_answer

FIXTURE = Path(__file__).parent / "fixtures/service_diagnosis_failure.json"


def original():
    return json.loads(FIXTURE.read_text())


def corrected():
    data = original()
    answer = data["answer"]
    answer["status"] = "partial"
    answer["conclusion"]["text"] = "以K3 ASR组件为例检查服务状态和接口配置。"
    answer["points"][0]["text"] = (
        "Qwen3-ASR调用失败可能是llama-server未启动或endpoint不正确；用健康检查接口核对状态，不能仅凭异常判定服务未启动。"
    )
    answer["points"][1]["text"] = "Fun-ASR还需核对alias funasr及专用转写endpoint。"
    answer["follow_up"] = "资料未给通用模型服务的判定规则；实际使用哪个服务？"
    return data


def test_synthetic_delivered_bad_answer_is_no_longer_accepted():
    data = original()
    with pytest.raises(InvalidAnswer, match="unsupported_service_down_inference"):
        validate_answer(data["answer"], data["sources"])


@pytest.mark.parametrize(
    "claim",
    [
        "健康检查失败说明服务未启动。",
        "health超时，就说明进程已经退出。",
        "健康接口无响应意味着llama-server未运行。",
        "用健康检查核对状态，返回异常即服务未起。",
    ],
)
def test_health_probe_cannot_establish_process_down(claim):
    data = corrected()
    data["answer"]["points"][0]["text"] = claim
    with pytest.raises(InvalidAnswer, match="unsupported_service_down_inference"):
        validate_answer(data["answer"], data["sources"])


@pytest.mark.parametrize(
    "claim",
    [
        "健康检查失败可能是服务未启动，也可能是endpoint错误。",
        "健康检查失败不能说明服务未启动。",
        "不能认为健康检查异常即服务未起。",
        "“健康检查异常即服务未起”的推断不成立。",
        "健康检查确认状态；服务未启动或endpoint错误都是可能原因。",
        "健康检查失败说明服务未就绪，需继续排查原因。",
        "健康检查超时；日志显示进程已退出。",
    ],
)
def test_qualified_diagnostics_are_not_rejected(claim):
    data = corrected()
    data["answer"]["points"][0]["text"] = claim
    assert validate_answer(data["answer"], data["sources"])["status"] == "partial"


def test_asr_example_is_partial_for_generic_service_question():
    data = corrected()
    answer = validate_answer(data["answer"], data["sources"])
    runtime.validate_service_scope(data["question"], answer, data["sources"])
    answer["status"] = "answered"
    with pytest.raises(
        InvalidAnswer, match="component_specific_diagnosis_requires_scope"
    ):
        runtime.validate_service_scope(data["question"], answer, data["sources"])
    # Explicit ASR and unrelated established workflows are unaffected.
    for query in [
        "K3 Qwen3-ASR调用失败如何排查？",
        "Python如何创建虚拟环境？",
        "本地模型服务是什么？",
    ]:
        runtime.validate_service_scope(query, answer, data["sources"])


@pytest.mark.parametrize("bad_stage", ["generate", "review"])
def test_real_pipeline_repairs_diagnosis_once(monkeypatch, bad_stage):
    bad, good = original(), corrected()
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": bad["sources"]}
    )
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        stage = "review" if kwargs.get("review_draft") else "generate"
        value = good if kwargs.get("feedback") or stage != bad_stage else bad
        return copy.deepcopy(value["answer"]), {}

    monkeypatch.setattr(runtime, "generate", generate)
    result = runtime.answer_question(bad["question"], config={})
    assert result["answer"]["status"] == "partial"
    assert "返回异常即服务未起" not in result["text"]
    assert calls[-1]["feedback"]["error"] == "unsupported_service_down_inference"
    assert len(calls) == (2 if bad_stage == "generate" else 3)


def test_repeated_bad_diagnosis_is_never_published(monkeypatch):
    bad = original()
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": bad["sources"]}
    )
    monkeypatch.setattr(
        runtime, "generate", lambda *a, **k: (copy.deepcopy(bad["answer"]), {})
    )
    with pytest.raises(InvalidAnswer, match="unsupported_service_down_inference"):
        runtime.answer_question(bad["question"], config={})
