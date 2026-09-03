"""Choosing a model, and installing the keys that make one reachable.

The shape of this router is set by one rule: **a key goes in and never comes
out.** ``PUT`` accepts one, every response carries only ``configured`` and a
four-character hint, and no handler here has a code path that puts
``api_key`` into a response body. That is the same reasoning as everywhere else
in this codebase -- the server is the trust boundary -- applied to the one piece
of data where getting it wrong is unrecoverable rather than merely wrong.

``POST /test`` exists because the alternative is worse. Without it the first
sign of a bad key is a failed turn: the model call dies mid-stream, the user
sees a provider error next to a half-written message, and nothing says whether
the problem was the key, the model or the request. A cheap round trip at the
moment the key is entered turns that into a sentence.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from studio.config import settings
from studio.llm import catalog, subscription
from studio.llm.factory import ProviderConfig, health
from studio.persistence.providers import ProviderStore, Selection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["providers"])


def _store(request: Request) -> ProviderStore:
    return request.app.state.providers


class ProviderInfo(BaseModel):
    """One row in the settings list. Carries no secret."""

    id: str
    label: str
    needs_key: bool
    #: Whether a turn could actually run on this provider right now.
    #:
    #: Computed here because the client cannot. "No key needed" means ready for
    #: Ollama and means nothing for the Claude subscription, whose readiness is
    #: a fact about this machine -- whether Claude Code is installed and signed
    #: in. The rule `!needs_key || configured` gave the right answer for every
    #: provider until one arrived that needs no key and can still be unusable.
    ready: bool = False
    base_editable: bool
    note: str = ""
    default_api_base: str | None = None
    configured: bool = False
    #: Last four characters, so a person can recognise their own key.
    hint: str = ""
    api_base: str | None = None


class CatalogResponse(BaseModel):
    providers: list[ProviderInfo]
    #: "provider/model", or null when nothing has been chosen and the
    #: environment's default is in force.
    selection: str | None = None
    #: What ``.env`` configures, shown as the fallback in the UI so the answer
    #: to "what runs if I choose nothing" is visible rather than folklore.
    fallback: str
    #: What the next turn will *actually* use. Usually the selection; the
    #: environment default when the selection cannot run. The picker labels
    #: itself from this, so the screen can never claim a model the server has
    #: already decided against.
    effective: str
    #: Why the selection was not honoured. Empty when it was.
    fallback_reason: str = ""


@router.get("", response_model=CatalogResponse)
async def read_catalog(request: Request) -> CatalogResponse:
    store = _store(request)
    stored = {credential.provider: credential for credential in await store.list()}
    selection = await store.selection()

    providers = []
    for provider in catalog.PROVIDERS.values():
        credential = stored.get(provider.id)
        redacted = credential.redacted() if credential else None

        note = provider.note
        configured = bool(redacted and redacted.configured)

        # The one provider whose readiness is a fact about this machine rather
        # than about a key we stored. Without this it renders as ready whenever
        # it is listed -- there is no key to be missing -- and a turn then fails
        # inside the SDK on a machine where nobody has ever run `claude`.
        if provider.id == catalog.CLAUDE_CODE:
            state = subscription.detect()
            configured = state.available
            note = state.detail

        providers.append(
            ProviderInfo(
                id=provider.id,
                label=provider.label,
                needs_key=provider.needs_key,
                base_editable=provider.base_editable,
                note=note,
                default_api_base=provider.default_api_base,
                configured=configured,
                ready=configured or not provider.needs_key
                if provider.id != catalog.CLAUDE_CODE
                else configured,
                hint=redacted.hint if redacted else "",
                api_base=redacted.api_base if redacted else None,
            )
        )

    resolution = await request.app.state.backends.effective(store)

    return CatalogResponse(
        providers=providers,
        selection=selection.encode() if selection else None,
        fallback=f"{settings.llm_provider}/{settings.llm_model}",
        effective=f"{resolution.config.provider}/{resolution.config.model}",
        fallback_reason=resolution.fallback_reason,
    )


class ModelsResponse(BaseModel):
    provider: str
    models: list[str]
    #: "live" means the provider was asked and answered; "fallback" means this
    #: is litellm's registry. Surfaced so the UI can say which it is showing.
    source: str
    detail: str = ""


@router.get("/{provider_id}/models", response_model=ModelsResponse)
async def read_models(request: Request, provider_id: str) -> ModelsResponse:
    provider = catalog.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="No such provider.")

    credential = await _store(request).get(provider_id)
    listing = await catalog.list_models(
        provider_id,
        api_key=credential.api_key if credential else "",
        api_base=(credential.api_base if credential else None),
    )
    return ModelsResponse(
        provider=provider_id,
        models=listing.models,
        source=listing.source,
        detail=listing.detail,
    )


class CredentialRequest(BaseModel):
    """A key and/or a base URL.

    Both optional, and the distinction between absent and empty is load-bearing.
    ``api_key=null`` means "leave the stored key alone", which is what lets the
    UI save a changed base URL without asking the user to retype a key it is
    not allowed to display. ``api_key=""`` means "clear it".
    """

    api_key: str | None = Field(default=None)
    api_base: str | None = Field(default=None)


@router.put("/{provider_id}/credentials", response_model=ProviderInfo)
async def put_credentials(
    request: Request, provider_id: str, body: CredentialRequest
) -> ProviderInfo:
    provider = catalog.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="No such provider.")

    credential = await _store(request).put(
        provider_id, api_key=body.api_key, api_base=body.api_base
    )
    redacted = credential.redacted()

    # Deliberately logged without the key, and without the base either: an
    # OpenAI-compatible base can itself carry a token in its path.
    logger.info("Credentials updated for %s (configured=%s)", provider_id, redacted.configured)

    return ProviderInfo(
        id=provider.id,
        label=provider.label,
        needs_key=provider.needs_key,
        ready=redacted.configured or not provider.needs_key,
        base_editable=provider.base_editable,
        note=provider.note,
        default_api_base=provider.default_api_base,
        configured=redacted.configured,
        hint=redacted.hint,
        api_base=redacted.api_base,
    )


@router.delete("/{provider_id}/credentials", status_code=204)
async def delete_credentials(request: Request, provider_id: str) -> Response:
    if catalog.get(provider_id) is None:
        raise HTTPException(status_code=404, detail="No such provider.")
    await _store(request).forget(provider_id)
    return Response(status_code=204)


class SelectionRequest(BaseModel):
    provider: str
    model: str


class SelectionResponse(BaseModel):
    provider: str
    model: str


@router.put("/selection", response_model=SelectionResponse)
async def put_selection(
    request: Request, body: SelectionRequest
) -> SelectionResponse:
    provider = catalog.get(body.provider)
    if provider is None:
        raise HTTPException(status_code=404, detail="No such provider.")
    if not body.model.strip():
        raise HTTPException(status_code=422, detail="Choose a model.")

    # Refused here rather than at the first turn. A selection naming a provider
    # with no key is a configuration that cannot run, and the moment to say so
    # is while the user is looking at the settings panel -- not thirty seconds
    # into a request they made an hour later.
    if provider.needs_key:
        credential = await _store(request).get(body.provider)
        if not (credential and credential.api_key):
            raise HTTPException(
                status_code=409,
                detail=f"Add an API key for {provider.label} before selecting it.",
            )

    selection = await _store(request).select(
        Selection(provider=body.provider, model=body.model.strip())
    )
    logger.info("Model selection set to %s", selection.encode())
    return SelectionResponse(provider=selection.provider, model=selection.model)


class TestRequest(BaseModel):
    """A key to try. Omit it to test what is already stored."""

    api_key: str | None = None
    api_base: str | None = None
    model: str | None = None


@router.post("/{provider_id}/test")
async def test_provider(
    request: Request, provider_id: str, body: TestRequest
) -> dict[str, object]:
    """One real completion, so a bad key fails here instead of mid-turn."""
    provider = catalog.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="No such provider.")

    stored = await _store(request).get(provider_id)
    api_key = body.api_key if body.api_key is not None else (stored.api_key if stored else "")
    api_base = body.api_base or (stored.api_base if stored else None) or provider.default_api_base

    model = body.model
    if not model:
        listing = await catalog.list_models(
            provider_id, api_key=api_key or "", api_base=api_base
        )
        model = listing.models[0] if listing.models else settings.llm_model

    backend = request.app.state.backends.build(
        ProviderConfig(
            provider=provider_id,
            model=model,
            api_base=api_base,
            api_key=api_key or "",
        )
    )
    result = await health(backend)
    # ``health`` reports the model it used; echoed back so a test that fell
    # through to a default does not look like it tested the user's choice.
    result["model"] = model
    return result
