"""Uploading a resume and streaming the parse back.

Beside ``turns.py`` rather than inside ``documents.py``: this is a channel-
driven stream with a detached task behind it, which is the turn router's shape
exactly, and nothing like the request/response CRUD ``documents.py`` documents
itself as being.

Note what this router does *not* do. It never creates a document. The stream is
a pure function of the uploaded bytes, and the parsed result travels to the
client, which shows it to the user and posts it back to ``POST /documents`` if
they accept it. That keeps document creation in the one place that already does
it, and means nothing is persisted from a parse the user never saw.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from studio.ingest.pipeline import ImportRequest, ImportRunner
from studio.streaming.http import MEDIA_TYPE, STREAM_HEADERS, ndjson

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/imports", tags=["imports"])

# Comfortably above a text-heavy resume and far below anything that would tie
# up a worker. A 5MB "resume" is a scan, which we cannot read anyway.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

_PDF_MAGIC = b"%PDF-"


def _read_id(request: Request) -> str:
    return uuid.uuid4().hex


@router.post("")
async def start_import(
    request: Request, file: UploadFile = File(...)
) -> StreamingResponse:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )

    # Read here, in the handler, and hand the task bytes. The request scope
    # closes when this function returns and takes the spooled upload file with
    # it, so a detached task holding the UploadFile would read from a closed
    # file at an unpredictable moment.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.",
        )
    if not data:
        raise HTTPException(status_code=400, detail="That file was empty.")

    # Decided on the bytes, not on the declared content type. A Content-Type
    # header is asserted by the client, which makes it a claim rather than
    # evidence -- the same reason an op's tier is derived from what it touches
    # instead of from what the caller says it is.
    if not data.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=415,
            detail="Only PDF resumes can be uploaded at the moment.",
        )

    app = request.app
    import_id = _read_id(request)
    channel = app.state.imports.create(import_id)
    # Same selection the assistant uses: an import parsed by one model
    # while the chat edits with another is a difference nobody asked for.
    runner = ImportRunner(backend=await app.state.backends.resolve(app.state.providers))
    filename = file.filename or "resume.pdf"

    async def drive() -> None:
        try:
            await runner.run(ImportRequest(data=data, filename=filename), channel)
        except Exception:  # noqa: BLE001 - the channel must always be closed
            logger.exception("import %s died outside the runner", import_id)
            channel.close()

    app.state.imports.attach(import_id, asyncio.create_task(drive()))

    return StreamingResponse(
        ndjson(channel),
        media_type=MEDIA_TYPE,
        headers={**STREAM_HEADERS, "X-Import-Id": import_id},
    )


@router.get("/{import_id}/stream")
async def resume_import(
    request: Request, import_id: str, from_seq: int = 0
) -> StreamingResponse:
    """Reattach to a running import after a dropped connection.

    Worth having here more than it is for a turn: losing a three-minute parse
    to a flaky connection means uploading again and waiting again.
    """
    channel = request.app.state.imports.get(import_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="No such import.")

    return StreamingResponse(
        ndjson(channel, from_seq=from_seq),
        media_type=MEDIA_TYPE,
        headers={**STREAM_HEADERS, "X-Import-Id": import_id},
    )


@router.delete("/{import_id}", status_code=202)
async def cancel_import(request: Request, import_id: str) -> dict[str, str]:
    """Stop an import the user no longer wants.

    Honoured between sections rather than mid-generation: a running completion
    has no safe interruption point, and the wait is bounded by one section.
    """
    channel = request.app.state.imports.get(import_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="No such import.")
    request.app.state.imports.cancel(import_id)
    return {"import_id": import_id, "status": "cancelling"}
