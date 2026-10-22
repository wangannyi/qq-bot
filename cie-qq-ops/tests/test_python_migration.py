"""An offered rebuild needs complete commands even in a partial migration answer."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import support_runtime as runtime
from support_extracts import source_extract_answer
from support_reply import InvalidAnswer, validate_answer

CASE = json.loads(
    (Path(__file__).parent / "fixtures/python_migration_failure.json").read_text()
)


def test_synthetic_original_partial_rebuild_is_incomplete():
    answer = validate_answer(CASE["structured_answer"], CASE["sources"])
    with pytest.raises(InvalidAnswer, match="matching_create_and_activate"):
        runtime.validate_environment_steps(CASE["question"], answer, CASE["sources"])


def test_migration_gets_complete_scoped_rebuild(monkeypatch):
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": CASE["sources"]}
    )
    monkeypatch.setattr(
        runtime,
        "generate",
        lambda *a, **k: pytest.fail("unsupported compatibility inference"),
    )
    r = runtime.answer_question(CASE["question"], config={})
    assert r["answer"]["status"] == "partial"
    assert [p["command"] for p in r["answer"]["points"]] == [
        "sudo apt install python3-virtualenv",
        "virtualenv myenv",
        "source myenv/bin/activate",
    ]
    assert "未给出" in r["text"] and "若目标为 Bianbu" in r["text"]
    assert "不能直接运行" not in r["text"]


@pytest.mark.parametrize(
    "question", ["已有myenv环境如何激活？", "虚拟环境原理是什么？"]
)
def test_activation_only_and_concept_do_not_require_creation(question):
    answer = {
        "status": "partial",
        "points": [{"text": "激活现有环境。", "command": "source myenv/bin/activate"}],
    }
    runtime.validate_environment_steps(question, answer, CASE["sources"])
    assert source_extract_answer(question, CASE["sources"]) is None


@pytest.mark.parametrize("mutation", ["command", "verified", "path"])
def test_missing_source_proof_retains_normal_qa(mutation):
    s = copy.deepcopy(CASE["sources"])
    if mutation == "command":
        s[0]["content"] = s[0]["content"].replace("virtualenv myenv", "changed")
    elif mutation == "verified":
        s[0]["verified"] = False
    else:
        s[0]["path"] = "unknown.md"
    assert source_extract_answer(CASE["question"], s) is None
