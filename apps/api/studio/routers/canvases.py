"""Canvases: a résumé and the versions of it aimed at particular jobs.

A canvas is what the register lists and what a URL names. Its boards are
ordinary documents carrying a ``canvas_id``, which is the whole point of the
shape: every existing path — ops, undo, export, the agent loop — works on a
board exactly as it worked on a document, because a board *is* a document.

Deliberately its own table rather than a link from one document to another.
Boards are peers with no master, so there is no board for the others to point
at; the canvas is the thing they have in common, and it needs somewhere to be.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from studio.persistence.repo import DocumentRepo
from studio.routers.documents import DocumentResponse, _as_response

router = APIRouter(prefix="/canvases", tags=["canvases"])


def _repo(request: Request) -> DocumentRepo:
    return request.app.state.repo


class CanvasResponse(BaseModel):
    id: str
    title: str
    #: Every board on it, in full.
    #:
    #: In full because the register renders them for real, through the same
    #: `DocumentFlow` the studio and the PDF use — so a card cannot go stale
    #: against the thing it opens.
    boards: list[DocumentResponse] = []
    updated_at: datetime | None = None


def _as_canvas(state: Any) -> CanvasResponse:
    return CanvasResponse(
        id=state.id,
        title=state.title,
        boards=[_as_response(board) for board in state.boards],
        updated_at=state.updated_at,
    )


class CreateCanvasRequest(BaseModel):
    title: str = "Untitled"


@router.post("", response_model=CanvasResponse, status_code=201)
async def create_canvas(request: Request, body: CreateCanvasRequest) -> CanvasResponse:
    """A canvas with no boards on it yet.

    Empty is a real state, not a half-made one: a canvas gets its first board
    the way a résumé already arrives — an imported PDF, or a template — and
    those are separate calls that already work.
    """
    return _as_canvas(await _repo(request).create_canvas(body.title))


@router.get("", response_model=list[CanvasResponse])
async def list_canvases(request: Request) -> list[CanvasResponse]:
    return [_as_canvas(state) for state in await _repo(request).list_canvases()]


@router.get("/{canvas_id}", response_model=CanvasResponse)
async def get_canvas(request: Request, canvas_id: str) -> CanvasResponse:
    state = await _repo(request).get_canvas(canvas_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Canvas not found")
    return _as_canvas(state)


class RenameCanvasRequest(BaseModel):
    title: str


@router.patch("/{canvas_id}", response_model=CanvasResponse)
async def rename_canvas(
    request: Request, canvas_id: str, body: RenameCanvasRequest
) -> CanvasResponse:
    state = await _repo(request).rename_canvas(canvas_id, body.title)
    if state is None:
        if await _repo(request).get_canvas(canvas_id) is None:
            raise HTTPException(status_code=404, detail="Canvas not found")
        # A blank name would leave the register with an unclickable-looking
        # row, so it is refused rather than stored — the same rule a document's
        # own title follows.
        raise HTTPException(status_code=422, detail="A name cannot be empty.")
    return _as_canvas(state)


@router.get("/{canvas_id}/messages")
async def get_conversation(request: Request, canvas_id: str) -> dict[str, Any]:
    """Everything said about this résumé, across its versions.

    Canvas-wide because the conversation is: you ask for a version aimed at one
    job, read it back, then ask for another. Held per board it split into as
    many transcripts as there were versions, and switching versions silently
    changed the subject.

    Each message still records which board it acted on, so the sidebar can say
    so where it matters — the conversation is about the résumé, but an edit
    landed on exactly one version of it.
    """
    if await _repo(request).get_canvas(canvas_id) is None:
        raise HTTPException(status_code=404, detail="Canvas not found")

    messages = await _repo(request).canvas_conversation(canvas_id)
    checkpoints = await _repo(request).turn_checkpoints_for_canvas(canvas_id)
    boards = {
        board.id: board
        for board in (await _repo(request).get_canvas(canvas_id)).boards
    }

    def undo_point(message: Any) -> str | None:
        """The snapshot this turn can be put back to, if that means anything.

        Offered only where the board has moved since the snapshot was taken.
        Every turn gets one, answers included, and restoring a document to the
        state it is already in would write a new version and change nothing on
        screen.
        """
        if message.role != "assistant" or not message.turn_id:
            return None
        found = checkpoints.get(message.turn_id)
        if found is None:
            return None
        checkpoint_id, version, document_id = found
        board = boards.get(document_id)
        return checkpoint_id if board and version < board.version else None

    return {
        "messages": [
            {
                "id": str(message.id),
                "role": message.role,
                "text": message.text,
                "status": message.status,
                "checkpoint": undo_point(message),
                # Which version this turn acted on. The conversation is about
                # the résumé; an edit landed on one of its versions.
                "board_id": message.document_id,
                "board": (boards.get(message.document_id).title
                          if message.document_id in boards else None),
            }
            for message in messages
        ]
    }


@router.delete("/{canvas_id}/messages", status_code=204)
async def clear_conversation(request: Request, canvas_id: str) -> None:
    if await _repo(request).get_canvas(canvas_id) is None:
        raise HTTPException(status_code=404, detail="Canvas not found")
    await _repo(request).clear_canvas_conversation(canvas_id)


@router.delete("/{canvas_id}", status_code=204)
async def delete_canvas(request: Request, canvas_id: str) -> None:
    """Delete a canvas and every board on it.

    The boards go with it. They are versions of one résumé; keeping them would
    leave a set of sheets with nothing in common and no way back to each other.
    """
    if not await _repo(request).delete_canvas(canvas_id):
        raise HTTPException(status_code=404, detail="Canvas not found")
