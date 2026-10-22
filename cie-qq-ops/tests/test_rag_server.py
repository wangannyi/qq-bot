import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import rag_server
from retrieval import citation, compatible, create_index, retrieve, split_sections


def doc(id, body="K1 部署大模型 llama.cpp", date="2026-01-01", **kw):
    return dict(id=id, path=id + ".md", title="K1大模型部署", page_url="https://www.spacemit.com/community/document?lang=zh",
                source_url="", updated_at=date, source_kind="official", body=body, **kw)


def test_complete_code_and_heading_ancestry():
    code = "```sh\n# a comment\n" + "echo safe\n" * 400 + "```"
    parts = split_sections("# K1\n## 编译\n说明\n\n" + code)
    assert any(code in text for _, text in parts)
    assert all(heading == "K1 / 编译" for heading, _ in parts)


def test_product_isolation():
    assert not compatible("K1烧录系统", "software/ros/k3/image.md", "K3镜像")
    assert compatible("K1", "software/common.md", "通用工具")
    assert compatible("K3", "software/ros/k3/image.md", "镜像")


def test_same_topic_prefers_newer_not_incompatible(tmp_path):
    path = tmp_path / "index.sqlite"
    docs = [doc("old", date="2024-01-01"), doc("new", date="2026-09-01")]
    other = doc("k3", date="2026-09-09")
    other["title"] = "K3大模型部署"
    docs.append(other)
    create_index(path, docs, "2026-09-09T00:00:00+00:00")
    hits = retrieve("K1 大模型部署", path)["results"]
    assert hits[0]["path"] == "new.md"
    assert all(x["path"] != "k3.md" for x in hits)


def test_unknown_query_returns_no_evidence(tmp_path):
    path = tmp_path / "index.sqlite"
    create_index(path, [doc("k1")], "2026-09-09T00:00:00+00:00")
    assert not retrieve("不存在的火星探测器XYZ999", path)["results"]


def test_citation_has_qq_link_marker_and_no_fabricated_url():
    assert citation(doc("k1")).startswith("[🔗")
    item = doc("rules")
    item["page_url"] = ""
    assert citation(item) == ""


def test_unavailable_reranker_is_explicit(monkeypatch):
    item = doc("test")
    item.update(heading="正文", content="K1 llama.cpp", fetched_at="now")
    monkeypatch.setattr(rag_server, "retrieve", lambda q: {"results": [item]})
    monkeypatch.setattr(rag_server, "model_credentials", lambda: (_ for _ in ()).throw(RuntimeError("private secret")))
    rag_server._cache.clear()
    result = rag_server.search_spacemit_docs("test query")
    assert result["evidence_status"] == "unverified"
    assert "private secret" not in str(result)
    assert "需要人工确认" in result["usage"]


def test_removed_documents_absent_from_new_generation(tmp_path):
    old, new = tmp_path / "old.sqlite", tmp_path / "new.sqlite"
    create_index(old, [doc("removed")], "2026-09-01T00:00:00+00:00")
    create_index(new, [doc("current")], "2026-09-09T00:00:00+00:00")
    new.replace(old)
    assert [x["path"] for x in retrieve("K1大模型部署", old)["results"]] == ["current.md"]


def test_limit_counts_documents_not_same_page_fragments(monkeypatch):
    item = doc("rules")
    item.update(heading="简介", content="背景介绍", fetched_at="now")
    scoring = dict(item, heading="评分", content="支持固定测试集的多轮推理")
    monkeypatch.setattr(rag_server, "retrieve", lambda q: {"results": [item, scoring]})
    monkeypatch.setattr(rag_server, "model_credentials", lambda: ({}, "test"))
    monkeypatch.setattr(rag_server, "semantic_rerank", lambda *a: {"results": [item, scoring], "evidence_status": "partial"})
    rag_server._cache.clear()
    result = rag_server.search_spacemit_docs("score table", limit=1)
    assert result["count"] == 1
    assert "支持固定测试集" in result["results"][0]["excerpt"]


def test_parent_section_carries_complete_child_steps(tmp_path):
    path = tmp_path / "index.sqlite"
    body = "# 云平台\n## 申请实例\n申请实例入口。\n### 创建\n填写名称并提交。\n## 无关内容\n" + "无关。" * 2100
    item = doc("cloud", body=body)
    item["title"] = "云平台"
    create_index(path, [item], "2026-09-09T00:00:00+00:00")
    hits = retrieve("云平台申请实例", path)["results"]
    parent = next(x for x in hits if x["heading"].endswith(" / 申请实例"))
    assert "填写名称并提交" in parent["content"]
    assert "无关。" not in parent["content"]


def test_challenge_two_does_not_use_challenge_one_fragment(tmp_path):
    path = tmp_path / "index.sqlite"
    item = doc("rules", body="# 赛题1: 固定测试集\n测试数据集使用要求。\n# 赛题2: 评分\n支持固定测试集的多轮推理。\n" + "评分。" * 2100)
    item.update(source_kind="event_rules", title="赛事规则")
    create_index(path, [item], "2026-09-09T00:00:00+00:00")
    hits = retrieve("赛题2固定测试数据集要求", path)["results"]
    assert hits and all("赛题1" not in x["heading"] for x in hits)
    assert "支持固定测试集" in hits[0]["content"]
