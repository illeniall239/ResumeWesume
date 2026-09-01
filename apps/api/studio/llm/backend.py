"""The model port.

Hexagonal boundary: the agent loop depends on *this* protocol, never on
litellm. Three things follow from that inversion, and all three matter.

**Testability.** ``ScriptedBackend`` replays a recorded chunk sequence, so the
entire turn loop, assembler and event protocol run in CI with no network and no
nondeterminism. A test seam bolted on later never reaches this deep.

**Provider churn is contained.** Chat-completion wire formats differ per
provider and drift between versions. That variance is normalised into the small
vocabulary below, once, in the adapter.

**The vocabulary is ours.** ``ToolCallDelta`` carries a partial argument string
because that is what every streaming provider actually emits — arguments arrive
as fragments across chunks, keyed by index, and must be reassembled. Modelling
that honestly here is what lets the assembler fire a tool the instant its
arguments balance, rather than waiting for the stream to end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class TextDelta:
    """A fragment of assistant prose."""

    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    """Reasoning content, where a model exposes it separately.

    Kept distinct from ``TextDelta`` so the UI can collapse it. Folding it into
    prose makes a reasoning model look like it is rambling at the user.
    """

    text: str


@dataclass(frozen=True)
class ToolCallDelta:
    """A fragment of a tool call.

    ``index`` is the position within this message's tool-call list and is the
    only reliable correlator mid-stream: ``id`` and ``name`` typically arrive
    once, on the first fragment, and ``arguments`` accumulates across many.
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass(frozen=True)
class StreamEnd:
    """Terminal chunk."""

    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


ModelChunk = TextDelta | ThinkingDelta | ToolCallDelta | StreamEnd


@dataclass(frozen=True)
class ModelSpec:
    """What we are talking to, and what it can do."""

    provider: str
    model: str
    api_base: str | None = None
    supports_tools: bool = True
    context_window: int = 8192

    @property
    def is_local(self) -> bool:
        return self.provider in {"ollama", "openai_compatible"}


@runtime_checkable
class ChatBackend(Protocol):
    """Streams a chat completion."""

    spec: ModelSpec

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        think: bool | None = None,
    ) -> AsyncIterator[ModelChunk]:
        """Yield chunks until the model stops.

        Implementations must yield exactly one ``StreamEnd`` last, even on a
        provider error, so the caller has a single place to finalise a turn.

        ``think`` asks a reasoning model to skip reasoning; ``None`` leaves the
        model's default alone. It earns a place in this deliberately small
        protocol because on a reasoning model it is not a tuning knob but the
        difference between a working call and a failing one: qwen3 spends
        several hundred tokens thinking before its first character of output,
        so a transcription call sized for its answer hits the token ceiling
        mid-thought and returns nothing at all. Measured on qwen3:14b, one
        section: 35.2s with reasoning, 4.0s without, same output.

        Turns leave it alone, because there the reasoning is the point -- the
        chat pane has a control for showing it.
        """
        ...


class BackendError(Exception):
    """A model call failed in a way the caller should surface, not retry."""

    def __init__(self, message: str, *, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code
