"""Optional unit configuration cannot establish a missing-import root cause."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from support_extracts import source_extract_answer
from support_reply import validate_answer

CASE = json.loads(
    (Path(__file__).parent / "fixtures/systemd_python_failure.json").read_text()
)


def test_synthetic_original_wrong_answer_passed_structural_validation():
    assert (
        validate_answer(CASE["structured_answer"], CASE["sources"])["status"]
        == "answered"
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_synthetic_dependency_question_keeps_user_service_scope(monkeypatch, reverse):
    sources = copy.deepcopy(CASE["sources"][::-1] if reverse else CASE["sources"])
    monkeypatch.setattr(runtime, "search_sources", lambda *a, **k: {"sources": sources})
    monkeypatch.setattr(
        runtime,
        "generate",
        lambda *a, **k: pytest.fail("source example became a causal diagnosis"),
    )
    result = runtime.answer_question(CASE["question"], config={})
    a = result["answer"]
    assert result["answer_strategy"] == "source_owned_scoped_extract"
    assert a["status"] == "partial" and a["source_ids"] == [1 if reverse else 0]
    assert "用户服务" in result["text"] and "仅适用" in result["text"]
    assert "ExecStart" in result["text"] and "WorkingDirectory" in result["text"]
    assert "按需" in result["text"] and "可选" in result["text"]
    assert "通常" not in result["text"]
    assert "解释器" in a["follow_up"] and "报错" in a["follow_up"]
    assert "不能" in a["follow_up"]
    assert not any(s["command"] for s in [a["conclusion"]] + a["points"])


@pytest.mark.parametrize(
    "mutation",
    [
        "scope",
        "environment",
        "directory",
        "journal",
        "unverified",
        "wrong_kind",
        "additional",
    ],
)
def test_incomplete_or_extra_sources_keep_original_qa(mutation):
    sources = copy.deepcopy(CASE["sources"])
    if mutation == "unverified":
        sources[0]["verified"] = False
    elif mutation == "wrong_kind":
        sources[0]["source_kind"] = "community"
    elif mutation == "additional":
        sources.append(
            {"path": "service-import-diagnosis.md", "content": "补充实际诊断材料"}
        )
    else:
        token = {
            "scope": "以 systemd 用户服务为例",
            "environment": "如需设置环境变量",
            "directory": "WorkingDirectory",
            "journal": "journalctl --user -u demo.service",
        }[mutation]
        sources[0]["content"] = sources[0]["content"].replace(token, "已修订")
    assert source_extract_answer(CASE["question"], sources) is None


@pytest.mark.parametrize(
    "question",
    [
        "Bianbu如何创建systemd用户服务运行Python脚本？",
        "Bianbu终端Python找不到依赖，如何安装？",
        "Bianbu systemd Python系统级服务找不到依赖，需要哪些命令？",
        "K3 OpenHarmony Python systemd找不到依赖怎么办？",
        "解释虚拟环境" + runtime.REFERENCE_MARKER + CASE["question"],
    ],
)
def test_other_workflows_are_not_rerouted(question):
    assert source_extract_answer(question, CASE["sources"]) is None
