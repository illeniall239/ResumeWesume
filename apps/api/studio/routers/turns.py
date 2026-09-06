"""Turn endpoints: start, resume, cancel, confirm.

The turn runs in a background task and writes into a channel; the HTTP response
merely drains that channel. That separation is what makes a turn survive a
dropped connection, which matters because a local model turn can run for
minutes and a laptop lid closing should not abandon half-applied edits.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from studio.agent.claude_code import ClaudeCodeRunner
from studio.agent.loop import TurnRequest, TurnRunner
from studio.llm import catalog
from studio.persistence.repo import DocumentRepo
from studio.streaming import events as ev
from studio.streaming.channel import TurnChannel
from studio.streaming.http import MEDIA_TYPE, STREAM_HEADERS, ndjson

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/turns", tags=["turns"])



class StartTurnRequest(BaseModel):
    document_id: str
    message: str
    job_description: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)
    # Nodes the user has focus in. The agent is refused on these, because the
    # person holding the caret outranks the assistant.
    busy_nids: list[str] = Field(default_factory=list)
    consent_tokens: list[str] = Field(default_factory=list)


@router.post("")
async def start_turn(request: Request, body: StartTurnRequest) -> StreamingResponse:
    app = request.app
    repo = app.state.repo

    state = await repo.get(body.document_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Document not found")

    turn_id = uuid.uuid4().hex
    channel = app.state.turns.create(turn_id, body.document_id)

    # The model the user picked in the sidebar, falling back to a Claude login
    # on this machine, then to .env.
    resolution = await app.state.backends.effective(app.state.providers)

    # Two harnesses, one document. The Agent SDK brings its own agent loop, so
    # the subscription path does not stream through a ChatBackend at all -- the
    # split is here rather than behind the backend interface because pretending
    # a loop is a stream is how one of them ends up quietly broken.
    if resolution.config.provider == catalog.CLAUDE_CODE:
        runner = ClaudeCodeRunner(
            repo=repo, model=catalog.claude_code_model(resolution.config.model)
        )
    else:
        runner = TurnRunner(
            repo=repo, backend=app.state.backends.build(resolution.config)
        )
    turn_request = TurnRequest(
        document_id=body.document_id,
        message=body.message,
        job_description=body.job_description,
        history=body.history,
        busy_nids=set(body.busy_nids),
        consent_tokens=set(body.consent_tokens),
    )

    async def drive() -> None:
        try:
            await runner.run(turn_request, channel)
        except Exception:  # noqa: BLE001 - a crashed turn must still close
            logger.exception("Turn %s failed outside the loop", turn_id)
            channel.close()
        finally:
            # Recorded here rather than in either runner: this is the one place
            # both harnesses pass through, and it is reached even when the
            # client navigated away mid-turn -- the task outlives the response,
            # so the exchange is saved whether or not anyone was watching.
            await _remember(repo, turn_request, channel, turn_id)

    task = asyncio.create_task(drive(), name=f"turn-{turn_id}")
    app.state.turns.attach(turn_id, task)

    return StreamingResponse(
        ndjson(channel),
        media_type=MEDIA_TYPE,
        headers={**STREAM_HEADERS, "X-Turn-Id": turn_id},
    )


@router.get("/{turn_id}/stream")
async def resume_turn(
    request: Request, turn_id: str, from_seq: int = 0
) -> StreamingResponse:
    """Reattach to a running turn after a dropped connection."""
    channel = request.app.state.turns.get(turn_id)
    if channel is None:
        # Either it never existed or its retention window elapsed. The client
        # should refetch the document rather than wait for events that will
        # never come.
        raise HTTPException(
            status_code=404,
            detail={
                "code": "turn_not_found",
                "message": "That turn is no longer available. Reload the document.",
            },
        )

    return StreamingResponse(
        ndjson(channel, from_seq=from_seq),
        media_type=MEDIA_TYPE,
        headers={**STREAM_HEADERS, "X-Turn-Id": turn_id},
    )


@router.delete("/{turn_id}", status_code=202)
async def cancel_turn(request: Request, turn_id: str) -> dict[str, Any]:
    """Ask a turn to stop.

    Cooperative: the loop checks between ops and after each chunk, so
    cancellation always lands on a consistent boundary. Edits already applied
    stay applied and remain undoable through the turn's checkpoint.
    """
    if not request.app.state.turns.cancel(turn_id):
        raise HTTPException(status_code=404, detail="Turn not found")
    return {"turn_id": turn_id, "status": "cancelling"}


@router.get("/{turn_id}/status")
async def turn_status(request: Request, turn_id: str) -> dict[str, Any]:
    channel = request.app.state.turns.get(turn_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    return {
        "turn_id": turn_id,
        "document_id": channel.document_id,
        "finished": channel.finished,
        "cancelled": channel.cancelled,
    }

def _transcript(channel: TurnChannel) -> tuple[str | None, list[dict[str, Any]]]:
    """The reasoning and the tool calls of a finished turn.

    From the same replay the prose comes from. A turn in flight has states this
    does not: running, drafting, awaiting a confirmation. Those belong to the
    client's reducer and are meaningless once the turn is over -- what is left
    is each call and the status it finished in, which is what somebody coming
    back to the conversation needs to read.

    Ordered by the calls, and warnings are kept in place among them: a note
    saying most of the resume was rewritten is about the edits either side of
    it, and floated to the end it reads as being about the last one.
    """
    calls: dict[str, dict[str, Any]] = {}
    activity: list[dict[str, Any]] = []
    thinking: list[str] = []

    for event in channel.replay_from(0):
        if isinstance(event, ev.ThinkingDelta):
            thinking.append(event.text)

        elif isinstance(event, ev.ToolStart):
            entry: dict[str, Any] = {
                "call_id": event.call_id,
                "name": event.name,
                "tier": event.tier,
                # A call the turn never came back to -- cancelled, or the
                # process died. Left as it was rather than guessed at.
                "status": "running",
            }
            calls[event.call_id] = entry
            activity.append(entry)

        elif isinstance(event, ev.PatchApplied):
            entry = calls.get(event.call_id, {})
            entry.update(status="applied", label=event.label, touched=event.touched)

        elif isinstance(event, ev.PatchRejected):
            entry = calls.get(event.call_id, {})
            entry.update(status="rejected", code=event.code, detail=event.message)

        elif isinstance(event, ev.ConfirmRequired):
            calls.get(event.call_id, {}).update(status="confirm")

        elif isinstance(event, (ev.BoardForked, ev.BoardSwitched, ev.BoardRenamed)):
            verb = {
                "board_forked": "started",
                "board_switched": "moved to",
                "board_renamed": "renamed it",
            }[event.type]
            calls.get(event.call_id, {}).update(
                status="applied", label=f"{verb} {event.title}"
            )

        elif isinstance(event, (ev.Warning, ev.DriftSuppressed)):
            source = getattr(event, "source", None)
            # Which model answered is not something a tool did. Stored on the
            # message itself by the client; in this list it would wear the same
            # mark as an edit.
            if source == "model":
                continue
            activity.append(
                {
                    "call_id": f"note_{event.seq}",
                    "name": getattr(event, "guard", None) or source or "note",
                    "tier": "A",
                    # Advisory, never a failure: these exist because the engine
                    # chose to report rather than refuse, and the work landed.
                    "status": "note",
                    "detail": getattr(event, "detail", "")
                    or getattr(event, "message", ""),
                }
            )

    return ("".join(thinking).strip() or None, activity)


async def _remember(
    repo: DocumentRepo,
    request: TurnRequest,
    channel: TurnChannel,
    turn_id: str,
) -> None:
    """Store the exchange so a reload does not lose it.

    Reconstructed from the events the turn already emitted rather than from
    anything the client reports, because the client may be gone. A turn that
    produced no prose at all still stores the user's message: what they asked
    is part of the conversation even when the answer was an error.
    """
    prose = "".join(
        event.text
        for event in channel.replay_from(0)
        if isinstance(event, ev.AssistantDelta)
    ).strip()
    status = next(
        (event.status for event in channel.replay_from(0) if isinstance(event, ev.Done)),
        "failed",
    )

    thinking, activity = _transcript(channel)

    try:
        await repo.add_messages(
            request.document_id,
            [("user", request.message, None), ("assistant", prose, status)],
            turn_id=turn_id,
            thinking=thinking,
            activity=activity or None,
        )
    except Exception:  # noqa: BLE001 -- a transcript must never fail a turn
        logger.exception("Could not store the conversation for turn %s", turn_id)
