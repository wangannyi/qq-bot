import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import answer_policy as policy


def test_other_platform_untouched():
    policy.begin(session_id="feishu", user_message="private", platform="feishu")
    assert policy.transform(response_text="unchanged", session_id="feishu", platform="feishu") is None


def test_technical_question_without_search_fails_closed():
    policy.begin(session_id="missing", user_message="K1怎么部署LLM", platform="qqbot")
    assert "未取得" in policy.transform(session_id="missing", platform="qqbot", response_text="unchecked")


def test_greeting_does_not_require_document_search(monkeypatch):
    calls = []
    bridge = types.ModuleType("website_bridge")
    bridge.enabled = lambda: True
    bridge.event_question = lambda question: False
    bridge.ask = lambda question: calls.append(question)
    bridge.final_answer = lambda *args: "unexpected"
    bridge.clarification = lambda *args: None
    monkeypatch.setitem(sys.modules, "website_bridge", bridge)
    policy.begin(session_id="greeting", user_message="你好", platform="qqbot")
    assert policy.transform(session_id="greeting", platform="qqbot", response_text="你好") is None
    assert calls == []


def test_identity_question_bypasses_official_bridge(monkeypatch):
    """Bot identity is local operational information, not a docs query."""

    policy._turns.clear()
    bridge = types.ModuleType("website_bridge")
    bridge.enabled = lambda: True
    bridge.event_question = lambda question: False
    bridge.ask = lambda question: (_ for _ in ()).throw(AssertionError("must not query official bot"))
    bridge.final_answer = lambda *args: (_ for _ in ()).throw(AssertionError("must not summarize official bot"))
    bridge.clarification = lambda *args: None
    monkeypatch.setitem(sys.modules, "website_bridge", bridge)

    policy.begin(session_id="identity", user_message="你是谁？", platform="qqbot")
    result = policy.transform(session_id="identity", platform="qqbot", response_text="provider wording")

    assert result == policy.IDENTITY_REPLY
    assert not policy.requires_official_answer("你是谁？")
    assert "http" not in result and "参考文档" not in result and "🔗" not in result


def test_k3_python_source_question_uses_official_bridge(monkeypatch):
    """Chinese immediately after K3 must not make a technical question disappear."""
    calls = []
    bridge = types.ModuleType("website_bridge")
    bridge.enabled = lambda: True
    bridge.event_question = lambda question: False

    def ask(question):
        calls.append(question)
        return {
            "backend": "website",
            "query": question,
            "original_answer": "官方 Python 软件源说明",
            "references": [],
        }

    bridge.ask = ask
    bridge.final_answer = lambda question, evidence, session_id: "已依据官网说明回复"
    bridge.clarification = lambda *args: None
    monkeypatch.setitem(sys.modules, "website_bridge", bridge)

    question = "K3上如何设置python源"
    policy.begin(session_id="python-source", user_message=question, platform="qqbot")
    assert policy.transform(session_id="python-source", platform="qqbot") == "已依据官网说明回复"
    assert calls == [question]


def test_substantive_question_uses_official_bridge_even_without_keyword(monkeypatch):
    """No local model answer may bypass the official-first support path."""
    calls = []
    bridge = types.ModuleType("website_bridge")
    bridge.enabled = lambda: True
    bridge.event_question = lambda question: False

    def ask(question):
        calls.append(question)
        return {"backend": "website", "query": question,
                "original_answer": "官网答复", "references": []}

    bridge.ask = ask
    bridge.final_answer = lambda question, evidence, session_id: "官网摘要"
    bridge.clarification = lambda *args: None
    monkeypatch.setitem(sys.modules, "website_bridge", bridge)

    question = "如何完成开发环境准备？"
    policy.begin(session_id="official-first", user_message=question, platform="qqbot")
    assert policy.transform(session_id="official-first", platform="qqbot") == "官网摘要"
    assert calls == [question]


def test_dataset_details_do_not_negate_explicit_scoring_rule():
    reply = policy.compose("赛题2有什么固定测试数据集？", {
        "evidence_status": "partial", "results": [{"excerpt": "支持固定测试集的多轮推理，结果质量可用"}]})
    assert "评分要求支持固定测试集的多轮推理" in reply
    assert "如何获取" in reply and "是否" not in reply


def test_two_qq_sessions_do_not_share_questions_or_evidence(monkeypatch):
    policy._turns.clear()
    for sid in ("a", "b"):
        policy.begin(session_id=sid, user_message=sid, platform="qqbot")
        policy.observe(tool_name="mcp__spacemit_docs__search_spacemit_docs", session_id=sid,
                       result={"evidence_status":"sufficient", "results":[sid]})
    monkeypatch.setattr(policy, "compose", lambda question, evidence: question + evidence["results"][0])
    assert policy.transform(session_id="a", platform="qqbot") == "aa"
    assert policy.transform(session_id="b", platform="qqbot") == "bb"


def test_nested_mcp_result_and_first_evidence_preserved():
    policy._turns.clear()
    policy.begin(session_id="a", user_message="question", platform="qqbot")
    policy.observe(tool_name="mcp__spacemit_docs__search_spacemit_docs", session_id="a",
        result={"content":[{"type":"text", "text":'{"evidence_status":"partial","results":[1]}'}]})
    policy.observe(tool_name="mcp__spacemit_docs__search_spacemit_docs", session_id="a",
        result={"evidence_status":"sufficient","results":[2]})
    assert policy._turns["a"]["evidence"]["results"] == [1]


def test_unavailable_editor_does_not_publish_unchecked_answer(monkeypatch):
    policy.begin(session_id="a", user_message="question", platform="qqbot")
    policy.observe(tool_name="mcp__spacemit_docs__search_spacemit_docs", session_id="a",
        result={"evidence_status":"sufficient","results":[1]})
    monkeypatch.setattr(policy, "compose", lambda *args: (_ for _ in ()).throw(ValueError("secret")))
    result = policy.transform(session_id="a", platform="qqbot", response_text="unsupported claim")
    assert "需要人工确认" in result and "secret" not in result and "unsupported claim" not in result
