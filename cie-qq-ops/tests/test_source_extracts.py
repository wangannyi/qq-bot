"""Synthetic counterexamples must keep primary-source wording, with narrow routing."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from support_extracts import source_extract_answer
from support_reply import render_answer

CASES = json.loads(
    (Path(__file__).parent / "fixtures/source_overclaim.json").read_text()
)
CAMERA = next(x for x in CASES if x["id"] == "random_camera_14")
EVENT = next(x for x in CASES if x["id"] == "random_competition_17")
LLM_QUESTION = "K3 Bianbu 4.0.1上怎么部署LLM？"
OLLAMA_CONTENT = """## 平台支持情况
| K3 Bianbu LXQT/GNOME | 不支持加速 |

## 安装
```shell
sudo apt update
sudo apt install spacemit-ollama-toolkit
```

验证安装：
```shell
ollama list
```
最后输出 `NAME ID SIZE MODIFIED` 表示安装成功。

验证版本（确保为 0.0.8 或以上版本）：
```shell
sudo apt show spacemit-ollama-toolkit
```
确认版本为 0.0.8 或以上，以支持新的模型格式和直接拉取功能。

从 spacemit-ollama-toolkit 0.0.8 版本开始，新增支持 **q4_K_M** 和 **q4_1** 模型格式，可以直接使用 `ollama pull` 命令从 Ollama 官网拉取 q4_K_M 格式的模型，并享受加速功能：
```shell
ollama pull qwen3:0.6b
```
```shell
ollama run qwen3:0.6b
```
"""
LLM_SOURCES = [
    {
        "title": "Ollama",
        "path": "ai/application_tools/ollama.md",
        "page_url": "https://www.spacemit.com/community/document/info?nodepath=ai/application_tools/ollama.md&lang=zh",
        "source_kind": "official",
        "verified": True,
        "content": OLLAMA_CONTENT,
    },
    {
        "title": "4.4 LLM",
        "path": "software/SDK/ros/k3/04-AI与算法/4.4-LLM.md",
        "page_url": "https://www.spacemit.com/community/document/info?nodepath=software/SDK/ros/k3/04-AI%E4%B8%8E%E7%AE%97%E6%B3%95/4.4-LLM.md",
        "source_kind": "official",
        "verified": True,
        "content": "K3 LLM 示例使用 llama.cpp。",
    },
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_synthetic_questions_use_source_spans_without_model_overclaim(monkeypatch, case):
    monkeypatch.setattr(
        runtime,
        "search_sources",
        lambda *a, **k: {"sources": copy.deepcopy(case["sources"])},
    )
    monkeypatch.setattr(
        runtime,
        "generate",
        lambda *a, **k: pytest.fail("known extract went through model"),
    )
    result = runtime.answer_question(case["question"], config={})
    assert result["answer_strategy"] == "source_owned_scoped_extract"
    assert result["usage"] == {}
    answer = result["answer"]
    assert all(not x["command"] for x in [answer["conclusion"]] + answer["points"])
    text = result["text"]
    if case is CAMERA:
        assert "udev" in text and "序列号或 USB 路径" in text
        assert "VideoCapture" not in text and "Python 使用 USB 相机" not in text
        assert answer["source_ids"] == [0]
        assert "完整设备路径" not in text
    else:
        assert answer["status"] == "partial" and "组委会" in answer["follow_up"]
        assert "不依赖云端API" in text and "申请8G实例" in text
        assert "而非云端推理" not in text and "可推断" not in text
        assert "Qwen3.5-2B-Instruct" in text and "Qwen3.5-4B-Instruct" in text


@pytest.mark.parametrize(
    "question",
    [
        "K1 Python如何创建虚拟环境？",
        "K1 USB相机如何使用VideoCapture打开设备？",
        "K1 USB相机插拔编号变化，给出udev规则代码",
        "K3 USB相机设备编号改变，如何避免固定编号？",
        "K1 OpenHarmony USB相机编号变化如何避免？",
        "K1 USB相机无法打开，如何检查权限？",
        "CIE赛题1的云端推理有哪些限制？",
        "2027 CIE赛题对云端推理有哪些限制？",
        "CIE赛题2提交推理代码有哪些要求？",
        "CIE赛题2如何优化推理速度？",
        "K1 USB相机插拔编号改变会影响帧率吗？",
        "CIE赛题2推理吞吐量有哪些要求？",
        "CIE赛题20的云端推理有哪些限制？",
    ],
)
def test_existing_other_workflows_do_not_enter_extract_route(question):
    assert source_extract_answer(question, CAMERA["sources"]) is None
    assert source_extract_answer(question, EVENT["sources"]) is None


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
@pytest.mark.parametrize(
    "mutation", ["missing_span", "unverified", "wrong_source_kind"]
)
def test_absent_or_untrusted_evidence_retains_normal_qa(case, mutation):
    sources = copy.deepcopy(case["sources"])
    s = sources[0]
    if mutation == "missing_span":
        s["content"] = (
            s["content"]
            .replace("基于序列号或 USB 路径创建持久命名", "参考其他资料")
            .replace("不依赖云端API", "以最新补充规定为准")
        )
    elif mutation == "unverified":
        s["verified"] = False
    else:
        s["source_kind"] = "community"
    assert source_extract_answer(case["question"], sources) is None


def test_event_supplement_does_not_get_silently_ignored():
    supplement = {
        "source_kind": "event_supplement",
        "content": "新增联网限制需逐条复核",
    }
    assert (
        source_extract_answer(EVENT["question"], EVENT["sources"] + [supplement])
        is None
    )


def test_source_ids_follow_reordered_camera_sources_and_plain_links_survive():
    sources = list(reversed(CAMERA["sources"]))
    answer = source_extract_answer(CAMERA["question"], sources)
    assert answer["source_ids"] == [1]
    assert sources[1]["page_url"] in render_answer(answer, sources, markdown=False)
    assert sources[0]["page_url"] not in render_answer(answer, sources, markdown=False)


def test_quoted_question_cannot_activate_an_unrelated_route():
    query = "解释Python虚拟环境" + runtime.REFERENCE_MARKER + CAMERA["question"]
    assert source_extract_answer(query, CAMERA["sources"]) is None


def test_k3_bianbu_llm_deployment_extract_includes_install_pull_and_run_steps(
    monkeypatch,
):
    monkeypatch.setattr(
        runtime,
        "search_sources",
        lambda *a, **k: {"sources": copy.deepcopy(LLM_SOURCES)},
    )
    monkeypatch.setattr(
        runtime,
        "generate",
        lambda *a, **k: pytest.fail(
            "source-complete deployment must not use model summary"
        ),
    )

    result = runtime.answer_question(LLM_QUESTION, config={})

    assert result["answer_strategy"] == "source_owned_scoped_extract"
    assert result["answer"]["status"] == "partial"
    assert "ollama pull qwen3:0.6b" in result["text"]
    assert "ollama run qwen3:0.6b" in result["text"]
    assert "Bianbu 4.0.1 的适用性" in result["text"]
    assert "加速能力" in result["text"]
    assert result["answer"]["source_ids"] == [0]


@pytest.mark.parametrize(
    "question",
    [
        "K1 Bianbu 4.0.1上怎么部署LLM？",
        "K3 Buildroot上怎么部署LLM？",
        "K3 Bianbu 4.0.1上如何评估LLM性能？",
    ],
)
def test_k3_bianbu_deployment_extract_does_not_capture_other_scopes(question):
    assert source_extract_answer(question, LLM_SOURCES) is None


def test_k3_bianbu_deployment_extract_requires_verified_complete_ollama_source():
    sources = copy.deepcopy(LLM_SOURCES)
    sources[0]["content"] = sources[0]["content"].replace(
        "ollama run qwen3:0.6b", "ollama inspect qwen3:0.6b"
    )
    assert source_extract_answer(LLM_QUESTION, sources) is None

    sources = copy.deepcopy(LLM_SOURCES)
    sources[0]["verified"] = False
    assert source_extract_answer(LLM_QUESTION, sources) is None
