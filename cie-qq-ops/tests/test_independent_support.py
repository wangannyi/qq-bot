"""Contracts for independent retrieval, evidence, isolation and FAQ handoff."""

import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import answer_policy
import daily_summary
import faq_sync
import support_runtime as runtime
from retrieval import create_index
from support_archive import archive_answer
from support_reply import InvalidAnswer, render_answer, validate_answer
from support_sources import search_sources, source_kind, supplemental_sources

URL = "https://www.spacemit.com/community/document/info?nodepath=software/python.md&lang=zh"
COMMAND = "pip config set global.extra-index-url https://git.spacemit.com/api/v4/projects/33/packages/pypi/simple"
SOURCE = {
    "title": "Python 使用指南",
    "content": "请配置 SpacemiT 额外软件源。\n"
    + COMMAND
    + "\n运行 pip config list 检查配置。",
    "page_url": URL,
    "path": "software/python.md",
    "heading": "额外源",
    "source_kind": "official",
    "verified": True,
}


def payload():
    return {
        "status": "answered",
        "conclusion": {
            "text": "将 SpacemiT 配置为额外软件源。",
            "evidence": [{"source_id": 0, "quote": "请配置 SpacemiT 额外软件源。"}],
        },
        "points": [
            {
                "text": "添加额外源。",
                "command": COMMAND,
                "evidence": [{"source_id": 0, "quote": COMMAND}],
            }
        ],
        "follow_up": "",
    }


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    runtime._contexts.clear()
    runtime._turns.clear()
    yield
    runtime._contexts.clear()
    runtime._turns.clear()


def test_complete_url_command_and_links_survive_both_renderers():
    answer = validate_answer(payload(), [SOURCE])
    md = render_answer(answer, [SOURCE])
    txt = render_answer(answer, [SOURCE], markdown=False)
    assert COMMAND in md and COMMAND in txt
    assert f"[🔗Python 使用指南]({URL})" in md
    assert URL in txt and "[🔗" not in txt
    assert md.startswith("结论：**") and "```" not in md


@pytest.mark.parametrize("sid", [-1, 2, True, "0"])
def test_model_cannot_invent_source_ids(sid):
    value = payload()
    value["conclusion"]["evidence"][0]["source_id"] = sid
    with pytest.raises(InvalidAnswer):
        validate_answer(value, [SOURCE])


def test_fact_requires_a_real_source_span():
    value = payload()
    value["conclusion"]["evidence"][0]["quote"] = "这是从来没有出现过的原文内容"
    with pytest.raises(InvalidAnswer, match="quote_not_in_source"):
        validate_answer(value, [SOURCE])


@pytest.mark.parametrize(
    "command",
    [
        "pip config set global.extra-index-url",
        "pip install invented-package",
        "echo ok\nrm file",
    ],
)
def test_unproven_or_truncated_commands_are_rejected(command):
    value = payload()
    value["points"][0]["command"] = command
    with pytest.raises(InvalidAnswer):
        validate_answer(value, [SOURCE])


def test_uncertainty_cannot_publish_unsupported_facts():
    value = payload()
    value.update(status="insufficient", conclusion={"text": "K3不支持任何大模型"})
    answer = validate_answer(value, [SOURCE])
    assert "K3不支持" not in render_answer(answer, [SOURCE])


def test_partial_answer_needs_a_specific_limit():
    value = payload()
    value["status"] = "partial"
    with pytest.raises(InvalidAnswer, match="missing_limitation"):
        validate_answer(value, [SOURCE])


def test_no_unbounded_points_or_overlong_messages():
    value = payload()
    value["points"] *= 4
    with pytest.raises(InvalidAnswer, match="too_many_points"):
        validate_answer(value, [SOURCE])


def test_community_label_is_visible():
    answer = validate_answer(payload(), [SOURCE])
    source = dict(SOURCE, source_kind="community")
    assert "社区经验" in render_answer(answer, [source])


def test_same_user_environment_persists_but_other_users_are_isolated():
    runtime.prepare("我用K3，Bianbu 4.0", "group-a", "alice")
    query, env = runtime.prepare("怎么部署LLM", "group-a", "alice")
    assert env == {"board": "K3", "system": "Bianbu", "version": "4.0"}
    assert "K3" in query
    assert not runtime.prepare("怎么部署LLM", "group-a", "bob")[1]
    assert not runtime.prepare("怎么部署LLM", "group-b", "alice")[1]
    assert not runtime.prepare("怎么部署LLM", "group-a", "")[1]


def test_board_change_does_not_reuse_old_system_version():
    runtime.prepare("K3 Bianbu 4.0", "a", "user")
    assert runtime.prepare("换成K1怎么安装", "a", "user")[1] == {"board": "K1"}


def test_pending_followup_keeps_original_intent():
    runtime.prepare("K3如何部署LLM", "a", "user")
    runtime._contexts[("a", "user")].pending = True
    query, env = runtime.prepare("Bianbu 4.0", "a", "user")
    assert "如何部署LLM" in query and env["version"] == "4.0"


def test_context_expires():
    runtime.prepare("K3 Bianbu4", "a", "user")
    runtime._contexts[("a", "user")].updated -= 1801
    assert not runtime.prepare("怎么部署LLM", "a", "user")[1]


def test_middleware_returns_exact_answer_once_and_never_calls_website(monkeypatch):
    monkeypatch.setattr(runtime, "enabled", lambda: True)
    import website_bridge

    monkeypatch.setattr(
        website_bridge, "ask", lambda *a: pytest.fail("official chatbot called")
    )
    calls = []
    monkeypatch.setattr(
        runtime, "_run_turn", lambda *a: calls.append(a) or {"text": "已核对的短答"}
    )
    answer_policy.begin(
        session_id="a", sender_id="alice", user_message="Python怎么用", platform="qqbot"
    )

    def unwanted(request):
        pytest.fail("redundant Hermes model call")

    for _ in range(2):
        response = runtime.execute(
            {"model": "test"}, unwanted, session_id="a", platform="qqbot"
        )
        assert response.choices[0].message.content == "已核对的短答"
    assert len(calls) == 1
    assert answer_policy.transform(session_id="a", platform="qqbot") == "已核对的短答"


def test_other_platform_and_legacy_backend_use_existing_path(monkeypatch):
    monkeypatch.setattr(runtime, "enabled", lambda: False)
    assert runtime.execute({}, lambda r: "legacy", platform="qqbot") == "legacy"
    monkeypatch.setattr(runtime, "enabled", lambda: True)
    assert runtime.execute({}, lambda r: "feishu", platform="feishu") == "feishu"


def test_missing_turn_cannot_leak_a_generic_model_answer(monkeypatch):
    monkeypatch.setattr(runtime, "enabled", lambda: True)
    response = runtime.execute(
        {},
        lambda r: pytest.fail("unguarded model"),
        platform="qqbot",
        session_id="missing",
    )
    assert response.choices[0].message.content == runtime.UNAVAILABLE_REPLY
    assert (
        runtime.finish(session_id="missing", platform="qqbot")
        == runtime.UNAVAILABLE_REPLY
    )


def test_model_timeout_has_feedback_and_no_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime,
        "answer_question",
        lambda *a, **k: (_ for _ in ()).throw(httpx.ReadTimeout("private diagnostic")),
    )
    result = runtime._run_turn(
        {"question": "K3问题", "query": "K3问题", "sender": "u"}, "a"
    )
    assert result["text"] == runtime.TIMEOUT_REPLY
    assert not (tmp_path / "website-answers").exists()


def test_three_concurrent_users_keep_independent_results(monkeypatch):
    barrier = __import__("threading").Barrier(3)

    def answer(query, **kwargs):
        barrier.wait(timeout=3)
        return {"text": query, "answer": {"status": "insufficient"}}

    monkeypatch.setattr(runtime, "answer_question", answer)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(
                runtime._run_turn,
                {"question": str(n), "query": str(n), "sender": str(n)},
                str(n),
            )
            for n in range(3)
        ]
        assert [f.result()["text"] for f in futures] == ["0", "1", "2"]


def test_archive_is_readable_by_daily_faq_and_retains_evidence(tmp_path):
    answer = validate_answer(payload(), [SOURCE])
    result = {
        "query": "配置额外源",
        "answer": answer,
        "sources": [SOURCE],
        "text": render_answer(answer, [SOURCE]),
    }
    archive_answer("配置额外源", result, "test-session", profile=tmp_path)
    path = tmp_path / "website-answers/candidates.sqlite"
    rows = faq_sync.load_candidates(path)
    assert len(rows) == 1 and rows[0].references[0].url == URL
    assert rows[0].source_type == "independent_documents"
    assert "请配置 SpacemiT 额外软件源。" in rows[0].original_answer
    assert "将 SpacemiT 配置为额外软件源。" not in rows[0].original_answer
    assert "将 SpacemiT 配置为额外软件源。" in faq_sync._render_entry(
        "配置额外源", rows[0]
    )
    with sqlite3.connect(path) as db:
        kind, evidence = db.execute(
            "SELECT source_type,evidence_json FROM candidates"
        ).fetchone()
    assert kind == "independent_documents" and json.loads(evidence)["answer"] == answer


def test_mixed_sources_do_not_pass_official_only_auto_publication(tmp_path):
    answer = validate_answer(payload(), [SOURCE])
    source = dict(
        SOURCE,
        source_kind="community",
        page_url="https://github.com/ros2/ros2/issues/123",
    )
    result = {
        "query": "test",
        "answer": answer,
        "sources": [source],
        "text": render_answer(answer, [source]),
    }
    archive_answer("test", result, "a", profile=tmp_path)
    rows = faq_sync.load_candidates(tmp_path / "website-answers/candidates.sqlite")
    assert len(rows) == 1 and not rows[0].references


@pytest.mark.parametrize(
    "text",
    [
        runtime.TIMEOUT_REPLY,
        runtime.UNAVAILABLE_REPLY,
        runtime.BUSY_REPLY,
        "待补充：请提供系统版本",
        "资料不足：尚未找到依据",
        "待确认：该版本是否支持",
    ],
)
def test_unfinished_answers_are_not_counted_as_handled(text):
    # Model/service failures must not manufacture human-support incidents;
    # legitimate environment clarifications remain a separate waiting state.
    expected = (
        daily_summary.STATUS_WAITING
        if text.startswith("待补充：")
        else daily_summary.STATUS_BOT_ISSUE
    )
    assert daily_summary.classify_response([text])[0] == expected


def test_topic_identifiers_beat_generic_platform_words(tmp_path):
    def doc(name, title, body):
        return dict(
            id=name,
            path=name + ".md",
            title=title,
            body=body,
            source_kind="official",
            source_url="",
            page_url="https://www.spacemit.com/" + name,
            updated_at="2026-10-09",
        )

    index = tmp_path / "docs.sqlite"
    create_index(
        index,
        [
            doc("mipi", "MIPI平台", "包含哪些内容 在什么平台适用 " * 20),
            doc("models", "Model Zoo", "Model Zoo包含图像分类、检测与语音模型。"),
        ],
        "2026-10-09T00:00:00+00:00",
    )
    found = search_sources("model zoo包含哪些内容 在什么平台适用", index=index)[
        "sources"
    ]
    assert found and found[0]["title"] == "Model Zoo"
    assert all(s["path"] != "mipi.md" for s in found)


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.python.org/a",
        "https://docs.python.org.evil/a",
        "https://127.0.0.1/a",
        "https://user@docs.python.org/a",
        "https://docs.python.org:123/a",
    ],
)
def test_supplemental_fetch_has_a_narrow_origin_boundary(url):
    assert source_kind(url) is None


def test_public_search_never_receives_private_log(monkeypatch):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: pytest.fail("private data sent"))
    )
    sources, warning = supplemental_sources(
        "api_key=abc 如何配置",
        deadline=__import__("time").monotonic() + 5,
        client=client,
    )
    assert not sources and warning
    client.close()


def test_empty_index_does_not_generate_an_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": [], "query": "q"}
    )
    monkeypatch.setattr(
        runtime,
        "generate",
        lambda *a, **k: pytest.fail("model should not invent evidence"),
    )
    assert runtime.answer_question("q", {})["answer"]["status"] == "insufficient"


def test_model_selects_complete_commands_instead_of_retyping():
    catalog = runtime.evidence_catalog([SOURCE])
    cid = next(c["command_id"] for c in catalog[0]["commands"] if c["text"] == COMMAND)
    value = {
        "status": "answered",
        "conclusion": {
            "text": "添加额外源。",
            "evidence": [{"source_id": 0, "evidence_id": 0}],
        },
        "points": [
            {
                "text": "执行完整配置命令。",
                "command_id": cid,
                "evidence": [{"source_id": 0, "evidence_id": 0}],
            }
        ],
        "follow_up": "",
    }
    checked = validate_answer(runtime.resolve_catalog(value, catalog), [SOURCE])
    assert checked["points"][0]["command"] == COMMAND


def test_unknown_command_catalog_id_is_rejected():
    value = payload()
    value["points"][0] = {"text": "执行命令。", "command_id": "999:999", "evidence": []}
    value["conclusion"] = None
    with pytest.raises(InvalidAnswer, match="unknown_command"):
        runtime.resolve_catalog(value, runtime.evidence_catalog([SOURCE]))


@pytest.mark.parametrize(
    "question", ["怎么部署LLM", "spacemit-asr部署方式", "K3如何部署LLM"]
)
def test_missing_deployment_environment_asks_before_searching(question, monkeypatch):
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: pytest.fail("must clarify first")
    )
    result = runtime.answer_question(question, {})
    assert result["answer"]["status"] == "clarify"
    assert result["text"].count("？") == 1


def test_catalog_parse_error_gets_one_bounded_repair(monkeypatch):
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": [SOURCE], "query": "q"}
    )
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise InvalidAnswer("unknown_command")
        return payload(), {}

    monkeypatch.setattr(runtime, "generate", generate)
    assert runtime.answer_question("设置软件源", {})["answer"]["status"] == "answered"
    assert len(calls) == 2 and calls[1]["feedback"]["error"] == "unknown_command"


def test_answer_is_reviewed_against_sources_before_delivery(monkeypatch):
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": [SOURCE], "query": "q"}
    )
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        value = payload()
        if kwargs.get("review_draft"):
            value["conclusion"]["text"] = "复核后：仅配置额外源。"
        return value, {"input_tokens": 100, "output_tokens": 20}

    monkeypatch.setattr(runtime, "generate", generate)
    result = runtime.answer_question("设置软件源", {})
    assert len(calls) == 2 and "复核后" in result["text"]
    assert result["usage"] == {"input_tokens": 200, "output_tokens": 40}


def test_upstream_reads_primary_page_without_search_snippets():
    requests = []

    def serve(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            text="<html><nav>Ignore irrelevant nav</nav><h1>argparse</h1><p>"
            + "The argparse module creates command-line parsers. " * 10
            + "</p></html>",
        )

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        sources, warning = supplemental_sources(
            "Python argparse如何使用",
            deadline=__import__("time").monotonic() + 5,
            client=client,
        )
    assert (
        len(requests) == 1
        and requests[0] == "https://docs.python.org/3/library/argparse.html"
    )
    assert (
        sources[0]["source_kind"] == "upstream"
        and "creates command-line" in sources[0]["content"]
    )
    assert "Ignore irrelevant nav" not in sources[0]["content"] and not warning


def test_partial_shell_lines_are_not_available_as_commands():
    source = dict(
        SOURCE, content="请按完整多行命令安装。\nsudo apt install \\\n  python3-venv\n"
    )
    assert not runtime.evidence_catalog([source])[0]["commands"]


def test_topic_section_beats_incidental_mentions(tmp_path):
    index = tmp_path / "docs.sqlite"
    body = "# Python指南\n## Jupyter安装\n" + (
        "安装 Jupyter 时也能设置 pypi 源。" * 500
    )
    body += "\n## 设置pypi源\n这里是配置pypi源的完整说明。\n" + COMMAND
    create_index(
        index,
        [
            dict(
                id="py",
                path="python.md",
                title="Python指南",
                body=body,
                page_url=URL,
                source_url="",
                source_kind="official",
            )
        ],
        "2026-10-09T00:00:00+00:00",
    )
    assert (
        "设置pypi源"
        in search_sources("如何设置pypi源", index=index)["sources"][0]["heading"]
    )


def test_concurrent_first_archives_migrate_schema_once(tmp_path):
    answer = validate_answer(payload(), [SOURCE])
    result = {
        "query": "配置源",
        "answer": answer,
        "sources": [SOURCE],
        "text": render_answer(answer, [SOURCE]),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(
            pool.map(
                lambda n: archive_answer("配置源", result, str(n), profile=tmp_path),
                range(3),
            )
        )
    assert (
        len(faq_sync.load_candidates(tmp_path / "website-answers/candidates.sqlite"))
        == 3
    )


def test_local_evidence_gap_can_use_upstream(monkeypatch):
    monkeypatch.setattr(
        runtime, "search_sources", lambda *a, **k: {"sources": [SOURCE], "query": "q"}
    )
    upstream = dict(SOURCE, source_kind="upstream", title="Python官方文档")
    monkeypatch.setattr(
        runtime, "supplemental_sources", lambda *a, **k: ([upstream], "")
    )

    def generate(question, sources, config, **kwargs):
        if sources[0]["source_kind"] == "upstream":
            return payload(), {}
        return {"status": "insufficient", "points": [], "follow_up": ""}, {}

    monkeypatch.setattr(runtime, "generate", generate)
    result = runtime.answer_question("Python argparse", {})
    assert result["answer"]["status"] == "answered" and "上游文档" in result["text"]


def test_service_endpoint_requires_exact_source_evidence():
    value = payload()
    value["conclusion"]["text"] = "服务位于 http://localhost:8050。"
    with pytest.raises(InvalidAnswer, match="unproven_url"):
        validate_answer(value, [SOURCE])
    source = dict(
        SOURCE, content=SOURCE["content"] + "\n服务位于 http://localhost:8050。"
    )
    assert validate_answer(value, [source])["status"] == "answered"


def test_duplicate_command_not_repeated_under_verification_step():
    value = payload()
    value["points"].append(dict(value["points"][0], text="随后检查是否成功。"))
    answer = validate_answer(value, [SOURCE])
    assert render_answer(answer, [SOURCE]).count(COMMAND) == 1


def test_unknown_board_cannot_select_version_specific_example():
    source = dict(
        SOURCE,
        content="K3推荐Python 3.14。\nuv venv myvenv --python 3.14\npython3 -m venv myvenv",
    )
    catalog = runtime.evidence_catalog([source], "Bianbu如何创建Python虚拟环境")
    assert [c["text"] for c in catalog[0]["commands"]] == ["python3 -m venv myvenv"]
    assert (
        len(runtime.evidence_catalog([source], "K3创建Python虚拟环境")[0]["commands"])
        == 2
    )
