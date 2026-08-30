"""Incremental tool-call assembly.

This is what makes the product feel like Pencil rather than a batch job.

Providers stream tool-call arguments as string fragments keyed by index. The
obvious implementation buffers everything, waits for the stream to end, then
parses and executes — which means a five-edit turn shows nothing for thirty
seconds and then five edits at once.

Instead we track a brace/quote depth counter per call and fire the moment a
call's argument JSON balances. In practice that is hundreds of milliseconds to
several seconds before the stream ends, so edits land one after another while
the model is still talking.

The depth counter has to be string-aware: a ``{`` inside a quoted string, or an
escaped quote, must not move the depth. Getting that wrong fires a tool on a
prefix of its arguments, which is worse than waiting.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

from studio.llm.backend import ModelChunk, StreamEnd, TextDelta, ThinkingDelta, ToolCallDelta

logger = logging.getLogger(__name__)


@dataclass
class PendingCall:
    index: int
    id: str = ""
    name: str = ""
    buffer: str = ""
    fired: bool = False

    # Depth bookkeeping, updated incrementally so each fragment costs O(len)
    # rather than rescanning the whole buffer.
    depth: int = 0
    in_string: bool = False
    escaped: bool = False
    seen_open: bool = False

    def feed(self, fragment: str) -> None:
        self.buffer += fragment
        for character in fragment:
            if self.in_string:
                if self.escaped:
                    self.escaped = False
                elif character == "\\":
                    self.escaped = True
                elif character == '"':
                    self.in_string = False
                continue

            if character == '"':
                self.in_string = True
            elif character in "{[":
                self.depth += 1
                self.seen_open = True
            elif character in "}]":
                self.depth -= 1

    @property
    def balanced(self) -> bool:
        """Whether the buffer holds a complete JSON value."""
        return self.seen_open and self.depth == 0 and not self.in_string

    def parse(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.buffer)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class AssembledCall:
    call_id: str
    name: str
    raw_arguments: str
    arguments: dict[str, Any] | None


@dataclass(frozen=True)
class TextEvent:
    text: str


@dataclass(frozen=True)
class ThinkingEvent:
    text: str


@dataclass(frozen=True)
class StreamFinished:
    finish_reason: str | None
    usage: dict[str, Any]


AssemblerEvent = TextEvent | ThinkingEvent | AssembledCall | StreamFinished


@dataclass
class ToolCallAssembler:
    """Turns a chunk stream into text and completed tool calls."""

    calls: dict[int, PendingCall] = field(default_factory=dict)
    text: str = ""
    thinking: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def had_tool_calls(self) -> bool:
        return bool(self.calls)

    def feed(self, chunk: ModelChunk) -> Iterator[AssemblerEvent]:
        """Consume one chunk, yielding whatever became available."""
        if isinstance(chunk, TextDelta):
            self.text += chunk.text
            yield TextEvent(text=chunk.text)
            return

        if isinstance(chunk, ThinkingDelta):
            self.thinking += chunk.text
            yield ThinkingEvent(text=chunk.text)
            return

        if isinstance(chunk, StreamEnd):
            self.finish_reason = chunk.finish_reason
            self.usage = chunk.usage
            yield StreamFinished(chunk.finish_reason, chunk.usage)
            return

        if not isinstance(chunk, ToolCallDelta):
            return

        pending = self.calls.get(chunk.index)
        if pending is None:
            pending = PendingCall(index=chunk.index)
            self.calls[chunk.index] = pending

        if chunk.id:
            pending.id = chunk.id
        if chunk.name:
            pending.name = chunk.name
        if chunk.arguments:
            pending.feed(chunk.arguments)

        # Fire as soon as the arguments close. Waiting for the stream to end
        # would batch every edit in a turn into one visible jump.
        if not pending.fired and pending.name and pending.balanced:
            pending.fired = True
            yield AssembledCall(
                call_id=pending.id or f"call_{pending.index}",
                name=pending.name,
                raw_arguments=pending.buffer,
                arguments=pending.parse(),
            )

    def unfired(self) -> list[AssembledCall]:
        """Calls that never balanced, surfaced at end of stream.

        A truncated argument buffer means the model was cut off mid-call. It is
        reported rather than dropped, because a silently ignored edit is
        indistinguishable to the user from the model choosing not to make it.
        """
        out: list[AssembledCall] = []
        for pending in self.calls.values():
            if pending.fired or not pending.name:
                continue
            pending.fired = True
            out.append(
                AssembledCall(
                    call_id=pending.id or f"call_{pending.index}",
                    name=pending.name,
                    raw_arguments=pending.buffer,
                    arguments=pending.parse(),
                )
            )
        return out

    def assistant_message(self) -> dict[str, Any]:
        """The assistant message to append before the next loop iteration.

        Built locally rather than from a provider helper: the loop must be able
        to continue a conversation identically whatever backend produced it,
        including the scripted one used in tests.
        """
        message: dict[str, Any] = {"role": "assistant", "content": self.text or ""}
        if self.calls:
            message["tool_calls"] = [
                {
                    "id": pending.id or f"call_{pending.index}",
                    "type": "function",
                    "function": {
                        "name": pending.name,
                        "arguments": pending.buffer or "{}",
                    },
                }
                for pending in sorted(self.calls.values(), key=lambda call: call.index)
                if pending.name
            ]
        return message
