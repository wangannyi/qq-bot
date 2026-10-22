"""Keep ASR backend, model weights, and capture-device claims source-scoped."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from support_extracts import source_extract_answer

QUESTION = "K3部署语音识别前，如何区分识别引擎、模型权重和音频采集设备？"
LINKSEE = {
    "title": "3.2 轮式机器人 Linksee",
    "path": "software/SDK/ros/k3/03-参考方案/3.2-轮式机器人Linksee.md",
    "page_url": "https://www.spacemit.com/community/document/info?lang=zh&nodepath=software/SDK/ros/k3/03-%E5%8F%82%E8%80%83%E6%96%B9%E6%A1%88/3.2-%E8%BD%AE%E5%BC%8F%E6%9C%BA%E5%99%A8%E4%BALinksee.md",
    "source_kind": "official",
    "verified": True,
    "content": """面向 `k3-com260` 板卡的 `linksee` 机器人整机方案。
语音设备参数说明如下：
- `asr.backend`：ASR 后端，可选 `sensevoice` 或 `qwen3_asr`。
- `asr.device_name`：采集设备名称。非空时优先按名称解析当前设备编号；未配置时使用 `asr.device`。
- `asr.device`：采集设备编号。
- `rate`、`playback_rate` 和 `channels`：录音与播放格式，应与所选设备支持的格式一致。
""",
}


def test_distinguishes_engine_device_and_unknown_weight_loading():
    answer = source_extract_answer(QUESTION, [LINKSEE])

    assert answer["status"] == "partial"
    assert answer["source_ids"] == [0]
    rendered = " ".join(
        [answer["conclusion"]["text"]]
        + [point["text"] for point in answer["points"]]
        + [answer["follow_up"]]
    )
    assert "sensevoice" in rendered and "qwen3_asr" in rendered
    assert "asr.device_name" in rendered and "asr.device 编号" in rendered
    assert "不能据此断言权重随后端自动加载" in rendered
    assert "模型权重随所选后端加载" not in rendered


def test_real_question_route_uses_scoped_extract_without_model(monkeypatch):
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": [LINKSEE]}
    )
    monkeypatch.setattr(
        runtime,
        "generate",
        lambda *a, **k: pytest.fail("ASR field comparison must use the source extract"),
    )

    result = runtime.answer_question(QUESTION, config={})

    assert result["answer_strategy"] == "source_owned_scoped_extract"
    assert result["answer"]["status"] == "partial"
    assert "该参数段未说明模型权重的配置或加载方式" in result["text"]


def test_ignores_adjacent_document_and_cites_only_linksee():
    adjacent = {
        **LINKSEE,
        "title": "Reachy Mini",
        "path": "software/SDK/ros/k3/03-参考方案/3.1-桌面机器人Reachy-mini.md",
        "content": "K3语音机器人依赖模型组件。",
    }

    answer = source_extract_answer(QUESTION, [adjacent, LINKSEE])

    assert answer["status"] == "partial"
    assert answer["source_ids"] == [1]


@pytest.mark.parametrize(
    "mutation",
    [
        {"verified": False},
        {"source_kind": "community"},
        {"path": "software/SDK/ros/k3/04-AI与算法/4.4-LLM.md"},
        {"content": "K3 Linksee ASR backend and audio device."},
    ],
)
def test_requires_verified_linksee_field_evidence(mutation):
    source = {**LINKSEE, **mutation}

    assert source_extract_answer(QUESTION, [source]) is None


def test_does_not_capture_installation_or_performance_questions():
    assert (
        source_extract_answer("K3怎么安装qwen3_asr并下载模型权重？", [LINKSEE]) is None
    )
    assert source_extract_answer("K3上的qwen3_asr性能如何？", [LINKSEE]) is None
