"""Model selection, credential storage, and the catalogue.

The block that matters most is ``TestKeysNeverLeak``. Everything else here is
ordinary CRUD; that one asserts the single property this feature exists to
uphold, and it is the one whose failure is unrecoverable -- a key that reaches a
response body has been disclosed, and no later fix un-discloses it.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from studio.llm import catalog
from studio.llm.factory import BackendFactory
from studio.persistence.providers import ProviderStore, Selection
from studio.persistence.repo import DocumentRepo

SECRET = "sk-test-abcdefghijklmnop-9XYZ"


@pytest.fixture
async def store(tmp_path):
    repo = DocumentRepo(f"sqlite+aiosqlite:///{(tmp_path / 'p.db').as_posix()}")
    await repo.create_schema()
    yield ProviderStore(repo.session_factory)
    await repo.dispose()


@pytest.fixture
async def client(tmp_path):
    """The real app, minus the lifespan, with an isolated database."""
    from studio.routers import providers as providers_router

    repo = DocumentRepo(f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}")
    await repo.create_schema()

    app = FastAPI()
    app.include_router(providers_router.router, prefix="/api/v1")
    app.state.repo = repo
    app.state.providers = ProviderStore(repo.session_factory)
    app.state.backends = BackendFactory()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http

    await repo.dispose()


class TestCredentialStore:
    async def test_put_then_get_round_trips(self, store) -> None:
        await store.put("openai", api_key=SECRET)
        credential = await store.get("openai")
        assert credential is not None
        assert credential.api_key == SECRET

    async def test_absent_key_leaves_the_stored_one_alone(self, store) -> None:
        """Editing a base URL must not require retyping a key the UI cannot show."""
        await store.put("openai_compatible", api_key=SECRET, api_base="http://a")
        await store.put("openai_compatible", api_base="http://b")

        credential = await store.get("openai_compatible")
        assert credential.api_key == SECRET
        assert credential.api_base == "http://b"

    async def test_empty_string_clears_the_key(self, store) -> None:
        """Absent and empty are different instructions, and both are used."""
        await store.put("openai", api_key=SECRET)
        await store.put("openai", api_key="")
        credential = await store.get("openai")
        assert credential.api_key == ""

    async def test_forget_removes_the_row(self, store) -> None:
        await store.put("groq", api_key=SECRET)
        assert await store.forget("groq") is True
        assert await store.get("groq") is None

    async def test_redacted_shows_only_the_last_four(self, store) -> None:
        await store.put("openai", api_key=SECRET)
        credential = await store.get("openai")
        redacted = credential.redacted()

        assert redacted.configured is True
        assert redacted.hint == "••••9XYZ"
        assert SECRET not in redacted.hint
        assert not hasattr(redacted, "api_key")


class TestSelection:
    async def test_round_trips(self, store) -> None:
        await store.select(Selection(provider="ollama", model="qwen3:14b-16k"))
        assert await store.selection() == Selection("ollama", "qwen3:14b-16k")

    def test_a_model_id_may_contain_a_slash(self) -> None:
        """Groq and OpenRouter ids are routinely ``vendor/model``.

        Splitting greedily would store ``openai`` as the model and silently
        drop the rest, producing a 404 that reads like a missing model.
        """
        selection = Selection.decode("groq/openai/gpt-oss-120b")
        assert selection == Selection("groq", "openai/gpt-oss-120b")

    @pytest.mark.parametrize("raw", ["", "ollama", "/model", "ollama/", "   "])
    def test_malformed_selections_are_rejected(self, raw: str) -> None:
        assert Selection.decode(raw) is None


class TestCatalogFallback:
    def test_fallback_comes_from_the_registry(self) -> None:
        models = catalog.fallback_models("anthropic")
        assert models, "litellm should know some Anthropic models"
        assert all("/" not in model for model in models), (
            "a registry prefix left on an id would be re-prefixed by "
            "qualified_model and 404"
        )

    def test_unknown_provider_has_no_fallback(self) -> None:
        assert catalog.fallback_models("not-a-provider") == []


class TestCatalogLive:
    @respx.mock
    async def test_ollama_lists_pulled_models(self) -> None:
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={"models": [{"name": "qwen3:14b-16k"}, {"name": "llama3:8b"}]},
            )
        )
        listing = await catalog.list_models("ollama")
        assert listing.source == "live"
        assert listing.models == ["llama3:8b", "qwen3:14b-16k"]

    @respx.mock
    async def test_a_cloud_tag_never_appears_in_the_local_list(self) -> None:
        """Ollama serves cloud models from the same endpoint as pulled ones.

        The local provider's own description promises that nothing leaves the
        machine, so a `:cloud` tag listed under it would let a user choose,
        having been told the opposite, to send their resume to someone else's
        computer. PRODUCT.md makes that privacy claim binding.
        """
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:14b-16k"},
                        {"name": "minimax-m2:cloud"},
                        {"name": "qwen3:4b"},
                    ]
                },
            )
        )
        listing = await catalog.list_models("ollama")

        assert listing.models == ["qwen3:14b-16k", "qwen3:4b"]
        assert not any(m.endswith(":cloud") for m in listing.models)

    @respx.mock
    async def test_openai_shape_is_parsed(self) -> None:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}
            )
        )
        listing = await catalog.list_models("openai", api_key=SECRET)
        assert listing.source == "live"
        assert listing.models == ["gpt-4o", "gpt-4o-mini"]

    @respx.mock
    async def test_gemini_drops_models_that_cannot_chat(self) -> None:
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-3.5-flash",
                            "supportedGenerationMethods": ["generateContent"],
                        },
                        {
                            "name": "models/text-embedding-004",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            )
        )
        listing = await catalog.list_models("gemini", api_key=SECRET)
        assert listing.models == ["gemini-3.5-flash"]

    async def test_no_key_means_fallback_with_a_reason(self) -> None:
        listing = await catalog.list_models("anthropic")
        assert listing.source == "fallback"
        assert "API key" in listing.detail

    @respx.mock
    async def test_a_rejected_key_says_so_and_falls_back(self) -> None:
        respx.get("https://api.openai.com/v1/models").mock(
            return_value=httpx.Response(401, json={"error": "bad key"})
        )
        listing = await catalog.list_models("openai", api_key="sk-wrong")
        assert listing.source == "fallback"
        assert listing.detail == "That API key was rejected."
        assert listing.models, "a rejected key still needs a usable dropdown"

    @respx.mock
    async def test_an_unreachable_ollama_is_named(self) -> None:
        respx.get("http://localhost:11434/api/tags").mock(
            side_effect=httpx.ConnectError("nope")
        )
        listing = await catalog.list_models("ollama")
        assert listing.detail == "Could not reach Ollama. Is it running?"

    @respx.mock
    async def test_a_failure_detail_never_carries_the_key(self) -> None:
        """Gemini puts the key in the query string, so httpx puts it in str(error).

        Echoing an exception message into a response body would hand the key to
        the browser and to any log that captured the response.
        """
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            side_effect=httpx.ConnectError(
                f"failed to connect to ...?key={SECRET}"
            )
        )
        listing = await catalog.list_models("gemini", api_key=SECRET)
        assert SECRET not in listing.detail
        assert SECRET not in json.dumps(listing.models)


class TestKeysNeverLeak:
    """No endpoint may put an API key in a response body.

    Asserted over the whole serialised response rather than field by field, so a
    field added later is covered without anyone remembering to extend this.
    """

    async def test_catalog_response_omits_the_key(self, client) -> None:
        await client.put(
            "/api/v1/providers/openai/credentials", json={"api_key": SECRET}
        )
        response = await client.get("/api/v1/providers")

        assert response.status_code == 200
        assert SECRET not in response.text

        openai = next(p for p in response.json()["providers"] if p["id"] == "openai")
        assert openai["configured"] is True
        assert openai["hint"] == "••••9XYZ"
        assert "api_key" not in openai

    async def test_the_write_itself_does_not_echo_the_key(self, client) -> None:
        response = await client.put(
            "/api/v1/providers/anthropic/credentials", json={"api_key": SECRET}
        )
        assert response.status_code == 200
        assert SECRET not in response.text

    async def test_models_endpoint_does_not_leak(self, client) -> None:
        await client.put(
            "/api/v1/providers/anthropic/credentials", json={"api_key": SECRET}
        )
        response = await client.get("/api/v1/providers/anthropic/models")
        assert SECRET not in response.text


class TestProviderApi:
    async def test_unknown_provider_is_404(self, client) -> None:
        response = await client.get("/api/v1/providers/pigeon/models")
        assert response.status_code == 404

    async def test_selecting_without_a_key_is_refused(self, client) -> None:
        """Caught while the settings panel is open, not thirty seconds into a turn."""
        response = await client.put(
            "/api/v1/providers/selection",
            json={"provider": "openai", "model": "gpt-4o"},
        )
        assert response.status_code == 409
        assert "API key" in response.json()["detail"]

    async def test_selecting_a_local_provider_needs_no_key(self, client) -> None:
        response = await client.put(
            "/api/v1/providers/selection",
            json={"provider": "ollama", "model": "qwen3:14b-16k"},
        )
        assert response.status_code == 200

        catalog_response = await client.get("/api/v1/providers")
        assert catalog_response.json()["selection"] == "ollama/qwen3:14b-16k"

    async def test_selection_survives_a_key_being_added(self, client) -> None:
        await client.put(
            "/api/v1/providers/openai/credentials", json={"api_key": SECRET}
        )
        response = await client.put(
            "/api/v1/providers/selection",
            json={"provider": "openai", "model": "gpt-4o"},
        )
        assert response.status_code == 200

    async def test_deleting_credentials_clears_the_flag(self, client) -> None:
        await client.put(
            "/api/v1/providers/groq/credentials", json={"api_key": SECRET}
        )
        assert (await client.delete("/api/v1/providers/groq/credentials")).status_code == 204

        groq = next(
            p for p in (await client.get("/api/v1/providers")).json()["providers"]
            if p["id"] == "groq"
        )
        assert groq["configured"] is False
        assert groq["hint"] == ""


class TestResolution:
    """Which backend a turn actually gets."""

    async def test_no_selection_falls_back_to_settings(self, store) -> None:
        backend = await BackendFactory().resolve(store)
        assert backend.spec.provider == "ollama"

    async def test_a_selection_is_honoured(self, store) -> None:
        await store.put("openai", api_key=SECRET)
        await store.select(Selection("openai", "gpt-4o"))

        backend = await BackendFactory().resolve(store)
        assert backend.spec.provider == "openai"
        assert backend.spec.model == "gpt-4o"

    async def test_a_selection_whose_key_was_deleted_falls_back(self, store) -> None:
        """A settings row must not be able to make the app unable to run at all."""
        await store.put("openai", api_key=SECRET)
        await store.select(Selection("openai", "gpt-4o"))
        await store.forget("openai")

        backend = await BackendFactory().resolve(store)
        assert backend.spec.provider == "ollama"

    async def test_an_unknown_provider_falls_back(self, store) -> None:
        await store.select(Selection("pigeon-post", "carrier-1"))
        backend = await BackendFactory().resolve(store)
        assert backend.spec.provider == "ollama"

    async def test_a_local_selection_needs_no_credential_row(self, store) -> None:
        await store.select(Selection("ollama", "llama3:8b"))
        backend = await BackendFactory().resolve(store)
        assert backend.spec.model == "llama3:8b"
