"""Regression coverage for the Bianbu Python-guide official-answer path.

The community assistant's answer to ``bianbu上python使用指南`` is a Python
development guide, not a generic Bianbu overview.  QQ must therefore enter
the official website bridge and cite the current released Python guide even
when the website answer happens to attach a broad root-overview document.
"""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

import answer_policy as policy
import website_bridge as bridge
from test_summary_validation import fake_model, payload

QUESTION = "bianbu上python使用指南"
PYTHON_PATH = "software/SDK/bianbu/development/python.md"
PYTHON_GUIDE = (
    "https://www.spacemit.com/community/document/info?nodepath="
    "software/SDK/bianbu/development/python.md&lang=zh"
)
ROOT_OVERVIEW_PATH = "software/SDK/bianbu/root_overview.md"
ROOT_OVERVIEW = (
    "https://www.spacemit.com/community/document/info?nodepath="
    "software/SDK/bianbu/root_overview.md&lang=zh"
)

# Invented website-answer fixture for the canonical-source routing contract.  The
# generic Bianbu overview link models the stale/adjacent reference that must
# not replace the more specific current Python guide.
OFFICIAL_PYTHON_ANSWER = """合成网站答复：Bianbu Python 使用指南介绍 Python 3、apt 与虚拟环境。
示例使用 extra-index-url 和 pip config list；链接故意指向相邻总览，用于验证纠正逻辑。
参考资源：[Bianbu Development Guide](https://www.spacemit.com/community/document/info?nodepath=software/SDK/bianbu/root_overview.md&lang=zh)
"""


def source(title: str, url: str, path: str) -> dict:
    return {"title": title, "url": url, "verified": True, "canonical_path": path}


@pytest.fixture(autouse=True)
def clean_policy_state():
    policy._turns.clear()
    policy._clarifications.clear()
    yield
    policy._turns.clear()
    policy._clarifications.clear()


def test_python_guide_question_routes_through_official_bridge_not_generic_rag(monkeypatch):
    """A Bianbu Python question must use the website-answer path end-to-end."""
    asked = []
    monkeypatch.setattr(bridge, "enabled", lambda: True)
    monkeypatch.setattr(bridge, "event_question", lambda question: False)
    monkeypatch.setattr(
        bridge,
        "ask",
        lambda question: asked.append(question) or {
            "backend": "website", "query": question,
            "original_answer": OFFICIAL_PYTHON_ANSWER, "references": [],
        },
    )
    monkeypatch.setattr(bridge, "final_answer", lambda question, evidence, session_id: "官网 Python 答复")
    monkeypatch.setattr(bridge, "clarification", lambda *args: None)
    # If the website route regresses, transform() would fall through to this
    # generic RAG compositor.  It must never be reached for this question.
    monkeypatch.setattr(policy, "compose", lambda *args: (_ for _ in ()).throw(
        AssertionError("generic RAG must not compose a Bianbu Python guide answer")))

    policy.begin(session_id="python-guide", user_message=QUESTION, platform="qqbot")

    assert policy.transform(session_id="python-guide", platform="qqbot") == "官网 Python 答复"
    assert asked == [QUESTION]


def test_python_guide_question_curates_current_python_document_over_generic_overview(monkeypatch):
    """The source selected by a model cannot downgrade a Python guide to root overview."""
    current_tree = [
        {"name": "Bianbu Development Guide", "path": ROOT_OVERVIEW_PATH,
         "updateTime": "2026-08-01T08:00:00Z"},
        {"name": "Python 使用指南", "path": PYTHON_PATH,
         "updateTime": "2026-08-04T17:11:00Z"},
    ]
    monkeypatch.setattr(bridge, "_current_document_index", lambda client: current_tree)

    checked, notes = bridge.verified_references(
        [{"title": "Bianbu Development Guide", "url": ROOT_OVERVIEW}],
        Mock(),
        OFFICIAL_PYTHON_ANSWER,
        question=QUESTION,
    )
    cited = bridge.curated_references(QUESTION, [], checked, selected=0)

    assert [item["canonical_path"] for item in checked] == [ROOT_OVERVIEW_PATH, PYTHON_PATH]
    assert [item["url"] for item in cited] == [PYTHON_GUIDE]
    assert [item["title"] for item in cited] == ["Python 使用指南"]
    assert any("Python 使用指南" in note for note in notes)


def test_python_guide_question_never_falls_back_to_generic_overview_when_current_guide_is_missing():
    """No exact current Python guide means no citation, rather than a vague one."""
    only_overview = [source("Bianbu Development Guide", ROOT_OVERVIEW, ROOT_OVERVIEW_PATH)]

    assert bridge.curated_references(QUESTION, [], only_overview, selected=0) == []


def test_python_guide_summary_links_the_current_python_guide_not_the_root_overview(monkeypatch, synthetic_index):
    monkeypatch.setattr(bridge, "EVENT_RULES_INDEX", synthetic_index)
    """End-to-end summary publishing keeps the official Python guide identity."""
    fake_model(monkeypatch, [payload(
        "Bianbu默认预装Python；开发时建议使用虚拟环境，并按指南配置PyPI源后安装依赖。",
        source_id=0,
    )])
    evidence = {
        "original_answer": OFFICIAL_PYTHON_ANSWER,
        "references": [
            source("Bianbu Development Guide", ROOT_OVERVIEW, ROOT_OVERVIEW_PATH),
            source("Python 使用指南", PYTHON_GUIDE, PYTHON_PATH),
        ],
    }

    answer = bridge.summarize(QUESTION, evidence)

    assert f"[🔗Python 使用指南]({PYTHON_GUIDE})" in answer
    assert ROOT_OVERVIEW not in answer
    assert "Bianbu Development Guide" not in answer
    # The source can contain only an adjacent IDE/tutorial reply. The compact
    # QQ answer must instead be grounded in the verified current Python guide.
    assert "系统级 Python 包优先用 apt" in answer
    assert "虚拟环境" in answer
    assert "额外源" in answer and "pip config list" in answer
    assert "Bianbu v4.0默认预装Python 3.14.3" not in answer
