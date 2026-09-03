"""Document CRUD and the direct-edit path.

This is the endpoint a user's own typing goes through. The agent will write via
the same repo, so both actors share one mutation path and one set of gates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from studio.doc.apply import OpContext
from studio.doc.autolayout import layout
from studio.doc.legacy import from_resume_data, to_resume_data
from studio.doc.ops import DocOp
from studio.doc.schema import DEFAULT_SECTIONS, StudioDoc, Template, starter_doc
from studio.persistence.repo import DocumentRepo, VersionConflict

router = APIRouter(prefix="/documents", tags=["documents"])


class CreateRequest(BaseModel):
    title: str = "Untitled resume"
    # Either shape is accepted: a StudioDoc, or the legacy ResumeData an old
    # export or parser produces.
    doc: dict[str, Any] | None = None
    resume_data: dict[str, Any] | None = None
    # How the résumé is set. Chosen on the home screen before anything exists
    # to choose it for, so it arrives here rather than as a later edit -- and
    # it is applied after the document is built, so it holds whichever shape
    # above produced it, an import included.
    template: Template | None = None
    # Start from a skeleton -- headings and one empty entry per section -- so a
    # new document is something to fill in rather than a blank sheet. Opt-in so
    # that an import, which arrives with its own content, is untouched.
    starter: bool = False
    # The content is a template's example text rather than anyone's résumé.
    # Set by the gallery, which seeds a document with the card it was shown.
    scaffold: bool = False
    # The text an import was parsed from, when this document came from a file.
    # Stored verbatim and never consulted during editing; it exists so a later
    # question about what the source actually said has an answer.
    source_markdown: str | None = None


class DocumentResponse(BaseModel):
    id: str
    title: str
    version: int
    hash: str
    doc: StudioDoc
    #: When it last changed, ISO-8601 and UTC. The register lists documents by
    #: this rather than by how many writes they have taken: "3 hours ago" says
    #: which résumé you were working on, and "173 writes" does not.
    updated_at: datetime | None = None


class ApplyRequest(BaseModel):
    ops: list[DocOp] = Field(default_factory=list)
    # Nodes the user currently has focus in. The agent is refused on these; a
    # direct edit is not, since the actor is the person holding the caret.
    busy_nids: list[str] = Field(default_factory=list)


class ApplyResponse(BaseModel):
    id: str
    version: int
    hash: str
    doc: StudioDoc
    applied: list[dict[str, Any]]
    rejected: list[dict[str, Any]]


def _repo(request: Request) -> DocumentRepo:
    return request.app.state.repo


def _as_response(state: Any) -> DocumentResponse:
    return DocumentResponse(
        id=state.id,
        title=state.title,
        version=state.version,
        hash=state.content_hash,
        doc=state.doc,
        updated_at=getattr(state, "updated_at", None),
    )


def _parse_version(if_match: str | None) -> int | None:
    """Pull the version out of a weak ETag.

    Lenient about the exact format because proxies rewrite ETags; the version
    prefix is the part we actually enforce on.
    """
    if not if_match:
        return None
    cleaned = if_match.strip().removeprefix("W/").strip('"')
    head, _, _ = cleaned.partition("-")
    try:
        return int(head)
    except ValueError:
        return None


@router.post("", response_model=DocumentResponse, status_code=201)
async def create_document(request: Request, body: CreateRequest) -> DocumentResponse:
    if body.doc is not None:
        doc = StudioDoc.model_validate(body.doc)
    elif body.resume_data is not None:
        doc = from_resume_data(body.resume_data)
    elif body.starter:
        doc = starter_doc(body.template or "plain")
    else:
        doc = StudioDoc(sections=list(DEFAULT_SECTIONS))

    if body.template is not None:
        doc.template = body.template
    if body.scaffold:
        # A document created from a gallery card carries the card's example
        # content. Marking it says the words in it are placeholders, not claims
        # about anybody -- which is what lets the assistant replace them.
        doc.scaffold = True

    # A document without a page cannot be placed on a canvas. Laid out here, at
    # the one point every new document passes through, rather than lazily on
    # first open -- the importer and the templates then need to know nothing
    # about pages at all.
    if not doc.pages:
        doc.pages = layout(doc)

    state = await _repo(request).create(
        doc, title=body.title, source_markdown=body.source_markdown
    )
    return _as_response(state)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(request: Request) -> list[DocumentResponse]:
    return [_as_response(state) for state in await _repo(request).list()]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    request: Request, document_id: str, response: Response
) -> DocumentResponse:
    state = await _repo(request).get(document_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Document not found")
    response.headers["ETag"] = state.etag
    return _as_response(state)


@router.get("/{document_id}/revisions")
async def get_revisions(request: Request, document_id: str) -> dict[str, Any]:
    """The revision number, and the marks of the latest issue.

    Read on page load, beside the conversation. Without it a reload cleared the
    clouds and their readings while the chat came back -- the record of what
    changed on weaker footing than the dialogue about it, which is exactly the
    wrong way round.
    """
    state = await _repo(request).get(document_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Document not found")

    return await _repo(request).revisions(document_id)


@router.get("/{document_id}/messages")
async def get_conversation(request: Request, document_id: str) -> dict[str, Any]:
    """The sidebar conversation for this document, oldest first.

    Read on page load. A chat that lived only in the browser was not merely
    redrawn empty on reload -- the history sent to the model came from it, so a
    refresh silently wiped the assistant's memory of the exchange it was in the
    middle of.
    """
    state = await _repo(request).get(document_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Document not found")

    messages = await _repo(request).conversation(document_id)
    return {
        "messages": [
            {
                "id": str(message.id),
                "role": message.role,
                "text": message.text,
                "status": message.status,
            }
            for message in messages
        ]
    }


@router.delete("/{document_id}/messages", status_code=204)
async def clear_conversation(request: Request, document_id: str) -> None:
    """Forget the conversation, leaving the document untouched.

    The transcript is the only thing removed: edits the assistant made are the
    document's own history and are undone through the op log, not by deleting
    what was said about them.
    """
    await _repo(request).clear_conversation(document_id)


@router.get("/{document_id}/legacy")
async def get_legacy_shape(request: Request, document_id: str) -> dict[str, Any]:
    """The old ResumeData shape, for the ported render templates."""
    state = await _repo(request).get(document_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return to_resume_data(state.doc)


@router.post("/{document_id}/ops", response_model=ApplyResponse)
async def apply_operations(
    request: Request,
    document_id: str,
    body: ApplyRequest,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ApplyResponse:
    ctx = OpContext(
        # A person editing their own document may change anything; the tier
        # system exists to constrain the *model*, not the author.
        granted_tiers={"A", "B", "C"},
        busy_nids=set(body.busy_nids),
        actor="user",
    )
    try:
        state, applied, rejected = await _repo(request).apply(
            document_id, body.ops, expected_version=_parse_version(if_match), ctx=ctx
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Document not found") from None
    except VersionConflict as conflict:
        # 409 carries what changed, so the client can rebase instead of
        # refetching and losing the user's in-flight typing.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "version_conflict",
                "current_version": conflict.current_version,
                "ops_since": conflict.ops_since,
            },
        ) from None

    response.headers["ETag"] = state.etag
    return ApplyResponse(
        id=state.id,
        version=state.version,
        hash=state.content_hash,
        doc=state.doc,
        applied=[entry.model_dump(mode="json") for entry in applied],
        rejected=[entry.model_dump(mode="json") for entry in rejected],
    )


class RevertRequest(BaseModel):
    checkpoint_id: str


@router.post("/{document_id}/revert", response_model=DocumentResponse)
async def revert_document(
    request: Request, document_id: str, body: RevertRequest
) -> DocumentResponse:
    try:
        state = await _repo(request).revert(document_id, body.checkpoint_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Checkpoint not found") from None
    return _as_response(state)


class ConfirmRequest(BaseModel):
    """Which invented lines the person has checked. Empty means all of them."""

    nids: list[str] = Field(default_factory=list)


@router.post("/{document_id}/confirm", response_model=DocumentResponse)
async def confirm_document(
    request: Request, document_id: str, body: ConfirmRequest
) -> DocumentResponse:
    """Accept text the assistant invented while the document was scaffolding.

    Confirming is a statement about truth, not about style, so it clears the
    mark and nothing else -- the words are untouched. It also ends the
    scaffolding: a document whose claims someone has read and accepted is that
    person's résumé, and every guarantee applies to it from here.
    """
    try:
        state = await _repo(request).confirm_unverified(document_id, set(body.nids))
    except KeyError:
        raise HTTPException(status_code=404, detail="Document not found") from None
    return _as_response(state)


class HistoryResponse(DocumentResponse):
    """A document plus which version the reversal touched.

    The version is returned so a client can keep its own cursor honest without
    re-deriving the stack; it is informational, not something the next call
    needs handed back.
    """

    reversed_version: int


@router.post("/{document_id}/undo", response_model=HistoryResponse)
async def undo_document(request: Request, document_id: str) -> HistoryResponse:
    return await _reverse(request, document_id, "undo")


@router.post("/{document_id}/redo", response_model=HistoryResponse)
async def redo_document(request: Request, document_id: str) -> HistoryResponse:
    return await _reverse(request, document_id, "redo")


async def _reverse(request: Request, document_id: str, direction: str) -> HistoryResponse:
    try:
        result = await _repo(request).reverse(document_id, direction=direction)
    except KeyError:
        raise HTTPException(status_code=404, detail="Document not found") from None

    if result is None:
        # Not an error: an empty stack is an ordinary state, and a 4xx here
        # lets the client disable its button without special-casing a message.
        raise HTTPException(
            status_code=409, detail=f"Nothing to {direction}."
        )

    state, version = result
    return HistoryResponse(
        id=state.id,
        title=state.title,
        version=state.version,
        hash=state.content_hash,
        doc=state.doc,
        reversed_version=version,
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document(request: Request, document_id: str) -> Response:
    if not await _repo(request).delete(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(status_code=204)
