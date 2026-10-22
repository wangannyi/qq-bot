"""Summary failures must not forward unreviewed website content."""
from types import SimpleNamespace

import pytest
import website_bridge as bridge
from daily_summary import STATUS_BOT_ISSUE, classify_response

RAW = """K3仅在Bianbu支持该方法，OpenHarmony不支持。
### 一、llama.cpp部署
#### 1. 安装依赖
安装工具包。
```bash
sudo apt update
```
#### 2. 下载模型
下载模型后启动服务。
[llama.cpp.md](https://www.spacemit.com/community/llama.cpp.md)
"""


@pytest.mark.parametrize("failure", [TimeoutError("secret-provider-body"), RuntimeError("sk-private")])
def test_service_failure_keeps_source_and_logs_only_exception_type(monkeypatch, caplog, failure):
    def fail(*args):
        raise failure
    monkeypatch.setattr(bridge, "summarize", fail)
    saved = []
    monkeypatch.setattr(bridge, "archive", lambda *args: saved.append(args))
    evidence = {"original_answer": RAW, "references": bridge.references(RAW)}
    result = bridge.final_answer("k3 bianbu4.0上如何部署llm", evidence)
    assert result == bridge.SUMMARY_UNAVAILABLE
    assert "OpenHarmony不支持" not in result and "[🔗" not in result
    assert "sudo apt" not in result and "```" not in result
    assert saved[0][1]["original_answer"] == RAW and saved[0][2] == result
    assert type(failure).__name__ in caplog.text
    assert "secret-provider-body" not in caplog.text and "sk-private" not in caplog.text
    assert classify_response([result])[0] == STATUS_BOT_ISSUE


def test_rejected_candidate_is_never_used(monkeypatch):
    monkeypatch.setattr(bridge, "summarize", lambda *args: bridge.SUMMARY_UNAVAILABLE)
    monkeypatch.setattr(bridge, "archive", lambda *args: None)
    result = bridge.final_answer("K3部署LLM", {"original_answer": RAW})
    assert result == bridge.SUMMARY_UNAVAILABLE
    assert "官网原答摘录" not in result and "参考文档" not in result


def test_validation_rejection_is_not_misreported_as_service_unavailable(monkeypatch):
    monkeypatch.setattr(bridge, "summarize", lambda *args: bridge.SUMMARY_REJECTED)
    monkeypatch.setattr(bridge, "archive", lambda *args: None)
    result = bridge.final_answer("K3部署LLM", {"original_answer": RAW})
    assert result == bridge.SUMMARY_REJECTED
    assert "官网原答摘录" not in result and "[🔗" not in result
    assert "OpenHarmony不支持" not in result and "参考文档" not in result


def test_legacy_source_fallback_is_fail_closed_not_a_partial_technical_claim():
    raw = RAW + "必须满足一个完整前提条件，否则不能使用。" * 100
    result = bridge.source_fallback({"original_answer": raw})
    assert result == bridge.SUMMARY_UNAVAILABLE
    assert "完整前提" not in result and "官网原答摘录" not in result
    assert "参考文档" not in result and "llama.cpp部署" not in result


def test_legacy_source_fallback_does_not_present_shell_comment_as_a_section_heading():
    raw = RAW + "\n```bash\n# K3推荐拉取qwen3:0.6b，可根据需求选择其他模型\n```\n" + "补充资料。" * 200
    result = bridge.source_fallback({"original_answer": raw})
    assert result == bridge.SUMMARY_UNAVAILABLE
    assert "K3推荐拉取" not in result


@pytest.mark.parametrize("raw", ["", "资料不足", "[假链接](https://evil.example/doc)",
                                  "[坏链接](https://www.spacemit.com:bad/doc)"])
def test_missing_or_unsafe_sources_do_not_invent_fallback(raw):
    assert bridge.source_fallback({"original_answer": raw,
        "references": [{"url": "https://www.spacemit.com/invented"}]}) == bridge.SUMMARY_UNAVAILABLE


def test_legacy_source_fallback_does_not_forward_incidental_links():
    result = bridge.source_fallback({"original_answer": RAW + "[SDK](https://www.spacemit.com/sdk)",
                                    "references": [{"url": "https://www.spacemit.com/invented"}]})
    assert result == bridge.SUMMARY_UNAVAILABLE
    assert "invented" not in result
    assert "community/llama.cpp.md" not in result and "spacemit.com/sdk" not in result


def test_generic_website_label_uses_its_actual_document_name():
    assert bridge.document_title({"title": "官方文档链接", "url":
        "https://developer.spacemit.com/community/document/info?nodepath=software/ai/llama.cpp.md"}) == "llama.cpp 部署指南"


def test_empty_final_channel_does_not_retry_or_publish_reasoning(monkeypatch, caplog):
    import openai
    from test_summary_validation import fake_model
    calls = fake_model(monkeypatch, [])
    client = openai.OpenAI
    def empty(self, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(
            content="", reasoning_content="private-reasoning"))])
    monkeypatch.setattr(client, "create", empty)
    answer = bridge.summarize("k3 bianbu4.0上如何部署llm", {"original_answer": RAW, "references": bridge.references(RAW)})
    assert answer == bridge.SUMMARY_UNAVAILABLE and len(calls) == 1
    assert "summary_empty_response" in caplog.text and "reasoning_present=True" in caplog.text
    assert "private-reasoning" not in answer + caplog.text


def test_native_transport_uses_same_provider_and_only_final_text():
    import httpx
    def respond(request):
        import json
        body = json.loads(request.content)
        assert str(request.url) == "https://provider.invalid/v1/messages"
        assert request.headers["x-api-key"] == "test-only-key"
        assert body["model"] == "same-model" and body["thinking"] == {"type": "disabled"}
        assert body["messages"] == [{"role": "user", "content": "public question"}]
        assert body["system"].startswith("editor")
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [
            {"type": "thinking", "thinking": "never-send"}, {"type": "text", "text": '{"body":"public"}'}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = bridge.native_summary_response(client, {"base_url": "https://provider.invalid/v1/"},
            "test-only-key", "same-model", [{"role": "system", "content": "editor"},
            {"role": "user", "content": "public question"}], 1600)
    assert result.choices[0].finish_reason == "stop"
    assert result.choices[0].message.content == '{"body":"public"}'
    assert result.choices[0].message.reasoning_content is None


@pytest.mark.parametrize("payload", [{"content": "not-a-list"}, {"content": ["not-a-block"]}])
def test_native_transport_rejects_malformed_envelope(payload):
    import httpx
    def respond(request):
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match="invalid_native_summary_response"):
            bridge.native_summary_response(client, {"base_url": "https://provider.invalid/v1"}, "key", "model",
                                           [{"role": "user", "content": "question"}], 100)


def test_native_transport_still_runs_validation_and_retry(monkeypatch):
    import json

    import rag_server
    monkeypatch.setattr(rag_server, "model_credentials", lambda: ({
        "model": {"provider": "custom:test", "default": "same-model"},
        "custom_providers": [{"name": "test", "base_url": "https://provider.invalid/v1"}],
        "cie_support": {"summary_transport": "anthropic_messages"},
    }, "test-only-key"))
    calls = []
    def reply(client, provider, key, model, messages, budget):
        calls.append(json.loads(json.dumps(messages)))
        content = "invalid JSON" if len(calls) == 1 else json.dumps({"body": "", "status": "answered",
            "source_id": 0, "methods": [{"name": "llama.cpp", "condition": "Bianbu支持，OpenHarmony不支持。",
                                        "steps": ["安装工具包。", "下载模型后启动服务。"]}]})
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))])
    monkeypatch.setattr(bridge, "native_summary_response", reply)
    answer = bridge.summarize("k3 bianbu4.0上如何部署llm", {"original_answer": RAW, "references": bridge.references(RAW)})
    assert "1、安装工具包。" in answer and "OpenHarmony不支持" in answer
    assert "[🔗" in answer and len(calls) == 2
    assert "invalid_json" in calls[1][-1]["content"]


def test_four_steps_use_their_own_bound_not_single_paragraph_limit():
    steps = ["按官方文档配置软件源及优先级。" * 5] * 4
    value = {"body": "", "steps": steps, "status": "answered", "source_id": 0}
    evidence = {"original_answer": RAW, "references": bridge.references(RAW)}
    body, _ = bridge.validate_summary(value, "K1如何安装ROS2", evidence, True, False)
    assert 240 < len(body) < 460
    value["steps"][0] = "字" * 111
    with pytest.raises(ValueError, match="invalid_steps"):
        bridge.validate_summary(value, "K1如何安装ROS2", evidence, True, False)
