"""A source manifest must not become an unsupported documentation version map."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from support_extracts import source_extract_answer
from support_reply import render_answer, validate_answer

CASE = json.loads(
    (Path(__file__).parent / "fixtures/document_version_failure.json").read_text()
)


def test_old_structural_validation_does_not_prove_semantic_correctness():
    assert (
        validate_answer(CASE["structured_answer"], CASE["sources"])["status"]
        == "answered"
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_synthetic_question_keeps_manifest_scope_and_requests_missing_documents(
    monkeypatch, reverse
):
    sources = copy.deepcopy(CASE["sources"][::-1] if reverse else CASE["sources"])
    monkeypatch.setattr(runtime, "search_sources", lambda *a, **k: {"sources": sources})
    monkeypatch.setattr(
        runtime,
        "generate",
        lambda *a, **k: pytest.fail("unsupported mapping via model"),
    )
    result = runtime.answer_question(CASE["question"], config={})
    assert result["answer_strategy"] == "source_owned_scoped_extract"
    answer = result["answer"]
    assert answer["status"] == "partial"
    assert "不能" in answer["follow_up"] and "链接" in answer["follow_up"]
    assert "SDK 源码" in answer["conclusion"]["text"]
    assert answer["source_ids"] == [1 if reverse else 0]
    assert not any(x["command"] for x in [answer["conclusion"]] + answer["points"])
    text = render_answer(answer, sources, markdown=False)
    assert CASE["sources"][0]["page_url"] in text
    assert CASE["sources"][1]["page_url"] not in text


@pytest.mark.parametrize(
    "mutation", ["missing", "unverified", "community", "wrong_path", "supplement"]
)
def test_missing_or_additional_evidence_retains_normal_qa(mutation):
    sources = copy.deepcopy(CASE["sources"])
    if mutation == "missing":
        sources[0]["content"] = "源码下载请参考其他版本。"
    elif mutation == "unverified":
        sources[0]["verified"] = False
    elif mutation == "community":
        sources[0]["source_kind"] = "community"
    elif mutation == "wrong_path":
        sources[0]["path"] = "some-other-manifest.md"
    else:
        sources.append(
            {"path": "documentation-version-map.md", "content": "补充文档版本映射表"}
        )
    assert source_extract_answer(CASE["question"], sources) is None


@pytest.mark.parametrize(
    "question",
    [
        "K3 SDK源码如何按manifest下载不同版本？",
        "不同版本的开发文档操作不一致，请比较 https://example.com/v1 和 https://example.com/v2",
        "K3 v1.0和v2.0文档操作不一致，怎样选择？",
        "不同版本的开发文档操作不一致，如何编译SDK源码？",
        "解释Python虚拟环境" + runtime.REFERENCE_MARKER + CASE["question"],
    ],
)
def test_other_workflows_keep_their_existing_route(question):
    assert source_extract_answer(question, CASE["sources"]) is None
