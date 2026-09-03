"""The subscription as an ordinary chat backend.

A turn on the subscription runs through the Agent SDK's own loop. But a turn is
not the only thing here that needs a model: import runs one completion per
section and the readiness probe makes a single call, and both ask the factory
for a `ChatBackend`.

With the subscription selected they were handed a `LiteLLMBackend` whose
provider was "claude_code" -- a name litellm has never heard of -- so every one
failed. Import showed it exactly:

    Summary, Work experience, Education, Projects   could not read
    Contact details, Skills, Certifications          read

The ones that came through are parsed mechanically and never reach a model.
"""

from __future__ import annotations

import pytest

from studio.llm import catalog
from studio.llm.backend import BackendError, ModelSpec
from studio.llm.claude_code_backend import ClaudeCodeBackend, _split
from studio.llm.factory import BackendFactory, ProviderConfig


class TestTheFactoryHandsOutTheRightThing:
    def test_the_subscription_is_not_a_litellm_provider(self) -> None:
        backend = BackendFactory().build(
            ProviderConfig(
                provider=catalog.CLAUDE_CODE, model=catalog.CLAUDE_CODE_DEFAULT
            )
        )

        assert isinstance(backend, ClaudeCodeBackend)

    def test_the_default_sentinel_does_not_reach_the_sdk(self) -> None:
        """"default" is our word for "let the plan decide", not a model id."""
        backend = BackendFactory().build(
            ProviderConfig(
                provider=catalog.CLAUDE_CODE, model=catalog.CLAUDE_CODE_DEFAULT
            )
        )

        assert backend.spec.model == ""

    def test_a_named_model_is_passed_through(self) -> None:
        backend = BackendFactory().build(
            ProviderConfig(provider=catalog.CLAUDE_CODE, model="claude-opus-5")
        )

        assert backend.spec.model == "claude-opus-5"

    def test_every_other_provider_still_goes_to_litellm(self) -> None:
        from studio.llm.litellm_backend import LiteLLMBackend

        backend = BackendFactory().build(
            ProviderConfig(provider="ollama", model="mistral-nemo:12b")
        )

        assert isinstance(backend, LiteLLMBackend)


class TestItRefusesWhatItCannotDo:
    async def test_tools_are_refused_rather_than_ignored(self) -> None:
        """A caller wanting tools wants the agent path, which runs the SDK's
        own loop. Accepting schemas here would look like it worked and quietly
        never call anything."""
        backend = ClaudeCodeBackend(ModelSpec(provider=catalog.CLAUDE_CODE, model=""))

        with pytest.raises(BackendError, match="own agent loop"):
            async for _ in backend.stream([{"role": "user", "content": "hi"}], tools=[{}]):
                pass


class TestFoldingMessagesIntoOnePrompt:
    """The SDK takes a prompt string, not a message array."""

    def test_the_system_prompt_is_kept_apart(self) -> None:
        system, prompt = _split(
            [
                {"role": "system", "content": "You transcribe resumes."},
                {"role": "user", "content": "WORK EXPERIENCE ..."},
            ]
        )

        assert system == "You transcribe resumes."
        assert prompt == "WORK EXPERIENCE ..."

    def test_prior_turns_are_labelled(self) -> None:
        """So the model reads its own words as its own."""
        _, prompt = _split(
            [
                {"role": "user", "content": "add Geo TV"},
                {"role": "assistant", "content": "What are the dates?"},
                {"role": "user", "content": "Sep 2025 to present"},
            ]
        )

        assert "You said: What are the dates?" in prompt
        assert "They said: add Geo TV" in prompt

    def test_empty_messages_are_dropped(self) -> None:
        _, prompt = _split(
            [{"role": "user", "content": "real"}, {"role": "assistant", "content": ""}]
        )

        assert prompt == "real"
