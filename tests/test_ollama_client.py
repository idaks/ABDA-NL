"""Unit tests for OllamaClient.

Uses ``httpx.MockTransport`` so no live Ollama daemon is needed. Tests
verify the request shape sent to /api/chat and the mapping of Ollama's
response format to our shared ``LLMResponse`` / ``ToolCallResponse``
types.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.llm.client import LLMResponse, OllamaClient, ToolCallResponse, _strip_think_and_extract_json


def _mock(handler) -> OllamaClient:
    """Build an OllamaClient whose HTTP layer is the given handler."""
    return OllamaClient(transport=httpx.MockTransport(handler))


def _ollama_chat_response(content: str, **overrides) -> dict[str, Any]:
    """Canonical shape returned by Ollama /api/chat with stream=False."""
    base = {
        "model": "qwen3:8b",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 42,
        "eval_count": 7,
        "total_duration": 1_500_000_000,
    }
    base.update(overrides)
    return base


# --- complete() ---


def test_complete_returns_text_and_usage_mapped():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/api/chat"
        return httpx.Response(200, json=_ollama_chat_response("The answer is 4."))

    client = _mock(handler)
    resp = client.complete(
        system="You are a calculator.",
        messages=[{"role": "user", "content": "What is 2+2?"}],
        max_tokens=128,
    )
    assert isinstance(resp, LLMResponse)
    assert resp.text == "The answer is 4."
    assert resp.stop_reason == "stop"
    assert resp.usage["input_tokens"] == 42
    assert resp.usage["output_tokens"] == 7
    # Cache fields are zero -- Ollama has no prompt cache.
    assert resp.usage["cache_read_input_tokens"] == 0
    assert resp.usage["cache_creation_input_tokens"] == 0
    assert resp.model == "qwen3:8b"


def test_complete_sends_stream_false_and_flat_system():
    captured: dict[str, Any] = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_ollama_chat_response("ok"))

    client = _mock(handler)
    client.complete(
        system="sys prompt",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
    )
    body = captured["body"]
    assert body["stream"] is False
    # system is injected as the first message.
    assert body["messages"][0] == {"role": "system", "content": "sys prompt"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}
    # Options forwarded: temperature + num_predict.
    assert body["options"]["temperature"] == OllamaClient.DEFAULT_TEMPERATURE
    assert body["options"]["num_predict"] == 64


def test_complete_flattens_cache_control_system_list():
    """Claude's cache-controlled system is a list of {type, text, cache_control}
    blocks. Ollama doesn't understand that shape; we flatten the text."""
    captured: dict[str, Any] = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_ollama_chat_response("ok"))

    client = _mock(handler)
    client.complete(
        system=[
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two", "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=32,
    )
    system_msg = captured["body"]["messages"][0]
    assert system_msg["role"] == "system"
    assert "part one" in system_msg["content"]
    assert "part two" in system_msg["content"]


# --- tool_call() ---


def _tool_schema() -> dict[str, Any]:
    """A minimal tool shaped like the ones in app/llm/edit_schemas.py."""
    return {
        "name": "propose_add_fact",
        "description": "Propose a new fact.",
        "input_schema": {
            "type": "object",
            "required": ["id", "description"],
            "properties": {
                "id": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    }


def test_tool_call_returns_parsed_json_as_tool_input():
    tool = _tool_schema()
    payload = {"id": "new_fact", "description": "the weather was clear"}

    def handler(request):
        return httpx.Response(200, json=_ollama_chat_response(json.dumps(payload)))

    client = _mock(handler)
    resp = client.tool_call(
        system="propose an edit",
        messages=[{"role": "user", "content": "Add a fact..."}],
        tool=tool,
        max_tokens=256,
    )
    assert isinstance(resp, ToolCallResponse)
    assert resp.tool_name == "propose_add_fact"
    assert resp.tool_input == payload
    assert resp.usage["input_tokens"] == 42
    assert resp.model == "qwen3:8b"


def test_tool_call_sends_input_schema_as_format():
    """Ollama's grammar-constrained decoding keys off the `format` field
    being the tool's input_schema -- that's what forces valid JSON."""
    captured: dict[str, Any] = {}
    tool = _tool_schema()

    def handler(request):
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200, json=_ollama_chat_response('{"id": "x", "description": "y"}')
        )

    client = _mock(handler)
    client.tool_call(
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        tool=tool,
        max_tokens=256,
    )
    assert captured["body"]["format"] == tool["input_schema"]
    # System prompt also carries an explicit directive to call the tool.
    system_msg = captured["body"]["messages"][0]["content"]
    assert "propose_add_fact" in system_msg
    assert "JSON" in system_msg or "json" in system_msg.lower()


def test_tool_call_raises_on_malformed_json():
    """If the model emits prose instead of JSON (e.g. an older or
    misconfigured Ollama ignores format), fail loudly."""
    tool = _tool_schema()

    def handler(request):
        return httpx.Response(
            200,
            json=_ollama_chat_response("Here is the answer: not a json object at all"),
        )

    client = _mock(handler)
    with pytest.raises(RuntimeError, match="not valid JSON"):
        client.tool_call(
            system="sys",
            messages=[{"role": "user", "content": "go"}],
            tool=tool,
            max_tokens=256,
        )


def test_tool_call_raises_on_non_object_json():
    """Tool input schemas are always objects; a bare list or number is
    a contract violation even if parseable."""
    tool = _tool_schema()

    def handler(request):
        return httpx.Response(200, json=_ollama_chat_response("[1, 2, 3]"))

    client = _mock(handler)
    with pytest.raises(RuntimeError, match="not an object"):
        client.tool_call(
            system="sys",
            messages=[{"role": "user", "content": "go"}],
            tool=tool,
            max_tokens=256,
        )


# --- _strip_think_and_extract_json ---


def test_strip_think_with_clean_json_is_noop():
    s = '{"id": "x", "description": "y"}'
    assert _strip_think_and_extract_json(s) == s


def test_strip_think_drops_leading_think_block():
    raw = "<think>reasoning about the prompt...</think>\n\n{\"id\": \"x\"}"
    assert _strip_think_and_extract_json(raw) == '{"id": "x"}'


def test_strip_think_drops_dangling_close_tag():
    """The live failure we observed: Qwen emitted a closing </think> with
    no opening tag (thinking got cut off). Make sure we still recover."""
    raw = 'junk_prose\n\n</think>\n\n{"id": "x", "label": "y"}'
    out = _strip_think_and_extract_json(raw)
    assert out == '{"id": "x", "label": "y"}'


def test_strip_think_extracts_json_from_wrapping_prose():
    raw = 'Here is the proposal:\n\n{"id": "x"}\n\nLet me know if...'
    assert _strip_think_and_extract_json(raw) == '{"id": "x"}'


def test_strip_think_handles_nested_braces():
    raw = '{"id": "x", "assumption": {"description": "y", "category": "z"}}'
    assert _strip_think_and_extract_json(raw) == raw


def test_strip_think_returns_original_when_no_json():
    """Model emitted nothing JSON-ish; pass through so the caller's
    json.loads() error message shows the real raw content.
    """
    raw = "I can't help with that."
    # first brace never found -> returns stripped text
    assert _strip_think_and_extract_json(raw) == raw


# --- tool_call sends think=false ---


def test_tool_call_sends_think_false():
    """Qwen 3 thinking mode adds latency and can destabilise the grammar-
    constrained decoder for structured output. OllamaClient disables it
    explicitly for the tool_call path."""
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_ollama_chat_response('{"id": "x", "description": "y"}'))

    client = _mock(handler)
    client.tool_call(
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        tool=_tool_schema(),
        max_tokens=256,
    )
    assert captured["body"].get("think") is False


def test_tool_call_recovers_when_model_emits_think_block():
    """End-to-end test of the strip-and-extract backstop: even if the
    model leaks a <think> block, tool_call still returns a clean
    tool_input dict."""
    def handler(request):
        return httpx.Response(
            200,
            json=_ollama_chat_response(
                "<think>let me reason...</think>\n\n{\"id\": \"ok\", \"description\": \"d\"}"
            ),
        )

    client = _mock(handler)
    resp = client.tool_call(
        system="sys",
        messages=[{"role": "user", "content": "go"}],
        tool=_tool_schema(),
        max_tokens=256,
    )
    assert resp.tool_input == {"id": "ok", "description": "d"}


# --- Config ---


def test_model_resolution_prefers_explicit_arg_over_env(monkeypatch):
    monkeypatch.setenv("ABDA_OLLAMA_MODEL", "from-env")
    client = OllamaClient(model="from-arg")
    assert client.model == "from-arg"


def test_model_resolution_uses_env_when_arg_absent(monkeypatch):
    monkeypatch.setenv("ABDA_OLLAMA_MODEL", "from-env")
    client = OllamaClient()
    assert client.model == "from-env"


def test_model_resolution_defaults_to_qwen(monkeypatch):
    monkeypatch.delenv("ABDA_OLLAMA_MODEL", raising=False)
    client = OllamaClient()
    assert client.model == "qwen3:8b"


def test_base_url_trailing_slash_stripped(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    client = OllamaClient(base_url="http://localhost:11434/")
    assert client.base_url == "http://localhost:11434"


# --- cache flag is a no-op (interface compatibility only) ---


def test_cache_flag_ignored_but_accepted():
    """Interface parity with ClaudeClient; Ollama has no prompt cache."""
    def handler(request):
        return httpx.Response(200, json=_ollama_chat_response("hi"))

    client = _mock(handler)
    # Both True and False should behave identically.
    r1 = client.complete(system="s", messages=[{"role": "user", "content": "q"}],
                        max_tokens=16, cache=True)
    r2 = client.complete(system="s", messages=[{"role": "user", "content": "q"}],
                        max_tokens=16, cache=False)
    assert r1.text == r2.text == "hi"
