"""Regression coverage for synthetic examples of incomplete answers."""

from support_extracts import source_extract_answer
from support_reply import render_answer
from support_sources import search_sources


def test_k1_bianbu_llm_steps_are_retrieved_and_complete(synthetic_index):
    question = "K1 Bianbu 2.3.5上部署LLM，官方有哪些步骤？"
    sources = search_sources(question, external=False, index=synthetic_index)["sources"]

    assert [s["path"] for s in sources] == [
        "software/SDK/ros/k1/06_Robot_development/6.4_Robot_Application_Module/"
        "6.4.2_Human-Robot_Interaction/2_LLM_Applications.md",
        "ai/application_tools/llmsdk.md",
    ]
    answer = source_extract_answer(question, sources)

    assert answer is not None
    assert answer["status"] == "partial"
    assert "Bianbu 2.3.5" in answer["follow_up"]
    text = render_answer(answer, sources)
    for required in (
        "sudo apt update",
        "安装文档列出的系统依赖",
        "llm_setup.sh",
        "配置文档中的pip源",
        "python3 -m venv ~/ai_env",
        "source ~/ai_env/bin/activate",
        "pip install -r /opt/bros/humble/share/rdk_chat/requirements.txt",
        "source /opt/bros/humble/setup.bash",
        "ros2 launch rdk_chat chat_service.launch.py",
    ):
        assert required in text
    for required_source_detail in (
        "libopenblas-dev",
        "python3-spacemit-ort",
        "pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/",
        "pip config set global.extra-index-url https://git.spacemit.com/api/v4/projects/33/packages/pypi/simple",
    ):
        assert required_source_detail in sources[0]["content"]
    statements = [answer["conclusion"], *answer["points"]]
    assert (
        sum(len(x["text"]) + len(x["command"]) for x in statements)
        + len(answer["follow_up"])
        <= 450
    )
    assert "ollama pull" not in text


def test_k1_llm_extract_requires_the_versioned_question_and_complete_source(synthetic_index):
    question = "K1 Bianbu 2.3.5上部署LLM，官方有哪些步骤？"
    sources = search_sources(question, external=False, index=synthetic_index)["sources"]
    without_primary = [
        s
        for s in sources
        if s["path"]
        != "software/SDK/ros/k1/06_Robot_development/6.4_Robot_Application_Module/"
        "6.4.2_Human-Robot_Interaction/2_LLM_Applications.md"
    ]
    unverified = [dict(s, verified=False) for s in sources]

    assert source_extract_answer(question, without_primary) is None
    assert (
        source_extract_answer("K1 Bianbu 2.3.4上部署LLM，官方有哪些步骤？", sources)
        is None
    )
    assert source_extract_answer(question, unverified) is None


def test_k1_k3_positioning_uses_product_overviews_not_technical_chapters(synthetic_index):
    question = "介绍K3平台，和K1的定位有什么区别？"
    sources = search_sources(question, external=False, index=synthetic_index)["sources"]

    assert [s["path"] for s in sources] == [
        "ecohardware/k3_series/k3_pico/root_overview.md",
        "ecohardware/k1_series/k1_muse_pi_pro/root_overview.md",
    ]
    answer = source_extract_answer(question, sources)

    assert answer is not None
    assert answer["status"] == "partial"
    assert "板卡" in answer["follow_up"]
    text = render_answer(answer, sources)
    assert "60 TOPS" in text and "科学计算" in text
    assert "低功耗" in text and "空间紧凑" in text
    assert "Timer" not in text and "uboot_key_prv" not in text


def test_platform_comparison_route_is_narrow(synthetic_index):
    technical = "K1和K3的CPU接口有哪些区别？"
    unrelated = "K3平台有哪些特点？"
    assert source_extract_answer(technical, []) is None
    assert source_extract_answer(unrelated, []) is None
