"""A backend that replays recorded chunks.

The whole point of the port in ``backend.py``. With this, the turn loop, the
assembler, the salvage ladder and the SSE protocol all run in CI against real
recorded model output, with no network and no nondeterminism.

Cassettes are recorded once from a live provider (``RECORD_LLM=1``) and
committed. Recording at the *transport* layer rather than mocking the agent's
inputs is what makes the tests worth having: provider quirks, chat-template
oddities and mid-token chunk boundaries are all reproduced exactly, and those
are precisely where the bugs live.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

from studio.llm.backend import (
    BackendError,
    ChatBackend,
    ModelChunk,
    ModelSpec,
    StreamEnd,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
)

_LOCAL_SPEC = ModelSpec(provider="scripted", model="scripted", supports_tools=True)


class ScriptedBackend(ChatBackend):
    """Replays one scripted response per ``stream`` call.

    A turn may loop several times (call tools, read results, continue), so the
    script is a *list of turns*, each a list of chunks. Running out of scripted
    turns raises rather than silently returning empty: a test that loops more
    than expected is a real finding, not something to paper over.
    """

    def __init__(
        self,
        turns: list[list[ModelChunk]],
        *,
        spec: ModelSpec | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.spec = spec or _LOCAL_SPEC
        self._turns = list(turns)
        self._call = 0
        self._fail_with = fail_with
        # Recorded for assertions: what the agent actually sent.
        self.received: list[dict[str, Any]] = []

    @property
    def calls(self) -> int:
        return self._call

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
        self.received.append(
            {
                "messages": messages,
                "tools": [tool["function"]["name"] for tool in tools or []],
                "tool_choice": tool_choice,
                "think": think,
            }
        )

        if self._fail_with is not None:
            raise self._fail_with

        if self._call >= len(self._turns):
            raise BackendError(
                f"ScriptedBackend exhausted after {self._call} turns; "
                "the loop iterated more than the script expected"
            )

        chunks = self._turns[self._call]
        self._call += 1

        for chunk in chunks:
            yield chunk
        if not any(isinstance(chunk, StreamEnd) for chunk in chunks):
            yield StreamEnd(finish_reason="stop")


# --- helpers for writing scripts by hand -----------------------------------


def say(text: str, *, chunk_size: int = 8) -> list[ModelChunk]:
    """Prose, split across chunks the way a real stream arrives."""
    return [
        TextDelta(text=text[index : index + chunk_size])
        for index in range(0, len(text), chunk_size)
    ]


def call_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    index: int = 0,
    call_id: str | None = None,
    split: int = 12,
) -> list[ModelChunk]:
    """A tool call whose arguments arrive in fragments.

    Splitting by default is deliberate: a test that feeds whole arguments in one
    chunk never exercises the assembler's buffering, which is where the
    interesting failures are.
    """
    encoded = json.dumps(arguments)
    chunks: list[ModelChunk] = [
        ToolCallDelta(index=index, id=call_id or f"call_{index}", name=name, arguments="")
    ]
    chunks.extend(
        ToolCallDelta(index=index, arguments=encoded[position : position + split])
        for position in range(0, len(encoded), split)
    )
    return chunks


def done(reason: str = "stop", **usage: int) -> list[ModelChunk]:
    return [StreamEnd(finish_reason=reason, usage=dict(usage))]


def think(text: str) -> list[ModelChunk]:
    return [ThinkingDelta(text=text)]


def turn(*parts: Iterable[ModelChunk]) -> list[ModelChunk]:
    """Concatenate the pieces of one model turn."""
    out: list[ModelChunk] = []
    for part in parts:
        out.extend(part)
    return out


# --- cassettes --------------------------------------------------------------


def _encode(chunk: ModelChunk) -> dict[str, Any]:
    if isinstance(chunk, TextDelta):
        return {"t": "text", "text": chunk.text}
    if isinstance(chunk, ThinkingDelta):
        return {"t": "thinking", "text": chunk.text}
    if isinstance(chunk, ToolCallDelta):
        return {
            "t": "tool",
            "index": chunk.index,
            "id": chunk.id,
            "name": chunk.name,
            "arguments": chunk.arguments,
        }
    return {"t": "end", "finish_reason": chunk.finish_reason, "usage": chunk.usage}


def _decode(raw: dict[str, Any]) -> ModelChunk:
    kind = raw.get("t")
    if kind == "text":
        return TextDelta(text=raw.get("text", ""))
    if kind == "thinking":
        return ThinkingDelta(text=raw.get("text", ""))
    if kind == "tool":
        return ToolCallDelta(
            index=raw.get("index", 0),
            id=raw.get("id"),
            name=raw.get("name"),
            arguments=raw.get("arguments", ""),
        )
    return StreamEnd(
        finish_reason=raw.get("finish_reason"), usage=raw.get("usage") or {}
    )


def save_cassette(path: Path, turns: list[list[ModelChunk]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [[_encode(chunk) for chunk in turn_chunks] for turn_chunks in turns]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_cassette(path: Path) -> list[list[ModelChunk]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [[_decode(raw) for raw in turn_chunks] for turn_chunks in payload]
