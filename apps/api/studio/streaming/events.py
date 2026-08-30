"""The wire protocol between a turn and the browser.

NDJSON over a POST response, not Server-Sent Events and not a WebSocket:

*Not SSE proper.* ``EventSource`` is GET-only and cannot carry a request body,
and a turn needs to POST a message plus document context.

*Not WebSocket.* Nothing here is bidirectional. A half-duplex stream is
trivially proxy-friendly, survives HTTP/1.1 intermediaries, and needs no
connection-state machine on either end.

Every event carries a monotonic ``seq`` scoped to its turn. That single field
buys ordering, gap detection, and resumption after a dropped connection, which
matters because a local model turn can legitimately run for minutes.

The type set is closed and versioned. Both the UI and the model-facing repair
messages key off ``code`` values, so a new failure mode must be named here
rather than described in free text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

PROTOCOL_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Event(BaseModel):
    """Envelope shared by every event."""

    v: int = PROTOCOL_VERSION
    seq: int = 0
    ts: str = Field(default_factory=_now)
    turn_id: str = ""
    type: str = "event"

    def line(self) -> str:
        """One NDJSON line, newline-terminated."""
        return self.model_dump_json(exclude_none=True) + "\n"


class TurnStarted(Event):
    type: Literal["turn_started"] = "turn_started"
    base_version: int
    base_hash: str
    model: str
    provider: str
    tools: list[str] = Field(default_factory=list)


class AssistantDelta(Event):
    type: Literal["assistant_delta"] = "assistant_delta"
    text: str


class ThinkingDelta(Event):
    """Collapsed in the UI. Separated so reasoning does not read as rambling."""

    type: Literal["thinking_delta"] = "thinking_delta"
    text: str


class ToolStart(Event):
    """The model has committed to a tool; arguments may still be streaming."""

    type: Literal["tool_start"] = "tool_start"
    call_id: str
    name: str
    tier: str = "A"


class ToolArgs(Event):
    """Validated arguments, emitted before the edit is attempted."""

    type: Literal["tool_args"] = "tool_args"
    call_id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class PatchApplied(Event):
    type: Literal["patch_applied"] = "patch_applied"
    call_id: str
    ops: list[dict[str, Any]] = Field(default_factory=list)
    touched: list[str] = Field(default_factory=list)
    doc_version: int
    hash: str
    label: str = ""


class PatchRejected(Event):
    type: Literal["patch_rejected"] = "patch_rejected"
    call_id: str
    code: str
    message: str
    # Recovery aid handed to both the UI and the model: the actual current text
    # for a stale expectation, candidate ids for an unknown node.
    hint: dict[str, Any] | None = None
    retryable: bool = True


class ConfirmRequired(Event):
    """A consent-gated edit is paused pending the user's approval."""

    type: Literal["confirm_required"] = "confirm_required"
    call_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk: str = ""
    preview_ops: list[dict[str, Any]] = Field(default_factory=list)
    consent_id: str = ""


class DriftSuppressed(Event):
    """A guard reverted an unrequested change.

    In normal operation this should never fire. When it does it is a bug or an
    exploit attempt, so it is surfaced rather than silently swallowed.
    """

    type: Literal["drift_suppressed"] = "drift_suppressed"
    guard: str
    scope: str
    ref: str
    detail: str = ""


class Warning(Event):
    type: Literal["warning"] = "warning"
    source: str
    message: str
    nid: str | None = None


class NodeLock(Event):
    type: Literal["node_lock"] = "node_lock"
    nids: list[str] = Field(default_factory=list)
    locked: bool = True


class Usage(Event):
    type: Literal["usage"] = "usage"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ms: int = 0
    iterations: int = 0


class ErrorEvent(Event):
    type: Literal["error"] = "error"
    code: str
    message: str
    fatal: bool = True


class Done(Event):
    type: Literal["done"] = "done"
    doc_version: int
    hash: str
    checkpoint_id: str | None = None
    applied: int = 0
    rejected: int = 0
    status: Literal["ok", "partial", "failed", "cancelled"] = "ok"


class Heartbeat(Event):
    """Keeps intermediaries from closing an idle connection.

    A local model can think for minutes before emitting its first token, which
    is well past the idle timeout of a default proxy.
    """

    type: Literal["heartbeat"] = "heartbeat"
