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

Two disjoint subsets share the envelope. The turn events describe an agent
editing an existing document; the import events at the bottom describe a PDF
being turned into one, which has no document and no version until the user
confirms it. They share ``seq``, replay and heartbeats because an import has
exactly the same problem a turn does -- minutes of silence while a local model
works -- and none of that machinery is turn-specific.
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


class BoardForked(Event):
    """A new version of the résumé was started, and the turn moved onto it.

    The client has to follow: everything after this lands on the new board, and
    a page still showing the original would draw patches against a document
    that never received them.
    """

    type: Literal["board_forked"] = "board_forked"
    call_id: str
    board_id: str
    title: str
    #: The version it was copied from, which is left exactly as it was.
    from_board: str


class BoardSwitched(Event):
    """The turn moved onto another existing version of the résumé.

    The client follows, exactly as it does for a fork: everything after this
    lands on that board, and a page still showing the previous one would draw
    patches against a document that never received them.
    """

    type: Literal["board_switched"] = "board_switched"
    call_id: str
    board_id: str
    title: str


class BoardRenamed(Event):
    """A version was given a different name."""

    type: Literal["board_renamed"] = "board_renamed"
    call_id: str
    board_id: str
    title: str


class Done(Event):
    type: Literal["done"] = "done"
    doc_version: int
    hash: str
    #: Where the board the turn ended on stood before it. Kept for the common
    #: turn, which touches one.
    checkpoint_id: str | None = None
    #: Every board the turn touched, and where each stood before it.
    #:
    #: A turn can move between versions, so undoing one has to put back every
    #: board it reached rather than the last it happened to be on.
    checkpoints: list[dict[str, str]] = Field(default_factory=list)
    applied: int = 0
    rejected: int = 0
    status: Literal["ok", "partial", "failed", "cancelled"] = "ok"


class Drafting(Event):
    """A tool call in flight, so the page can show the text arriving.

    A picture, never a change: no op is compiled, no version moves, and a call
    that never balances leaves the document exactly as it was. The patch that
    follows is what actually edits anything.
    """

    type: Literal["drafting"] = "drafting"
    call_id: str
    #: A node id, or a field path like ``personal.phone``.
    target: str
    #: As much of the new text as has been written so far.
    text: str


class Heartbeat(Event):
    """Keeps intermediaries from closing an idle connection.

    A local model can think for minutes before emitting its first token, which
    is well past the idle timeout of a default proxy.
    """

    type: Literal["heartbeat"] = "heartbeat"


# --------------------------------------------------------------------------
# Import
#
# A resume being read out of an uploaded file. ``turn_id`` on the envelope
# carries the import id; nothing else is shared with a turn, and no event here
# refers to a document, because an import has not created one yet -- that
# happens only if the user confirms what they are shown.
# --------------------------------------------------------------------------


class ImportStarted(Event):
    type: Literal["import_started"] = "import_started"
    filename: str
    pages: int = 0
    chars: int = 0
    # 1 or 2. Reported because a wrongly detected second column is the failure
    # most likely to make a parse look inexplicably scrambled.
    columns: int = 1
    warnings: list[str] = Field(default_factory=list)


class SectionFound(Event):
    """Emitted for every section up front, before any parsing starts.

    Gives the client the whole checklist immediately, so the user can see what
    was found in their resume within a second of uploading it rather than
    watching an empty box for a minute.
    """

    type: Literal["section_found"] = "section_found"
    key: str
    heading: str = ""
    chars: int = 0
    order: int = 0
    # False for the sections parsed deterministically, so the UI can show them
    # resolving instantly instead of implying a model call that never happens.
    needs_model: bool = False


class SectionStarted(Event):
    type: Literal["section_started"] = "section_started"
    key: str


class SectionParsed(Event):
    type: Literal["section_parsed"] = "section_parsed"
    key: str
    data: dict[str, Any] = Field(default_factory=dict)
    # The text this was parsed from, so the review screen can show the result
    # beside its source rather than asking the user to take it on trust.
    source_text: str = ""
    ms: int = 0


class SectionFailed(Event):
    """A section that could not be parsed.

    Carries its source text like a successful one. A section that failed is
    exactly the section the user most needs to see, and dropping it silently
    would hide the failure this whole design exists to make visible.
    """

    type: Literal["section_failed"] = "section_failed"
    key: str
    code: Literal["no_json", "invalid_shape", "timeout", "provider_error", "empty"]
    message: str = ""
    source_text: str = ""


class SectionSkipped(Event):
    """A section we recognised but do not import yet."""

    type: Literal["section_skipped"] = "section_skipped"
    key: str
    heading: str = ""
    source_text: str = ""


class ImportReady(Event):
    """The parse is finished and is waiting on the user.

    Carries both shapes on purpose. ``doc`` is for rendering the preview and
    its ids are throwaway; ``resume_data`` is what the client posts back to
    create the document, so ids get minted once, server-side, by the one
    function that has ever minted them.
    """

    type: Literal["import_ready"] = "import_ready"
    title: str = ""
    resume_data: dict[str, Any] = Field(default_factory=dict)
    doc: dict[str, Any] = Field(default_factory=dict)
    source_text: str = ""
    parsed: int = 0
    failed: int = 0
