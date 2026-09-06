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

    # Which turn snapshots each board currently *is*, and where the last
    # restore on it came from. One lookup per board rather than per message: a
    # conversation is long and the answer is the same for every turn in it.
    wanted: dict[str, set[str]] = {}
    for points in checkpoints.values():
        for checkpoint_id, _version, document_id in points:
            if document_id in boards:
                wanted.setdefault(document_id, set()).add(checkpoint_id)

    undone = {
        board_id: await _repo(request).undone_turn_checkpoint(board_id, ids)
        for board_id, ids in wanted.items()
    }
    restores = {
        board_id: await _repo(request).latest_restore(board_id) for board_id in wanted
    }

    def redo_points(message: Any) -> list[dict[str, str]]:
        """Where every board this turn touched stood *after* it, if it is
        currently undone.

        A revert writes the state it replaced as its own inverse, so the way
        back is already recorded and this only has to find it. Without it a
        reload offered to undo a turn that was already undone -- and taking the
        offer put the document back where it already was, which reads as a
        control that does nothing.
        """
        if message.role != "assistant" or not message.turn_id:
            return []
        found = []
        for checkpoint_id, _version, document_id in checkpoints.get(
            message.turn_id, []
        ):
            # Undone means the board *is* what it was before the turn, by
            # content. Matching instead on which checkpoint was last restored
            # broke after one round trip: undo, redo, undo again leaves the
            # board at the turn's starting content by way of a snapshot the
            # reverts made along the way, and the turn's own id is nowhere in
            # it. The reload then offered to undo a turn the sheet had visibly
            # already undone.
            if undone.get(document_id) != checkpoint_id:
                continue
            restore = restores.get(document_id)
            if restore:
                found.append({"board_id": document_id, "checkpoint_id": restore[1]})
        return found

    def undo_points(message: Any) -> list[dict[str, str]]:
        """Where every board this turn touched stood before it.

        Offered only for boards that have moved since. Every turn takes a
        snapshot, answers included, and restoring a document to the state it is
        already in would write a new version and change nothing on screen —
        which is the shape of a control that appears broken.

        A list because a turn can move between versions, and putting one back
        means putting back every board it reached.
        """
        if message.role != "assistant" or not message.turn_id:
            return []
        return [
            {"board_id": document_id, "checkpoint_id": checkpoint_id}
            for checkpoint_id, version, document_id in checkpoints.get(
                message.turn_id, []
            )
            if document_id in boards and version < boards[document_id].version
        ]

    # Once per message: it is asked for twice below, and it walks every board
    # the turn touched.
    redos = {message.id: redo_points(message) for message in messages}

    return {
        "messages": [
            {
                "id": str(message.id),
                "role": message.role,
                "text": message.text,
                "status": message.status,
                # What the turn was working through and what its tools did.
                # Without these a reload left every past turn as a bare
                # paragraph, and the record of how the resume came to say what
                # it says lasted only as long as the tab.
                "thinking": message.thinking,
                "activity": message.activity,
                # The board the message itself names, for the common turn.
                "checkpoint": next(
                    (
                        point["checkpoint_id"]
                        for point in undo_points(message)
                        if point["board_id"] == message.document_id
                    ),
                    None,
                ),
                # Every board it touched, which is what undoing it must restore.
                "checkpoints": undo_points(message),
                # And where to put it back to, for a turn already undone.
                "redo": redos[message.id],
                "reverted": bool(redos[message.id]),
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
