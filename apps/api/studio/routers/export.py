"""PDF export.

The print route renders the document as it is laid out, page for page. There
used to be a template parameter here whose default was ats -- a flowing
single column that ignored the canvas entirely -- so unless somebody went into
the export dialog and changed it, the file they got was not the document they
had been looking at. On a résumé exported with that default, a section break
landed as a page break: a first page holding a name and a summary, and nothing
else on it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile

from studio.config import settings
from studio.export.pdf import describe_failure, failure_detail, render_pdf

logger = logging.getLogger(__name__)

#: A ceiling on an upload that is parsed into memory as JSON. Generous against
#: a real backup -- a register of fifty résumés with photographs is a few
#: megabytes -- and a bound rather than none at all.
_MAX_BACKUP_BYTES = 256 * 1024 * 1024

router = APIRouter(prefix="/documents", tags=["export"])

#: Everything on this machine, which is not a document and must not live under
#: one. Mounted apart because `/documents/backup` is shadowed by
#: `GET /documents/{document_id}`, registered first, which matched "backup" as
#: an id and answered "Document not found" -- a 404 for a route that exists.
backup_router = APIRouter(tags=["export"])


def _filename(title: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in " -_" else ""
        for character in (title or "resume")
    ).strip()
    return (safe or "resume").replace(" ", "_")


@backup_router.get("/backup")
async def export_everything(request: Request) -> Response:
    """Every résumé on this machine, as one file.

    The backup story for a program people install rather than sign into. No
    server holds a copy: if ``studio.db`` goes, every résumé goes with it, and
    the per-document PDF export does not bring one back -- a PDF is a
    rendering, not a document.

    Sent as a download rather than rendered, and dated in the filename, so
    keeping several is the obvious thing to do with them.
    """
    bundle = await request.app.state.repo.export_everything()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        # `indent` on purpose: this is a file somebody keeps for years and may
        # one day have to read or repair by hand, and the size it costs is
        # nothing beside the base64 images it sits next to.
        content=json.dumps(bundle, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="resumewesume-backup-{stamp}.json"'
            )
        },
    )


@backup_router.post("/restore")
async def restore_everything(request: Request, file: UploadFile = File(...)) -> dict:
    """Put back whatever a backup holds that this machine is missing.

    Never overwrites: an id already here is left alone. A restore is reached
    for when something has already gone wrong, and the one outcome worse than
    not recovering is destroying what survived.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(raw) > _MAX_BACKUP_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That file is larger than a backup this app produces.",
        )

    try:
        bundle = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400, detail="That file is not readable as JSON."
        ) from None
    if not isinstance(bundle, dict):
        raise HTTPException(
            status_code=400, detail="That file is not a ResumeWesume backup."
        )

    try:
        return await request.app.state.repo.restore_everything(bundle)
    except ValueError as error:
        # The repository's own words: it names which of the two things is
        # wrong -- the wrong kind of file, or a version this build cannot read.
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.get("/{document_id}/pdf")
async def export_pdf(
    request: Request,
    document_id: str,
    page_size: str = "A4",
) -> Response:
    state = await request.app.state.repo.get(document_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Document not found")

    url = (
        f"{settings.web_base_url.rstrip('/')}/print/{document_id}"
        f"?pageSize={page_size}"
    )

    try:
        pdf = await render_pdf(url, page_size=page_size)
    except Exception as error:
        # Detail server-side, generic-but-actionable to the client.
        logger.error(
            "PDF export failed for %s: %s", document_id, failure_detail(error)
        )
        raise HTTPException(
            status_code=503, detail=describe_failure(error, url)
        ) from None

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_filename(state.title)}.pdf"'
            )
        },
    )
