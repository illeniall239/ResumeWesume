"""litellm adapter.

The only module that imports litellm. Everything above it speaks the vocabulary
in ``backend.py``.

Two provider realities are handled here rather than leaking upward:

*Capability probing lies about local models.* ``supports_function_calling`` was
measured returning True for a custom Ollama tag that is not in the registry at
all, and False for the bare model name. The answer for an unregistered tag is a
default, not a fact, so for local providers we assume tools work and let the
salvage ladder handle a model that cannot actually use them.

*Local runtimes emit tool calls as prose.* Several Ollama chat templates put
``<tool_call>`` blocks in message content instead of the structured field. That
recovery belongs in the agent's salvage ladder, but the adapter must not
swallow the content that carries it.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from studio.llm.backend import (
    BackendError,
    ChatBackend,
    ModelChunk,
    ModelSpec,
    StreamEnd,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
)

logger = logging.getLogger(__name__)

# litellm's model-name prefix per provider.
_PREFIXES: dict[str, str] = {
    "openai": "",
    "openai_compatible": "openai/",
    "anthropic": "anthropic/",
    "openrouter": "openrouter/",
    "gemini": "gemini/",
    "deepseek": "deepseek/",
    "groq": "groq/",
    # ollama_chat routes to /api/chat, which takes a messages array and
    # supports tools; plain "ollama/" uses the older completion endpoint.
    "ollama": "ollama_chat/",
}

_KNOWN_PREFIXES = tuple(value for value in _PREFIXES.values() if value)


def qualified_model(provider: str, model: str) -> str:
    """Prefix a model name for litellm, without double-prefixing."""
    if model.startswith(_KNOWN_PREFIXES):
        return model
    return f"{_PREFIXES.get(provider, '')}{model}"


def probe_supports_tools(provider: str, model: str) -> bool:
    """Whether to send a tools payload.

    For local providers this returns True unconditionally. The registry has no
    entry for a custom tag, so its answer carries no information, and refusing
    to send tools on a model that supports them is worse than sending them to
    one that does not: the latter degrades into the JSON-plan fallback, the
    former disables the product's core loop outright.
    """
    if provider in {"ollama", "openai_compatible"}:
        return True
    try:
        import litellm

        return bool(litellm.supports_function_calling(qualified_model(provider, model)))
    except Exception:  # pragma: no cover - registry lookups are best-effort
        return True


def _api_key_for(provider: str, api_key: str) -> str:
    """Resolve the key actually sent to the provider.

    Local providers deliberately never inherit a configured cloud key. Sending
    a paid key to an arbitrary localhost server is a credential leak, and the
    sentinel keeps clients that demand a non-empty value happy.
    """
    if provider in {"ollama", "openai_compatible"}:
        return api_key or "local"
    return api_key


class LiteLLMBackend(ChatBackend):
    def __init__(self, spec: ModelSpec, *, api_key: str = "") -> None:
        self.spec = spec
        self._api_key = _api_key_for(spec.provider, api_key)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> AsyncIterator[ModelChunk]:
        import litellm

        # Providers reject parameters they do not know; dropping them beats
        # maintaining a per-provider allowlist that drifts.
        litellm.drop_params = True

        kwargs: dict[str, Any] = {
            "model": qualified_model(self.spec.provider, self.spec.model),
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_key": self._api_key,
        }
        if self.spec.api_base:
            kwargs["api_base"] = self.spec.api_base
        if tools and self.spec.supports_tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

        finish_reason: str | None = None
        usage: dict[str, Any] = {}

        try:
            response = await litellm.acompletion(**kwargs)
            async for raw in response:
                for chunk in _normalise(raw):
                    if isinstance(chunk, StreamEnd):
                        finish_reason = chunk.finish_reason or finish_reason
                        usage = chunk.usage or usage
                        continue
                    yield chunk
        except Exception as error:  # noqa: BLE001 - surfaced as a typed error
            logger.error("Model stream failed: %s", error)
            raise BackendError(str(error)) from error

        # Exactly one terminal chunk, always, so the caller finalises in one
        # place regardless of how the stream ended.
        yield StreamEnd(finish_reason=finish_reason, usage=usage)


def _normalise(raw: Any) -> list[ModelChunk]:
    """Translate one provider chunk into our vocabulary."""
    out: list[ModelChunk] = []
    choices = getattr(raw, "choices", None) or []
    if not choices:
        return out

    choice = choices[0]
    delta = getattr(choice, "delta", None)

    if delta is not None:
        content = getattr(delta, "content", None)
        if content:
            out.append(TextDelta(text=content))

        # Reasoning models expose thinking under several names depending on
        # provider and version.
        for attribute in ("reasoning_content", "thinking"):
            thinking = getattr(delta, attribute, None)
            if thinking:
                out.append(ThinkingDelta(text=thinking))
                break

        for call in getattr(delta, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            out.append(
                ToolCallDelta(
                    index=getattr(call, "index", 0) or 0,
                    id=getattr(call, "id", None),
                    name=getattr(function, "name", None) if function else None,
                    arguments=(getattr(function, "arguments", None) or "")
                    if function
                    else "",
                )
            )

    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason:
        usage_object = getattr(raw, "usage", None)
        usage: dict[str, Any] = {}
        if usage_object is not None:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(usage_object, key, None)
                if value is not None:
                    usage[key] = value
        out.append(StreamEnd(finish_reason=finish_reason, usage=usage))

    return out
