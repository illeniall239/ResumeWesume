"""PDF export."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from studio.config import settings
from studio.export.pdf import describe_failure, failure_detail, render_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["export"])


def _filename(title: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in " -_" else ""
        for character in (title or "resume")
    ).strip()
    return (safe or "resume").replace(" ", "_")


@router.get("/{document_id}/pdf")
async def export_pdf(
    request: Request,
    document_id: str,
    template: str = "ats",
    page_size: str = "A4",
) -> Response:
    state = await request.app.state.repo.get(document_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Document not found")

    url = (
        f"{settings.web_base_url.rstrip('/')}/print/{document_id}"
        f"?template={template}&pageSize={page_size}"
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
