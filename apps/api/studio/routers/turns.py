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

from studio.agent.loop import TurnRequest, TurnRunner
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

    runner = TurnRunner(repo=repo, backend=app.state.backends.from_settings())
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
