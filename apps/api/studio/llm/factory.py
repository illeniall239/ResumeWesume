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
from typing import TYPE_CHECKING

from studio.config import settings
from studio.llm import catalog
from studio.llm.backend import ChatBackend, ModelSpec, StreamEnd, TextDelta
from studio.llm.litellm_backend import LiteLLMBackend, probe_supports_tools
from studio.llm import subscription
from studio.llm.resilience import CircuitBreaker

if TYPE_CHECKING:  # pragma: no cover - import cycle: persistence imports nothing here
    from studio.persistence.providers import ProviderStore

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


@dataclass(frozen=True)
class Resolution:
    """The config that will be used, and why it is not the selected one.

    ``fallback_reason`` is empty when the selection was honoured. When it is
    not, it is a sentence for the user rather than a log line: the selection
    they made is not what is running, and they are entitled to know which.
    """

    config: ProviderConfig
    fallback_reason: str = ""


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
        return self.build(_default_config())

    async def effective(self, store: "ProviderStore | None") -> "Resolution":
        """What will actually run, and why, if it is not what was asked for.

        Split out from ``resolve`` so the settings UI can show the same answer
        the next turn will use. Without this the two could disagree silently:
        a selection whose key was later deleted falls back here, while the
        picker went on displaying the dead choice as though it were live --
        which is exactly what it did, and is the worst kind of wrong, because
        the screen is confidently reporting the wrong model.
        """
        default = _default_config()

        if store is None:
            return Resolution(default, "")

        selection = await store.selection()
        if selection is None:
            return Resolution(default, "")

        provider = catalog.get(selection.provider)
        if provider is None:
            logger.warning(
                "Selected provider %r is not in the catalogue; using settings",
                selection.provider,
            )
            return Resolution(
                default, f"{selection.provider} is no longer a known provider."
            )

        credential = await store.get(selection.provider)
        api_key = credential.api_key if credential else ""

        # A base the user typed always wins -- that is an explicit instruction
        # to talk to a gateway. The provider's *default* is only forwarded when
        # the provider has no address of its own, because that default exists
        # to list models and litellm builds a completion URL differently from
        # the listing one. Sending Gemini's listing base produced a 404 with an
        # empty body; see ``Provider.routes_itself``.
        override = credential.api_base if credential else None
        api_base = override or (
            None if provider.routes_itself else provider.default_api_base
        )

        # Chosen deliberately but no longer usable: the login expired, or this
        # is a different machine. Saying so beats failing the turn with an SDK
        # stack trace.
        if selection.provider == catalog.CLAUDE_CODE:
            state = subscription.detect()
            if not state.available:
                return Resolution(default, state.detail)
            return Resolution(
                ProviderConfig(provider=catalog.CLAUDE_CODE, model=selection.model),
                "",
            )

        if provider.needs_key and not api_key:
            logger.warning(
                "Selected provider %s has no API key; using settings", provider.id
            )
            return Resolution(
                default, f"{provider.label} has no API key, so it cannot be used."
            )

        return Resolution(
            ProviderConfig(
                provider=selection.provider,
                model=selection.model,
                api_base=api_base,
                api_key=api_key,
            ),
            "",
        )

    async def resolve(self, store: "ProviderStore | None") -> ChatBackend:
        """The backend the user has chosen, or the configured default.

        Every caller that needs a model goes through here -- turns, imports and
        the readiness probe alike -- so "which model is this app using" has one
        answer rather than three that can disagree. A half-configured selection
        (a provider chosen, its key since deleted) falls back to ``.env``
        rather than failing the request: the alternative is an app that cannot
        run at all because of a settings row, when a working local default is
        sitting right there.
        """
        return self.build((await self.effective(store)).config)

    def clear(self) -> None:
        self._cache.clear()


def _default_config() -> ProviderConfig:
    """What to use when the user has not chosen anything.

    A Claude subscription already signed in on this machine is preferred over
    the configured local default, which is the whole point of "pick it up
    automatically": somebody who pays for Claude should not have to find a
    settings page to get the better model, and nothing has to be typed in for
    it to work.

    It is a default, not an override. An explicit selection is honoured even
    when a subscription is sitting right there -- see ``effective``, which only
    reaches this when ``selection`` is None.
    """
    state = subscription.detect()
    if state.is_subscription:
        logger.info("No provider selected; using the Claude login on this machine")
        return ProviderConfig(
            provider=catalog.CLAUDE_CODE, model=catalog.CLAUDE_CODE_DEFAULT
        )

    return ProviderConfig(
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_base=settings.llm_api_base,
        api_key=settings.llm_api_key,
    )


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
