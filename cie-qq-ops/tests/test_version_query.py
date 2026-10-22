"""Version inspection must retain available evidence and its platform scope."""

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from retrieval import create_index
from support_extracts import source_extract_answer
from support_sources import search_sources

SOURCE = json.loads(
    (Path(__file__).parent / "fixtures/version_query_source.json").read_text()
)
QUESTION = "Bianbu系统如何查看当前系统版本和内核版本？"


def test_version_query_recalls_runtime_kernel_not_image_root(tmp_path):
    docs = [
        dict(SOURCE, id="rt", body=SOURCE["content"], source_url=""),
        dict(
            id="image",
            source_url="",
            path="software/SDK/bianbu/system_integration/iso_image.md",
            title="系统版本",
            body="# Bianbu系统版本\nexport CODENAME=$(grep VERSION_CODENAME $R/etc/os-release)",
            source_kind="official",
            page_url="https://www.spacemit.com/image",
        ),
    ]
    docs.append(dict(id="ethercat", source_url="", source_kind="official",
                     path="software/SDK/buildroot/k3_buildroot/device/peripheral_driver/22-EtherCAT.md",
                     title="EtherCAT", page_url="https://www.spacemit.com/ethercat",
                     body="# EtherCAT\n介绍 K3 EtherCAT 主站功能。\n## 验证内核版本\n```shell\nuname -r\n```\n输出中应包含 rt 字样"))
    index = tmp_path / "docs.sqlite"
    create_index(index, docs, datetime.now(timezone.utc).isoformat())
    found = search_sources(QUESTION, index=index)["sources"]
    assert [s["path"] for s in found] == [SOURCE["path"]]
    answer = source_extract_answer(QUESTION, found)
    assert answer["status"] == "partial"
    assert answer["points"][0]["command"] == "uname -r"
    assert "K3" in answer["conclusion"]["text"]
    assert "发行版本" in answer["follow_up"]
    assert "/etc/os-release" not in str(answer)


@pytest.mark.parametrize(
    "mutation", ["verified", "source_kind", "scope", "command", "extra"]
)
def test_missing_or_additional_evidence_keeps_normal_qa(mutation):
    source = copy.deepcopy(SOURCE)
    sources = [source]
    if mutation == "verified":
        source["verified"] = False
    elif mutation == "source_kind":
        source["source_kind"] = "community"
    elif mutation == "scope":
        source["content"] = source["content"].replace("Bianbu（K3 平台）", "other")
    elif mutation == "command":
        source["content"] = source["content"].replace("uname -r", "changed")
    else:
        sources.append(dict(source, path="new-release-query.md"))
    assert source_extract_answer(QUESTION, sources) is None


@pytest.mark.parametrize(
    "question",
    [
        "Bianbu如何升级内核版本？",
        "Bianbu如何查询Python版本？",
        "OpenHarmony如何查看内核版本？",
        "Bianbu内核版本不兼容驱动如何修复？",
    ],
)
def test_other_workflows_keep_normal_qa(question):
    assert source_extract_answer(question, [SOURCE]) is None


def test_stale_index_does_not_extract_even_when_source_matches(tmp_path):
    index = tmp_path / "docs.sqlite"
    create_index(index, [dict(SOURCE, id="rt", body=SOURCE["content"], source_url="")],
                 (datetime.now(timezone.utc) - timedelta(hours=37)).isoformat())
    found = search_sources(QUESTION, index=index)
    assert found["warning"]
    assert not found["sources"][0]["verified"]
    assert source_extract_answer(QUESTION, found["sources"]) is None
