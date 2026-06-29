"""LLM client abstraction.

Two implementations:

  - `ClaudeClient` — wraps the Anthropic SDK with prompt caching and
    internal streaming for timeout protection on long inputs.
  - `OllamaClient` — local-model backend via Ollama's chat API.

Callers code against the `LLMClient` protocol so switching backends
is a config knob, not a refactor.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Minimal response envelope, provider-agnostic."""

    text: str
    stop_reason: str
    usage: dict[str, int]
    latency_ms: int
    model: str


@dataclass
class ToolCallResponse:
    """Structured response for a forced tool-use call.

    `tool_input` is the JSON object the model emitted as the tool's
    input. The caller is responsible for validating it against the
    tool's schema (the Anthropic API also validates server-side but
    its error messages are terse).
    """

    tool_name: str
    tool_input: dict[str, Any]
    stop_reason: str
    usage: dict[str, int]
    latency_ms: int
    model: str


class LLMClient(Protocol):
    """Common interface. Callers pass pre-built system blocks +
    message list."""

    def complete(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        cache: bool = True,
    ) -> LLMResponse:
        ...

    def tool_call(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tool: dict[str, Any],
        max_tokens: int,
        cache: bool = True,
    ) -> ToolCallResponse:
        ...


DEFAULT_MODEL = "claude-sonnet-4-6"


def _resolve_model() -> str:
    """Pick the Claude model id from the env, with a Sonnet default."""
    return os.getenv("ABDA_LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


class ClaudeClient:
    """Anthropic SDK wrapper.

    Streams internally to avoid request timeouts on large inputs,
    then collects the full message. Callers get a non-streaming
    envelope.
    """

    def __init__(self, model: str | None = None) -> None:
        # Lazy-import so non-LLM mode never pays the import cost.
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = model or _resolve_model()

    def complete(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        cache: bool = True,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if cache:
            # Top-level cache_control auto-caches the last cacheable
            # block.  Keeps volatile content (the latest user turn in
            # `messages`) out of the cached prefix automatically.
            kwargs["cache_control"] = {"type": "ephemeral"}

        start = time.monotonic()
        with self._client.messages.stream(**kwargs) as stream:
            final = stream.get_final_message()
        latency_ms = int((time.monotonic() - start) * 1000)

        text_parts = [b.text for b in final.content if getattr(b, "type", None) == "text"]
        text = "".join(text_parts)

        usage = {
            "input_tokens": final.usage.input_tokens,
            "output_tokens": final.usage.output_tokens,
            "cache_read_input_tokens": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
        }

        log.info(
            "llm_call model=%s stop=%s input=%d cache_read=%d cache_write=%d output=%d latency_ms=%d",
            self.model,
            final.stop_reason,
            usage["input_tokens"],
            usage["cache_read_input_tokens"],
            usage["cache_creation_input_tokens"],
            usage["output_tokens"],
            latency_ms,
        )

        return LLMResponse(
            text=text,
            stop_reason=final.stop_reason or "end_turn",
            usage=usage,
            latency_ms=latency_ms,
            model=self.model,
        )

    def tool_call(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tool: dict[str, Any],
        max_tokens: int,
        cache: bool = True,
    ) -> ToolCallResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "tools": [tool],
            # Force the model to emit via this specific tool. No free-form
            # prose; the tool input IS the response.
            "tool_choice": {"type": "tool", "name": tool["name"]},
        }
        if cache:
            kwargs["cache_control"] = {"type": "ephemeral"}

        start = time.monotonic()
        with self._client.messages.stream(**kwargs) as stream:
            final = stream.get_final_message()
        latency_ms = int((time.monotonic() - start) * 1000)

        # Grab the tool_use block. With tool_choice forced to a
        # specific tool, the API guarantees exactly one tool_use block
        # in `content`.
        tool_block = next(
            (b for b in final.content if getattr(b, "type", None) == "tool_use"),
            None,
        )
        if tool_block is None:
            raise RuntimeError(
                f"expected tool_use block in response (tool={tool['name']!r}); "
                f"got blocks: {[getattr(b, 'type', None) for b in final.content]}"
            )

        usage = {
            "input_tokens": final.usage.input_tokens,
            "output_tokens": final.usage.output_tokens,
            "cache_read_input_tokens": getattr(final.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
        }

        log.info(
            "llm_tool model=%s tool=%s stop=%s input=%d cache_read=%d cache_write=%d output=%d latency_ms=%d",
            self.model,
            tool_block.name,
            final.stop_reason,
            usage["input_tokens"],
            usage["cache_read_input_tokens"],
            usage["cache_creation_input_tokens"],
            usage["output_tokens"],
            latency_ms,
        )

        return ToolCallResponse(
            tool_name=tool_block.name,
            tool_input=dict(tool_block.input or {}),
            stop_reason=final.stop_reason or "tool_use",
            usage=usage,
            latency_ms=latency_ms,
            model=self.model,
        )


def _strip_think_and_extract_json(text: str) -> str:
    """Prepare a model's `message.content` for `json.loads`.

    Handles two Qwen / Ollama quirks that can still occur even with
    `think: false` set (models sometimes ignore the directive, and
    older Ollama versions may embed a leading `</think>` regardless):

    1. Strip everything up to and including the last `</think>` tag.
    2. If residual prose wraps the JSON, extract the substring from
       the first `{` to the last matching `}`.

    Returns the cleaned text. If no JSON-looking substring is found,
    the original text is returned unchanged. The caller's json.loads()
    will surface the error with both the raw and cleaned samples in
    its message.
    """
    if not text:
        return text
    # (1) drop anything up through the final </think>
    close_tag = "</think>"
    if close_tag in text:
        text = text.rsplit(close_tag, 1)[1]
    # (2) extract outermost {...} if there's surrounding prose
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        text = text[first:last + 1]
    return text.strip()


class OllamaClient:
    """Local-model backend via the Ollama HTTP API.

    Talks to Ollama's `POST /api/chat` endpoint. For `tool_call`, uses
    the `format` parameter with the tool's `input_schema` as a
    server-side JSON-schema constraint (grammar-constrained decoding),
    matching Claude's forced tool use more faithfully than free-form
    JSON mode, because Ollama enforces the schema during token
    sampling rather than hoping the model gets it right.

    Config:

      - model: from arg, env `ABDA_OLLAMA_MODEL`, else `qwen3:8b`
      - base URL: from arg, env `OLLAMA_BASE_URL`, else
        `http://localhost:11434`
      - read timeout (seconds): env `ABDA_OLLAMA_TIMEOUT`, else
        600. The default is sized for laptop-CPU inference on 3.8B-8B
        models; drop it if you're on a GPU host and want faster
        failure detection.

    Caching: the `cache` flag on every call is accepted for interface
    compatibility with `ClaudeClient` but is a no-op. Ollama has no
    prompt-cache analog. Expect per-call latency proportional to the
    full prompt length, unlike the cached-prefix amortization you get
    with Claude.

    Testing: pass `transport=httpx.MockTransport(handler)` to exercise
    the class without a running Ollama daemon. See
    tests/test_ollama_client.py.
    """

    DEFAULT_MODEL = "qwen3:8b"
    DEFAULT_BASE_URL = "http://localhost:11434"
    # Low temperature across both paths: structured-output (tool_call)
    # needs determinism; our chat/explain flows are focused scenario
    # Q&A where reproducibility matters more than creativity. Override
    # per-task if something benefits from more variety.
    DEFAULT_TEMPERATURE = 0.0
    DEFAULT_TIMEOUT_S = 600.0

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        transport: Any = None,
    ) -> None:
        import httpx

        self._httpx = httpx
        self.model = (
            model
            or (os.getenv("ABDA_OLLAMA_MODEL") or "").strip()
            or self.DEFAULT_MODEL
        )
        self.base_url = (
            base_url
            or (os.getenv("OLLAMA_BASE_URL") or "").strip()
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        timeout_s = self.DEFAULT_TIMEOUT_S
        raw = (os.getenv("ABDA_OLLAMA_TIMEOUT") or "").strip()
        if raw:
            try:
                parsed = float(raw)
                if parsed > 0:
                    timeout_s = parsed
            except ValueError:
                pass
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            transport=transport,
        )

    # --- Helpers ---

    @staticmethod
    def _flatten_system(system: str | list[dict[str, Any]]) -> str:
        """Collapse the Claude-style cache-controlled system blocks to
        a single string. Ollama accepts only a plain system message.
        Cache markers are ignored.
        """
        if isinstance(system, str):
            return system
        parts: list[str] = []
        for block in system or []:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n\n".join(parts)

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post("/api/chat", json=payload)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _usage_from(result: dict[str, Any]) -> dict[str, int]:
        """Map Ollama's token-count fields to our shared usage
        envelope.  cache_read / cache_creation stay zero. Ollama has
        no cache.
        """
        return {
            "input_tokens": int(result.get("prompt_eval_count") or 0),
            "output_tokens": int(result.get("eval_count") or 0),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

    # --- API ---

    def complete(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        cache: bool = True,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._flatten_system(system)},
                *messages,
            ],
            "stream": False,
            "options": {
                "temperature": self.DEFAULT_TEMPERATURE,
                "num_predict": max_tokens,
            },
        }
        start = time.monotonic()
        result = self._post_chat(payload)
        latency_ms = int((time.monotonic() - start) * 1000)

        text = ((result.get("message") or {}).get("content") or "")
        usage = self._usage_from(result)
        stop_reason = result.get("done_reason") or "stop"

        log.info(
            "llm_call backend=ollama model=%s stop=%s input=%d output=%d latency_ms=%d",
            self.model, stop_reason,
            usage["input_tokens"], usage["output_tokens"], latency_ms,
        )

        return LLMResponse(
            text=text,
            stop_reason=stop_reason,
            usage=usage,
            latency_ms=latency_ms,
            model=self.model,
        )

    def tool_call(
        self,
        *,
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tool: dict[str, Any],
        max_tokens: int,
        cache: bool = True,
    ) -> ToolCallResponse:
        tool_name = tool.get("name") or "tool"
        tool_schema = tool.get("input_schema") or {}

        # Instruct the model explicitly. The schema constraint does
        # the heavy lifting (grammar-constrained decoding forces valid
        # JSON matching the schema), but this directive aims the model
        # at the right content in the first place.
        tool_directive = (
            f"\n\nYou MUST respond by calling the `{tool_name}` tool. "
            f"Emit a single JSON object matching the tool's input schema. "
            f"No surrounding prose, no markdown fences."
        )
        system_text = self._flatten_system(system) + tool_directive

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_text},
                *messages,
            ],
            "stream": False,
            "format": tool_schema,
            # think=false disables the model's thinking mode for
            # structured output. With thinking on, Qwen 3 emits
            # <think>...</think> tokens before the JSON, which (a)
            # uses tokens unnecessarily, (b) can truncate the
            # schema-constrained portion, and (c) sometimes pushes the
            # model off-distribution so the grammar constraint fires
            # on bad tokens. Structured outputs don't benefit from
            # chain-of-thought here.
            "think": False,
            "options": {
                "temperature": self.DEFAULT_TEMPERATURE,
                "num_predict": max_tokens,
            },
        }
        start = time.monotonic()
        result = self._post_chat(payload)
        latency_ms = int((time.monotonic() - start) * 1000)

        raw_content = ((result.get("message") or {}).get("content") or "")
        cleaned = _strip_think_and_extract_json(raw_content)
        try:
            tool_input = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OllamaClient.tool_call: model output was not valid JSON "
                f"(first 200 chars of cleaned: {cleaned[:200]!r}; "
                f"raw: {raw_content[:200]!r}): {exc}"
            ) from exc
        if not isinstance(tool_input, dict):
            raise RuntimeError(
                f"OllamaClient.tool_call: model emitted JSON but not an object "
                f"(got {type(tool_input).__name__}); tool schemas are always objects"
            )

        usage = self._usage_from(result)
        stop_reason = result.get("done_reason") or "stop"

        log.info(
            "llm_tool backend=ollama model=%s tool=%s stop=%s input=%d output=%d latency_ms=%d",
            self.model, tool_name, stop_reason,
            usage["input_tokens"], usage["output_tokens"], latency_ms,
        )

        return ToolCallResponse(
            tool_name=tool_name,
            tool_input=tool_input,
            stop_reason=stop_reason,
            usage=usage,
            latency_ms=latency_ms,
            model=self.model,
        )
