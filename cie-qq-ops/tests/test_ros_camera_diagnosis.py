"""API workflow evidence is not an observation of a capture failure."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from support_reply import InvalidAnswer, validate_answer, validate_camera_inference
from support_ros_camera import ros_camera_diagnostic_answer

CASE = json.loads(
    (Path(__file__).parent / "fixtures/ros_camera_failure.json").read_text()
)


def test_synthetic_original_delivered_false_cause_is_rejected():
    with pytest.raises(InvalidAnswer, match="unsupported_camera_capture_inference"):
        validate_answer(CASE["structured_answer"], CASE["sources"])


@pytest.mark.parametrize("reverse", [False, True])
def test_unconfirmed_camera_retains_both_routes_and_no_cause(monkeypatch, reverse):
    sources = copy.deepcopy(CASE["sources"][::-1] if reverse else CASE["sources"])
    monkeypatch.setattr(runtime, "search_sources", lambda *a, **k: {"sources": sources})
    monkeypatch.setattr(
        runtime, "generate", lambda *a, **k: pytest.fail("workflow paraphrase used")
    )
    r = runtime.answer_question(CASE["question"], config={})
    assert r["answer"]["status"] == "partial"
    assert all(
        x in r["text"]
        for x in [
            "若使用 MIPI",
            "若使用文档的 USB",
            "/image_raw",
            "JdkFrameMsg",
            "不能仅凭",
            "启动命令",
        ]
    )
    assert "说明采集" not in r["text"]
    assert not any(p["command"] for p in r["answer"]["points"])


@pytest.mark.parametrize(
    "mutation", ["unverified", "missing_usb", "missing_fields", "extra"]
)
def test_incomplete_evidence_keeps_normal_qa(mutation):
    s = copy.deepcopy(CASE["sources"])
    if mutation == "unverified":
        s[0]["verified"] = False
    elif mutation == "missing_usb":
        s.pop()
    elif mutation == "missing_fields":
        s[0]["content"] = s[0]["content"].replace(
            "width/height/stride/pix_format/is_dma/dma_fd/data", "changed"
        )
    else:
        s.append(dict(s[0], path="camera-error-observation.md"))
    assert ros_camera_diagnostic_answer(CASE["question"], s) is None


@pytest.mark.parametrize(
    "question",
    [
        "K1 ROS2 MIPI相机camera_node话题存在但无图像，如何检查？",
        "K1 ROS2 USB相机/image_raw话题存在但无图像，如何检查？",
    ],
)
def test_explicit_implementation_preserves_model_qa(monkeypatch, question):
    answer = copy.deepcopy(CASE["structured_answer"])
    answer["conclusion"]["text"] = (
        "以下为MIPI节点API描述的采集、帧传递与显示职责；实际原因需日志确认。"
    )
    if "USB" in question:
        answer["conclusion"] = {
            "text": "USB示例在主机上使用rqt_image_view查看/image_raw。",
            "evidence": [
                {
                    "source_id": 1,
                    "quote": "确保主机已安装 ROS2 Humble，使用 `rqt_image_view` 工具订阅 USB 相机采集的图像：",
                }
            ],
        }
        answer["points"] = []
        answer["source_ids"] = [1]
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": CASE["sources"]}
    )
    monkeypatch.setattr(runtime, "generate", lambda *a, **k: (answer, {}))
    r = runtime.answer_question(question, config={})
    assert r["answer"]["status"] == "answered"
    assert r.get("answer_strategy") != "source_owned_scoped_extract"
    assert ("/image_raw" if "USB" in question else "/camera_frames") in r["text"]


@pytest.mark.parametrize(
    "text",
    [
        "话题存在但无图像，不能说明采集失败。",
        "话题存在但无图像，可能说明采集异常，需日志确认。",
        "话题存在但无图像说明采集失败的推断不成立。",
        "日志明确V4L2打开失败，应先检查该设备。",
    ],
)
def test_uncertainty_negation_and_actual_logs_are_not_rejected(text):
    validate_camera_inference(text)
