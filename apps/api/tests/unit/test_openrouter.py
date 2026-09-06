"""OpenRouter's catalogue, which is a different problem from a provider's.

OpenRouter was in the provider table and could run a turn -- the litellm
prefix, the base and the resilience entry were all there. What it did not have
was a listing of its own, and it borrowed the generic OpenAI one, which got two
things wrong that only show up on a gateway.

Measured against the live catalogue at the time this was written: 431 models,
of which 364 declare tool support. Listed the generic way, the menu was the
first sixty ids alphabetically -- ``aion-labs`` through ``deepseek``, with no
OpenAI models in it at all, no Google, and eight entries that cannot call a
tool and therefore cannot edit a resume.

And it never got that far anyway: the listing refused to ask until a key was
stored, which is exactly backwards for a gateway. What somebody wants to know
before signing up is what they would be able to run.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from studio.llm import catalog

BASE = "https://openrouter.ai/api/v1/models"


def model(
    ident: str,
    *,
    tools: bool = True,
    created: int = 1_700_000_000,
) -> dict:
    """One catalogue entry, in the shape OpenRouter actually answers with."""
    return {
        "id": ident,
        "name": ident,
        "created": created,
        "context_length": 200_000,
        "supported_parameters": (
            ["max_tokens", "temperature", "tools", "tool_choice"]
            if tools
            else ["max_tokens", "temperature"]
        ),
    }


def answers(*entries: dict) -> None:
    respx.get(BASE).mock(return_value=httpx.Response(200, json={"data": list(entries)}))


class TestListingWithoutAKey:
    """The question people have before they pay is what they would get."""

    @respx.mock
    async def test_the_catalogue_is_read_with_no_key_at_all(self) -> None:
        answers(model("openai/gpt-5"), model("anthropic/claude-opus-5"))

        listing = await catalog.list_models("openrouter")

        assert listing.source == "live"
        assert listing.models == ["anthropic/claude-opus-5", "openai/gpt-5"]

    @respx.mock
    async def test_a_key_is_still_sent_when_there_is_one(self) -> None:
        # An authenticated call can reflect what the account may actually
        # route to, so the key is used where it exists -- it is simply not
        # required.
        answers(model("openai/gpt-5"))

        await catalog.list_models("openrouter", api_key="sk-or-v1-secret")

        assert respx.calls.last.request.headers["authorization"] == (
            "Bearer sk-or-v1-secret"
        )

    async def test_every_other_paid_provider_still_waits_for_its_key(self) -> None:
        # The exemption is a property of the provider, not a hole in the rule.
        # Anthropic's model list genuinely needs authenticating.
        listing = await catalog.list_models("anthropic")

        assert listing.source == "fallback"
        assert "API key" in listing.detail


class TestWhatIsOfferedFromIt:
    @respx.mock
    async def test_a_model_that_cannot_call_a_tool_is_not_offered(self) -> None:
        # Every edit this app makes is a tool call, so a model without them
        # cannot do the one thing it would be chosen for. The same guard the
        # Gemini listing gets from `generateContent`.
        answers(
            model("qwen/qwen3-max"),
            model("anthracite-org/magnum-v4-72b", tools=False),
        )

        listing = await catalog.list_models("openrouter")

        assert listing.models == ["qwen/qwen3-max"]

    @respx.mock
    async def test_the_batch_route_is_not_offered(self) -> None:
        # A turn here is streamed and watched as each edit lands. The batch
        # route is asynchronous and answers later, which is not that.
        answers(model("openai/gpt-5"), model("openai/gpt-5:batch"))

        listing = await catalog.list_models("openrouter")

        assert listing.models == ["openai/gpt-5"]

    @respx.mock
    async def test_a_free_route_is_offered(self) -> None:
        # `:free` is a real way to run a model and the one most worth having on
        # a local-first app. Only `:batch` is excluded, not every suffix.
        answers(model("deepseek/deepseek-v3:free"))

        listing = await catalog.list_models("openrouter")

        assert listing.models == ["deepseek/deepseek-v3:free"]


class TestWhichSixty:
    """The cut is the decision, because the catalogue is four times the menu."""

    @respx.mock
    async def test_recent_models_survive_the_cut_and_early_letters_do_not(
        self,
    ) -> None:
        # The defect, in miniature. Alphabetically `aion-labs` wins every time
        # and `openai` never appears; by recency the menu holds what somebody
        # would actually pick.
        old = [model(f"aion-labs/aion-{n}", created=1) for n in range(catalog.MAX_MODELS)]
        answers(*old, model("openai/gpt-5", created=2_000_000_000))

        listing = await catalog.list_models("openrouter")

        assert "openai/gpt-5" in listing.models
        assert len(listing.models) == catalog.MAX_MODELS

    @respx.mock
    async def test_the_menu_is_still_alphabetical(self) -> None:
        # Recency chooses the members; it does not order them. A menu that
        # reshuffles itself as a gateway adds models is one nobody can learn.
        answers(
            model("zed/zed-1", created=3),
            model("alpha/alpha-1", created=2),
            model("mid/mid-1", created=1),
        )

        listing = await catalog.list_models("openrouter")

        assert listing.models == ["alpha/alpha-1", "mid/mid-1", "zed/zed-1"]

    @respx.mock
    async def test_an_entry_with_no_date_does_not_displace_one_that_has_it(
        self,
    ) -> None:
        # Treated as oldest rather than newest: a model that will not say when
        # it arrived should not push out one that did.
        undated = model("mystery/model")
        del undated["created"]
        answers(undated, *[model(f"known/m{n}", created=1_000 + n) for n in range(catalog.MAX_MODELS)])

        listing = await catalog.list_models("openrouter")

        assert "mystery/model" not in listing.models


class TestWhenItCannotBeReached:
    @respx.mock
    async def test_it_falls_back_rather_than_failing(self) -> None:
        # Same contract as every other provider: a dropdown that hangs or
        # empties is worse than one showing the registry and saying so.
        respx.get(BASE).mock(side_effect=httpx.ConnectError("no route"))

        listing = await catalog.list_models("openrouter")

        assert listing.source == "fallback"
        assert listing.models
        assert listing.detail

    @respx.mock
    async def test_a_rejected_key_is_reported_without_echoing_it(self) -> None:
        respx.get(BASE).mock(return_value=httpx.Response(401, json={"error": "nope"}))

        listing = await catalog.list_models("openrouter", api_key="sk-or-v1-secret")

        assert listing.source == "fallback"
        assert "sk-or-v1-secret" not in listing.detail


class TestRunningATurnOnIt:
    def test_the_model_is_prefixed_for_litellm(self) -> None:
        from studio.llm.litellm_backend import qualified_model

        assert qualified_model("openrouter", "openai/gpt-5") == (
            "openrouter/openai/gpt-5"
        )

    def test_it_is_not_prefixed_twice(self) -> None:
        from studio.llm.litellm_backend import qualified_model

        assert qualified_model("openrouter", "openrouter/openai/gpt-5") == (
            "openrouter/openai/gpt-5"
        )

    def test_litellm_is_left_to_address_it(self) -> None:
        # `routes_itself`: litellm appends its own path to any base it is
        # handed, so forwarding the listing base would build a URL that is
        # right for the catalogue and wrong for completions.
        assert catalog.PROVIDERS["openrouter"].routes_itself is True
