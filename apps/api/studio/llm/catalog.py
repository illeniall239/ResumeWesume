"""Which providers exist, and what each one can run.

Two questions this answers, kept together because they share a table: what may
appear in the provider list, and what models that provider actually has.

**Models are asked for, not hardcoded.** A hand-maintained list of model ids is
wrong the week after it is written, and for Ollama it is wrong immediately --
the only list that matters there is what the user has actually pulled. So each
provider declares how to interrogate it, and the answer comes from the provider.

**The fallback is litellm's registry, not our own list.** When there is no key,
or the live call fails, the models shown come from ``litellm.model_cost``
filtered to chat models that support tool calling -- which is exactly the
capability this app needs, since a model that cannot call a tool cannot edit a
resume. That registry ships with the litellm dependency and moves with it, so
the fallback ages with the package rather than with our attention span.

Nothing here holds a secret. Keys arrive as arguments and are used for one
request; storage is ``persistence/providers.py``'s problem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: How to ask a provider what it has. ``none`` means there is nothing to ask --
#: an arbitrary OpenAI-compatible server may or may not implement /models, and
#: is tried optimistically anyway.
Listing = Literal["ollama", "openai", "anthropic", "gemini"]

#: A dropdown that hangs is worse than one that shows the fallback. Short on
#: purpose: this runs while a menu is open and a person is waiting.
LIST_TIMEOUT_SECONDS = 6.0

#: Beyond this the dropdown stops being a list and becomes a haystack. OpenAI
#: alone reports 76 tool-capable chat models.
MAX_MODELS = 60

#: The tag suffix Ollama gives a model it runs in its own cloud rather than on
#: this machine. See ``_parse``: these are excluded from the local listing,
#: because the whole point of the local provider is that nothing leaves.
CLOUD_SUFFIX = ":cloud"


@dataclass(frozen=True)
class Provider:
    """One place models can come from."""

    id: str
    label: str
    #: Whether a request can be made without an API key. False for local
    #: runtimes, which is what makes them the default.
    needs_key: bool
    listing: Listing
    #: Where to reach it. Editable for the local and the compatible providers,
    #: fixed for the cloud ones, which is why the UI keys off ``base_editable``.
    default_api_base: str | None = None
    base_editable: bool = False
    #: Shown under the provider in the UI. Says what the user has to go and do.
    note: str = ""
    #: Prefixes litellm registry entries carry for this provider, stripped so a
    #: stored model id is what the provider itself would call it.
    registry_key: str = ""


PROVIDERS: dict[str, Provider] = {
    "ollama": Provider(
        id="ollama",
        label="Ollama (local)",
        needs_key=False,
        listing="ollama",
        default_api_base="http://localhost:11434",
        base_editable=True,
        note=(
            "Runs on this machine; nothing leaves it. Prefer a tag with an "
            "explicit context size -- a bare tag defaults to 4096 tokens and "
            "silently truncates the front of the prompt."
        ),
        registry_key="ollama",
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        needs_key=True,
        listing="openai",
        default_api_base="https://api.openai.com/v1",
        note="Key from platform.openai.com.",
        registry_key="openai",
    ),
    "anthropic": Provider(
        id="anthropic",
        label="Anthropic",
        needs_key=True,
        listing="anthropic",
        default_api_base="https://api.anthropic.com",
        note="Key from console.anthropic.com.",
        registry_key="anthropic",
    ),
    "gemini": Provider(
        id="gemini",
        label="Google Gemini",
        needs_key=True,
        listing="gemini",
        default_api_base="https://generativelanguage.googleapis.com",
        note="Key from aistudio.google.com.",
        registry_key="gemini",
    ),
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter",
        needs_key=True,
        listing="openai",
        default_api_base="https://openrouter.ai/api/v1",
        note="One key, many providers. Key from openrouter.ai.",
        registry_key="openrouter",
    ),
    "groq": Provider(
        id="groq",
        label="Groq",
        needs_key=True,
        listing="openai",
        default_api_base="https://api.groq.com/openai/v1",
        note="Key from console.groq.com.",
        registry_key="groq",
    ),
    "deepseek": Provider(
        id="deepseek",
        label="DeepSeek",
        needs_key=True,
        listing="openai",
        default_api_base="https://api.deepseek.com/v1",
        note="Key from platform.deepseek.com.",
        registry_key="deepseek",
    ),
    "openai_compatible": Provider(
        id="openai_compatible",
        label="OpenAI-compatible server",
        needs_key=False,
        listing="openai",
        default_api_base="http://localhost:8080/v1",
        base_editable=True,
        note=(
            "Anything speaking the OpenAI API: llama.cpp, LM Studio, vLLM, "
            "a gateway. Set the URL; the key is optional."
        ),
        registry_key="",
    ),
}


def get(provider_id: str) -> Provider | None:
    return PROVIDERS.get(provider_id)


@dataclass(frozen=True)
class ModelListing:
    """What to put in the dropdown, and where it came from.

    ``source`` is surfaced in the UI rather than kept for debugging. "These are
    the models you have pulled" and "this is a generic list because I could not
    reach the provider" are different claims, and showing one while meaning the
    other is how someone ends up picking a model they do not have.
    """

    models: list[str] = field(default_factory=list)
    source: Literal["live", "fallback"] = "fallback"
    #: Why the live call did not happen or did not work. Empty when it did.
    detail: str = ""


# --------------------------------------------------------------------------
# Fallback: litellm's registry
# --------------------------------------------------------------------------


def _strip_prefix(name: str, registry_key: str) -> str:
    """``gemini/gemini-3.5-flash`` -> ``gemini-3.5-flash``.

    litellm's registry is inconsistent about whether an entry carries its
    provider prefix. ``qualified_model`` adds the prefix on the way out, so
    storing a prefixed id would produce ``gemini/gemini/...`` and a 404 that
    reads like a missing model.
    """
    prefix = f"{registry_key}/"
    return name[len(prefix) :] if registry_key and name.startswith(prefix) else name


def fallback_models(provider_id: str) -> list[str]:
    """Tool-capable chat models litellm knows this provider offers.

    Filtered on ``supports_function_calling`` because the whole product is tool
    calls: a model without them cannot make a single edit, so offering one in
    the picker is offering a broken configuration. Best-effort throughout --
    the registry is a convenience, and a failure here must not stop the picker
    rendering.
    """
    provider = PROVIDERS.get(provider_id)
    if provider is None or not provider.registry_key:
        return []

    try:
        import litellm

        registry: dict[str, Any] = litellm.model_cost
        names: list[str] = litellm.models_by_provider.get(provider.registry_key, [])
    except Exception:  # pragma: no cover - registry access is best-effort
        logger.debug("litellm registry unavailable for %s", provider_id)
        return []

    found: list[str] = []
    seen: set[str] = set()
    for name in names:
        info = registry.get(name) or registry.get(f"{provider.registry_key}/{name}") or {}
        if info.get("mode") != "chat" or not info.get("supports_function_calling"):
            continue
        model = _strip_prefix(name, provider.registry_key)
        if model and model not in seen:
            seen.add(model)
            found.append(model)

    return sorted(found)[:MAX_MODELS]


# --------------------------------------------------------------------------
# Live: ask the provider
# --------------------------------------------------------------------------


def _base(provider: Provider, api_base: str | None) -> str:
    return (api_base or provider.default_api_base or "").rstrip("/")


async def live_models(
    provider: Provider, *, api_key: str = "", api_base: str | None = None
) -> list[str]:
    """Ask the provider what it has. Raises on failure; the caller decides."""
    import httpx

    base = _base(provider, api_base)
    if not base:
        raise ValueError("No API base to query")

    url, headers, params = _request_for(provider, base, api_key)

    async with httpx.AsyncClient(timeout=LIST_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()

    models = _parse(provider, payload)
    # Sorted for a stable menu, deduplicated because a gateway can list the
    # same model under two routes.
    return sorted(dict.fromkeys(model for model in models if model))[:MAX_MODELS]


def _request_for(
    provider: Provider, base: str, api_key: str
) -> tuple[str, dict[str, str], dict[str, str]]:
    if provider.listing == "ollama":
        return f"{base}/api/tags", {}, {}

    if provider.listing == "anthropic":
        return (
            f"{base}/v1/models",
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            {},
        )

    if provider.listing == "gemini":
        # The key goes in the query string, which is Google's own scheme. It
        # therefore reaches request logs on the way out, so this URL is never
        # logged here -- see the except clause in ``list_models``.
        return f"{base}/v1beta/models", {}, {"key": api_key}

    # OpenAI-shaped. The base already ends in /v1 for every provider that needs
    # it, so the path is bare.
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return f"{base}/models", headers, {}


def _parse(provider: Provider, payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []

    if provider.listing == "ollama":
        # Cloud-proxied tags are dropped, and this is a positioning rule rather
        # than a tidiness one. Ollama serves models it runs *for* you under the
        # same /api/tags as the ones you pulled: `minimax-m2:cloud` arrives
        # beside `qwen3:14b` with nothing to tell them apart. Listing it here
        # puts a model that sends the resume to someone else's machine under a
        # heading whose own description reads "Runs on this machine; nothing
        # leaves it" -- so a user could pick it having been told the opposite,
        # and the product's binding privacy claim (see PRODUCT.md) would be
        # false at the moment it mattered most.
        #
        # Excluded rather than relabelled because this provider *is* the local
        # one. A cloud model reachable through a local daemon is still a cloud
        # model, and it belongs to a provider entry that says so, not to a
        # footnote under this one.
        return [
            name
            for entry in payload.get("models", [])
            if isinstance(entry, dict)
            and not (name := str(entry.get("name", ""))).endswith(CLOUD_SUFFIX)
        ]

    if provider.listing == "gemini":
        return [
            # "models/gemini-3.5-flash" -> "gemini-3.5-flash"
            str(entry.get("name", "")).split("/", 1)[-1]
            for entry in payload.get("models", [])
            if isinstance(entry, dict)
            # Only models that can actually hold a conversation; the endpoint
            # also lists embedding and vision-only ones.
            and "generateContent" in (entry.get("supportedGenerationMethods") or [])
        ]

    # OpenAI and Anthropic both answer {"data": [{"id": ...}]}.
    return [
        str(entry.get("id", ""))
        for entry in payload.get("data", [])
        if isinstance(entry, dict)
    ]


async def list_models(
    provider_id: str, *, api_key: str = "", api_base: str | None = None
) -> ModelListing:
    """The models to offer for a provider, live where possible.

    Never raises. A provider that is unreachable, unauthorised or simply does
    not implement a models endpoint still has to produce a usable dropdown, so
    every failure degrades to the registry fallback and says so in ``detail``.
    """
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        return ModelListing(detail=f"Unknown provider {provider_id!r}")

    fallback = fallback_models(provider_id)

    if provider.needs_key and not api_key:
        return ModelListing(
            models=fallback,
            source="fallback",
            detail="Add an API key to see the models on your account.",
        )

    try:
        models = await live_models(provider, api_key=api_key, api_base=api_base)
    except Exception as error:  # noqa: BLE001 - every failure is a fallback
        # ``type(error).__name__`` and nothing else: an httpx error stringifies
        # to include the request URL, and for Gemini that URL carries the API
        # key in its query string.
        detail = _describe(error, provider)
        logger.info("Live model listing failed for %s: %s", provider_id, type(error).__name__)
        return ModelListing(models=fallback, source="fallback", detail=detail)

    if not models:
        return ModelListing(
            models=fallback,
            source="fallback",
            detail="The provider returned no models.",
        )

    return ModelListing(models=models, source="live")


def _describe(error: Exception, provider: Provider) -> str:
    """Why the live call failed, in terms someone can act on.

    Deliberately built from the exception *type* and status code rather than its
    message. httpx puts the full request URL in ``str(error)``, and Gemini's URL
    carries the API key -- so echoing the message into a response body would
    hand the key to the browser and to any log that captured it.
    """
    import httpx

    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return "That API key was rejected."
        if status == 404:
            return "This provider does not offer a model list."
        if status == 429:
            return "Rate limited while listing models."
        return f"The provider answered {status}."

    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        if provider.id == "ollama":
            return "Could not reach Ollama. Is it running?"
        return "Could not reach the provider."

    if isinstance(error, httpx.TimeoutException):
        return "The provider took too long to answer."

    return "Could not read the model list."


__all__ = [
    "PROVIDERS",
    "Provider",
    "ModelListing",
    "get",
    "list_models",
    "fallback_models",
    "live_models",
]
