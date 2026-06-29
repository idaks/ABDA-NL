"""LLM client abstraction and prompt loader for ABDA-NL."""
import logging
import os

from app.llm.client import ClaudeClient, LLMClient, LLMResponse, OllamaClient, ToolCallResponse
from app.llm.prompts import load_prompt

log = logging.getLogger(__name__)

# Recognised backend identifiers. Extend when adding a new client class.
_BACKENDS = {"claude", "ollama"}
_DEFAULT_BACKEND = "claude"


def resolve_backend() -> str:
    """Read `ABDA_LLM_BACKEND` and return a validated backend id.

    Unset / empty / unrecognised values fall back to the default and
    log a warning rather than crashing -- keeps a misconfigured env
    from breaking startup silently.
    """
    raw = (os.getenv("ABDA_LLM_BACKEND") or "").strip().lower()
    if not raw:
        return _DEFAULT_BACKEND
    if raw not in _BACKENDS:
        log.warning(
            "unrecognised ABDA_LLM_BACKEND=%r; falling back to %r. Valid: %s",
            raw, _DEFAULT_BACKEND, sorted(_BACKENDS),
        )
        return _DEFAULT_BACKEND
    return raw


def make_llm_client(
    backend: str | None = None,
    model: str | None = None,
) -> LLMClient:
    """Instantiate the active LLM client.

    `backend` overrides the env var; useful for tests. When None,
    reads `ABDA_LLM_BACKEND` (`claude` or `ollama`; default claude).

    `model` overrides the client's default model. For Ollama this
    means a tag like `llama3.1:8b` or `gemma3n:e4b`; for Claude it
    means a Claude model id like `claude-sonnet-4-6`. When None, the
    client's own env-var / default resolution applies.
    """
    chosen = (backend or resolve_backend()).lower()
    if chosen == "ollama":
        client = OllamaClient(model=model)
        log.info("llm_backend=ollama model=%s base_url=%s", client.model, client.base_url)
        return client
    if chosen == "claude":
        client = ClaudeClient(model=model)
        log.info("llm_backend=claude model=%s", client.model)
        return client
    # resolve_backend already filters to known values, but
    # belt-and-braces for callers that pass `backend=` explicitly.
    raise ValueError(f"unknown LLM backend: {chosen!r}")


__all__ = [
    "ClaudeClient",
    "LLMClient",
    "LLMResponse",
    "OllamaClient",
    "ToolCallResponse",
    "load_prompt",
    "make_llm_client",
    "resolve_backend",
]
