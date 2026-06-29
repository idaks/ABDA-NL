"""Tests for the LLM backend factory in app/llm/__init__.py.

Pins the ABDA_LLM_BACKEND → client-class mapping so a misconfigured env
can't silently produce the wrong backend at runtime.
"""
from __future__ import annotations

import pytest

from app.llm import ClaudeClient, OllamaClient, make_llm_client, resolve_backend


# --- resolve_backend ---


def test_resolve_backend_default_is_claude(monkeypatch):
    monkeypatch.delenv("ABDA_LLM_BACKEND", raising=False)
    assert resolve_backend() == "claude"


@pytest.mark.parametrize("value,expected", [
    ("claude", "claude"),
    ("CLAUDE", "claude"),  # case-insensitive
    ("ollama", "ollama"),
    ("Ollama", "ollama"),
    ("", "claude"),        # empty string -> default
    ("   ", "claude"),     # whitespace -> default
])
def test_resolve_backend_reads_env_and_normalises(monkeypatch, value, expected):
    monkeypatch.setenv("ABDA_LLM_BACKEND", value)
    assert resolve_backend() == expected


def test_resolve_backend_unknown_falls_back_to_default(monkeypatch, caplog):
    """An unrecognised value logs a warning but does not crash startup."""
    monkeypatch.setenv("ABDA_LLM_BACKEND", "gpt5-fake")
    with caplog.at_level("WARNING", logger="app.llm"):
        result = resolve_backend()
    assert result == "claude"
    assert any("gpt5-fake" in r.message for r in caplog.records)


# --- make_llm_client ---


def test_make_llm_client_returns_ollama_when_selected(monkeypatch):
    """Ollama path: no Anthropic SDK import, no API key required."""
    monkeypatch.setenv("ABDA_LLM_BACKEND", "ollama")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = make_llm_client()
    assert isinstance(client, OllamaClient)


def test_make_llm_client_explicit_override_wins_over_env(monkeypatch):
    monkeypatch.setenv("ABDA_LLM_BACKEND", "claude")
    # Explicit arg should route to ollama even with the env set to claude.
    client = make_llm_client(backend="ollama")
    assert isinstance(client, OllamaClient)


def test_make_llm_client_rejects_unknown_explicit_backend():
    with pytest.raises(ValueError, match="unknown LLM backend"):
        make_llm_client(backend="gpt5-fake")


def test_make_llm_client_forwards_model_to_ollama(monkeypatch):
    """The --model flag on the eval runner needs to cascade all the way
    to the client constructor, so users can bench alternative open models
    without an env-var dance.
    """
    monkeypatch.setenv("ABDA_LLM_BACKEND", "ollama")
    client = make_llm_client(model="llama3.1:8b")
    assert isinstance(client, OllamaClient)
    assert client.model == "llama3.1:8b"


def test_make_llm_client_ollama_defaults_when_model_is_none(monkeypatch):
    """Omitting --model leaves the client's own resolution intact
    (env var ABDA_OLLAMA_MODEL if set, else the qwen3:8b default)."""
    monkeypatch.setenv("ABDA_LLM_BACKEND", "ollama")
    monkeypatch.delenv("ABDA_OLLAMA_MODEL", raising=False)
    client = make_llm_client()  # no model override
    assert client.model == "qwen3:8b"


def test_make_llm_client_routes_to_claude(monkeypatch):
    """Happy path with the default backend. We only verify the class --
    ClaudeClient will try to read ANTHROPIC_API_KEY and construct an
    anthropic.Anthropic instance; that needs the SDK but doesn't actually
    call the network, so it's safe at test time.
    """
    monkeypatch.setenv("ABDA_LLM_BACKEND", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy-not-a-real-key")
    client = make_llm_client()
    assert isinstance(client, ClaudeClient)


# --- Preflight: startup-time backend prereq check ---


def test_preflight_disabled_llm_noop(monkeypatch):
    """Non-LLM mode must skip all preflight checks."""
    from app.api.main import _preflight_llm_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ABDA_LLM_BACKEND", "claude")
    _preflight_llm_config(False)  # should not raise


def test_preflight_claude_without_key_raises(monkeypatch):
    from app.api.main import _preflight_llm_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ABDA_LLM_BACKEND", "claude")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        _preflight_llm_config(True)


def test_preflight_claude_with_key_passes(monkeypatch):
    from app.api.main import _preflight_llm_config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-dummy")
    monkeypatch.setenv("ABDA_LLM_BACKEND", "claude")
    _preflight_llm_config(True)  # should not raise


def test_preflight_ollama_skips_key_requirement(monkeypatch):
    """Ollama backend must not require ANTHROPIC_API_KEY."""
    from app.api.main import _preflight_llm_config

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ABDA_LLM_BACKEND", "ollama")
    _preflight_llm_config(True)  # should not raise
