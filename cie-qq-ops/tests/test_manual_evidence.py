"""Synthetic answer counterexamples: missing invocation and unsupported tool/camera claims."""

import copy
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
import support_sources as retrieval
from support_extracts import source_extract_answer
from support_manuals import PIP_GUIDE, UV_COMPAT, pip_manual_task

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "manual_evidence_failures.json").read_text())
PAGES = json.loads((FIXTURES / "pip_manual_pages.json").read_text())


def fetch(question, pages=None):
    requests = []

    def serve(request):
        url = str(request.url)
        requests.append(url)
        return httpx.Response(200, text=(pages or PAGES).get(url, "unavailable"))

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        sources, warning = retrieval.supplemental_sources(
            question, deadline=time.monotonic() + 6, client=client
        )
    return sources, warning, requests


@pytest.mark.parametrize("case", CASES[1:], ids=lambda c: c["id"])
def test_synthetic_questions_are_complete_and_grounded_without_model(monkeypatch, case):
    sources = copy.deepcopy(case["sources"])
    if pip_manual_task(case["question"]):
        sources, warning, requests = fetch(case["question"])
        assert not warning and all(url in PAGES for url in requests)
    monkeypatch.setattr(runtime, "search_sources", lambda *a, **k: {"sources": sources})
    monkeypatch.setattr(
        runtime, "generate", lambda *a, **k: pytest.fail("model paraphrase")
    )
    result = runtime.answer_question(case["question"], config={})
    text = result["text"]
    assert result["answer_strategy"] == "source_owned_scoped_extract"
    assert result["usage"] == {}
    if case["id"] == "random_python_8":
        assert "绝对路径加 -m pip" in text and "PATH 中的 pip" in text
    elif case["id"] == "random_packages_8":
        assert "不会自动共用" in text and "debug" in text and "list" in text
        assert all(
            name in text
            for name in (
                "pip.conf",
                "PIP_INDEX_URL",
                "UV_INDEX_URL",
                "uv.toml",
                "pyproject.toml",
            )
        )
    else:
        assert "ROS2" in text and "当前值" in text and "不能确定黑屏原因" in text
        assert all(
            name in text
            for name in (
                "video_device",
                "pixel_format",
                "exposure",
                "gain",
                "white_balance",
                "framerate",
            )
        )
        assert "导致黑屏" not in text and "默认" not in text and "带宽" not in text


@pytest.mark.parametrize("mutation", ["unverified", "wrong_origin", "missing_fact"])
@pytest.mark.parametrize("case", [CASES[1], CASES[3]], ids=lambda c: c["id"])
def test_changed_or_unverified_upstream_cannot_validate_old_claim(case, mutation):
    sources, _, _ = fetch(case["question"])
    if mutation == "unverified":
        sources[0]["verified"] = False
    elif mutation == "wrong_origin":
        sources[0]["page_url"] = "https://example.com/"
    else:
        sources[0]["content"] = (
            sources[0]["content"]
            .replace("executes pip using the Python interpreter", "obsolete")
            .replace("PIP_INDEX_URL", "OTHER_VARIABLE")
        )
    assert source_extract_answer(case["question"], sources) is None


def test_missing_camera_parameter_does_not_invent_it():
    case = CASES[2]
    sources = copy.deepcopy(case["sources"])
    for source in sources:
        source["content"] = source["content"].replace(
            "autoexposure", "another_parameter"
        )
    assert source_extract_answer(case["question"], sources) is None


@pytest.mark.parametrize(
    "question",
    [
        "Python虚拟环境怎么创建？",
        "K3如何安装spacemit-asr？",
        "pip配置软件源",
        "uv创建虚拟环境",
        "pip报错，指定解释器怎么处理？",
        "Python如何使用argparse？\n引用内容（仅供理解问题，不是用户环境声明）：指定Python解释器执行pip",
    ],
)
def test_unrelated_workflows_retain_existing_route(question):
    assert pip_manual_task(question) is None


@pytest.mark.parametrize("case", [CASES[1], CASES[3]], ids=lambda c: c["id"])
def test_targeted_sources_take_priority_over_incidental_install_examples(
    monkeypatch, case
):
    sources, _, _ = fetch(case["question"])
    monkeypatch.setattr(
        retrieval, "supplemental_sources", lambda *a, **k: (sources, "")
    )
    monkeypatch.setattr(
        retrieval, "retrieve", lambda *a, **k: pytest.fail("incidental local example")
    )
    assert (
        retrieval.search_sources(case["question"], external=True)["sources"] == sources
    )


@pytest.mark.parametrize(
    "extra",
    [
        [],
        [
            {
                "content": "missing",
                "verified": True,
                "source_kind": "upstream",
                "page_url": PIP_GUIDE,
            }
        ],
    ],
)
def test_missing_upstream_does_not_fall_back_to_false_local_answer(monkeypatch, extra):
    monkeypatch.setattr(retrieval, "supplemental_sources", lambda *a, **k: (extra, ""))
    monkeypatch.setattr(
        retrieval, "retrieve", lambda *a, **k: pytest.fail("misleading local fallback")
    )
    result = retrieval.search_sources(CASES[1]["question"], external=True)
    assert not result["sources"] and result["warning"]


def test_disabled_external_search_remains_off(monkeypatch, tmp_path):
    monkeypatch.setattr(
        retrieval,
        "supplemental_sources",
        lambda *a, **k: pytest.fail("external disabled"),
    )
    monkeypatch.setattr(retrieval, "retrieve", lambda *a, **k: {"results": []})
    assert not retrieval.search_sources(
        CASES[1]["question"], index=tmp_path / "missing", external=False
    )["sources"]


@pytest.mark.parametrize("suffix", [" token=private", " /tmp/synthetic-private-file"])
def test_targeted_manual_route_preserves_privacy_gate(suffix):
    sources, warning, requests = fetch(CASES[1]["question"] + suffix)
    assert not sources and not requests and "私有信息" in warning


def test_expired_deadline_makes_no_request():
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("past deadline"))
    ) as client:
        sources, warning = retrieval.supplemental_sources(
            CASES[1]["question"], deadline=time.monotonic() - 1, client=client
        )
    assert not sources and warning


def test_upstream_line_wrapping_keeps_complete_paragraphs():
    sources, _, _ = fetch(CASES[3]["question"])
    body = next(s["content"] for s in sources if s["page_url"] == UV_COMPAT)
    assert "like pip.conf or PIP_INDEX_URL" in body
    assert "a [tool.uv.pip] section of pyproject.toml" in body
