"""Headless capture/display questions must retain the FAQ example's scope."""

from support_extracts import source_extract_answer
from support_reply import render_answer
from support_sources import headless_camera_question, search_sources

QUESTION = "K1无桌面环境时如何区分相机采集失败与图形显示失败？"


def test_headless_question_retrieves_actual_capture_display_distinction(synthetic_index):
    sources = search_sources(QUESTION, external=False, index=synthetic_index)["sources"]
    assert [s["path"] for s in sources] == [
        "software/SDK/bianbu/user_guide/LXQt/Camera_FAQ.md"
    ]
    answer = source_extract_answer(QUESTION, sources)
    assert answer["status"] == "partial"
    text = render_answer(answer, sources)
    assert "Wayland" in text and "SSH" in text and "jdk_cam" in text
    assert "不能外推为你的相机采集已经成功" in text
    assert "通用分层检测方法" in answer["follow_up"]


def test_missing_or_unverified_distinction_does_not_produce_static_answer(synthetic_index):
    sources = search_sources(QUESTION, external=False, index=synthetic_index)["sources"]
    assert source_extract_answer(QUESTION, [dict(sources[0], verified=False)]) is None
    damaged = dict(
        sources[0],
        content=sources[0]["content"].replace(
            "但采图和日志流程本身不受影响", "内容已变更"
        ),
    )
    assert source_extract_answer(QUESTION, [damaged]) is None


def test_other_boards_and_ros_keep_their_existing_paths(synthetic_index):
    for question in (
        QUESTION.replace("K1", "K3"),
        "K1 ROS2无桌面环境时如何区分相机采集失败与图形显示失败？",
        "K1 Buildroot无桌面环境时如何区分相机采集失败与图形显示失败？",
        "K1相机如何采集图像？",
    ):
        assert not headless_camera_question(question)
