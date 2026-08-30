"""Backend construction and health.

The composition root for the model layer. Nothing above this module chooses a
provider or knows one exists; they receive a ``ChatBackend``.

Backends are cached per configuration fingerprint. Building one is cheap, but a
stable instance means the circuit breaker accumulates state across requests,
which is the only way it can observe that a provider is down.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from studio.config import settings
from studio.llm.backend import ChatBackend, ModelSpec, StreamEnd, TextDelta
from studio.llm.litellm_backend import LiteLLMBackend, probe_supports_tools
from studio.llm.resilience import CircuitBreaker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    api_base: str | None = None
    api_key: str = ""

    def fingerprint(self) -> str:
        # The key is hashed, never logged: a fingerprint ends up in log lines
        # and metrics labels.
        return f"{self.provider}|{self.model}|{self.api_base}|{hash(self.api_key)}"


@dataclass
class BackendFactory:
    _cache: dict[str, ChatBackend] = field(default_factory=dict, init=False)
    _breakers: dict[str, CircuitBreaker] = field(default_factory=dict, init=False)

    def breaker_for(self, provider: str) -> CircuitBreaker:
        """One breaker per provider.

        Per provider, not per model: a dead Ollama daemon should not stop a
        configured cloud provider from being tried, and two models on the same
        dead daemon share a single fate.
        """
        if provider not in self._breakers:
            self._breakers[provider] = CircuitBreaker()
        return self._breakers[provider]

    def build(self, config: ProviderConfig) -> ChatBackend:
        key = config.fingerprint()
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        spec = ModelSpec(
            provider=config.provider,
            model=config.model,
            api_base=config.api_base or None,
            supports_tools=probe_supports_tools(config.provider, config.model),
        )
        backend = LiteLLMBackend(spec, api_key=config.api_key)
        self._cache[key] = backend
        logger.info(
            "Built backend %s/%s (tools=%s)",
            spec.provider,
            spec.model,
            spec.supports_tools,
        )
        return backend

    def from_settings(self) -> ChatBackend:
        return self.build(
            ProviderConfig(
                provider=settings.llm_provider,
                model=settings.llm_model,
                api_base=settings.llm_api_base,
                api_key=settings.llm_api_key,
            )
        )

    def clear(self) -> None:
        self._cache.clear()


async def health(backend: ChatBackend) -> dict[str, object]:
    """A minimal live call.

    Note the caveat this returns rather than hides: a reasoning model given a
    small token budget can spend all of it thinking and return no content, so
    an empty completion is reported as ``degraded`` rather than unhealthy. The
    honest signal is that generation works, not that it produced prose.
    """
    text = ""
    try:
        async for chunk in backend.stream(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            max_tokens=32,
        ):
            if isinstance(chunk, TextDelta):
                text += chunk.text
            elif isinstance(chunk, StreamEnd):
                break
    except Exception as error:  # noqa: BLE001
        return {
            "healthy": False,
            "provider": backend.spec.provider,
            "model": backend.spec.model,
            "error": str(error)[:200],
        }

    return {
        "healthy": True,
        "degraded": not text.strip(),
        "provider": backend.spec.provider,
        "model": backend.spec.model,
        "supports_tools": backend.spec.supports_tools,
        "output": text.strip()[:100],
        "note": (
            "The model produced no visible text. Reasoning models often spend a "
            "small token budget on thinking; real generation is usually fine."
            if not text.strip()
            else ""
        ),
    }
