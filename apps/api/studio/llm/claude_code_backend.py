"""The Claude subscription as an ordinary chat backend.

Turns on the subscription go through :mod:`studio.agent.claude_code`, where the
Agent SDK owns the whole loop. But a turn is not the only thing in this
application that needs a model: résumé import runs one completion per section,
and the readiness probe makes a single call.

Those callers ask the factory for a `ChatBackend` and get whatever the person
selected. With the subscription selected they were handed a `LiteLLMBackend`
whose provider was ``claude_code`` — a name litellm has never heard of — so
every one of them failed with a provider error. Import surfaced it plainly:

    Summary          could not read
    Work experience  could not read
    Education        could not read
    Projects         could not read

while contact, skills and certifications came through, because those are parsed
mechanically and never reach a model at all.

So the subscription is a backend like any other. No tools and no agent loop:
this is the plain "stream a completion" contract, which is all these callers
want, and it is what makes the subscription usable everywhere rather than only
in the sidebar.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from studio.agent.bridge import run_with_subprocess_support
from studio.llm.backend import BackendError, ModelChunk, ModelSpec, StreamEnd, TextDelta

logger = logging.getLogger(__name__)


class ClaudeCodeBackend:
    """One completion at a time, on the login this machine already has."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        think: bool | None = None,
    ) -> AsyncIterator[ModelChunk]:
        """Yield the reply, then exactly one ``StreamEnd``.

        ``tools`` is accepted and ignored. A caller that wants the model to use
        tools wants the agent path, which runs the SDK's own loop; handing tool
        schemas to a bare completion here would look like it worked and quietly
        never call anything.
        """
        if tools:
            raise BackendError(
                "The Claude subscription runs tools through its own agent loop, "
                "not through a plain completion.",
                code="unsupported",
            )

        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

        system, prompt = _split(messages)
        options = ClaudeAgentOptions(
            system_prompt=system or None,
            # No tools at all, not even Claude Code's own: this is a completion,
            # and a model that could read the filesystem while transcribing a
            # résumé section is a larger surface than the job needs.
            tools=[],
            model=self.spec.model or None,
            max_turns=1,
            # `think=False` is the caller saying the reasoning is not the point
            # -- transcription, mostly. Honoured where the SDK lets us; there is
            # no budget to blow here as there is on a small local model, so the
            # default is left alone otherwise.
            thinking={"type": "disabled"} if think is False else None,
        )

        collected: list[str] = []

        async def run() -> None:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            collected.append(block.text)

        try:
            await run_with_subprocess_support(run)
        except Exception as error:  # noqa: BLE001 -- reported, never raised past here
            logger.exception("Claude subscription completion failed")
            raise BackendError(str(error)) from error

        # Collected and then yielded rather than streamed through: the bridge
        # may be running this on another loop, and a generator cannot be driven
        # across that boundary. Every caller of this path wants the whole answer
        # anyway -- import transcribes a section, the probe asks for a word.
        text = "".join(collected)
        if text:
            yield TextDelta(text=text)
        yield StreamEnd(finish_reason="stop")


def _split(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Fold a message list into the system prompt and one user prompt.

    The SDK takes a single prompt string rather than a message array. Prior
    turns are rendered rather than replayed, and labelled, so the model reads
    its own words as its own -- the same shape :mod:`studio.agent.claude_code`
    uses, and for the same reason.
    """
    system: list[str] = []
    turns: list[str] = []

    for message in messages:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        role = message.get("role")
        if role == "system":
            system.append(content)
        elif role == "assistant":
            turns.append(f"You said: {content}")
        else:
            turns.append(content if len(messages) <= 2 else f"They said: {content}")

    return "\n\n".join(system), "\n\n".join(turns)
