"""Uploading and serving images.

Two endpoints and one rule: **nothing is stored as received.** Every upload is
decoded and re-encoded by ``assets/images.py``, which drops EXIF, bounds the
size and guarantees the stored bytes came out of Pillow rather than off the
wire. See that module for why each of those matters.

Serving goes through ``/api/v1`` like everything else, which is not incidental:
headless Chromium renders the print route from the web origin, and an asset on
a different origin would simply not appear in an exported PDF.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from studio.assets.images import ACCEPTED, MAX_BYTES, ImageError, sanitise
from studio.persistence.repo import DocumentRepo

router = APIRouter(prefix="/assets", tags=["assets"])


def _repo(request: Request) -> DocumentRepo:
    return request.app.state.repo


@router.post("", status_code=201)
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    document_id: str | None = Form(default=None),
) -> dict[str, object]:
    """Store an image and return the id a document can place.

    The response carries pixel dimensions because the caller needs them
    immediately: an image dropped on the page should arrive at its true aspect
    ratio, and the alternative is the browser decoding the file a second time
    to find out.
    """
    raw = await file.read()

    # Read the cap here too, not only inside `sanitise`. A 4GB upload would
    # otherwise be buffered in full before anything looked at it.
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Images must be under {MAX_BYTES // (1024 * 1024)}MB.",
        )

    try:
        image = sanitise(raw, declared=file.content_type)
    except ImageError as error:
        # 422, not 500: the file is the problem and the message says how.
        raise HTTPException(status_code=422, detail=str(error)) from error

    asset = await _repo(request).store_asset(
        sha256=image.sha256,
        document_id=document_id,
        mime=image.mime,
        data=image.data,
        width=image.width,
        height=image.height,
        filename=file.filename or "",
    )

    return {
        "id": asset.id,
        "url": f"/api/v1/assets/{asset.id}",
        "mime": asset.mime,
        "width": asset.width,
        "height": asset.height,
        "byte_size": asset.byte_size,
    }


@router.get("/{asset_id}")
async def read_asset(asset_id: str, request: Request) -> Response:
    asset = await _repo(request).get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="No such image.")

    return Response(
        content=asset.data,
        media_type=asset.mime,
        headers={
            # The id is the content hash, so the bytes behind a URL can never
            # change. Immutable caching is exactly right, and it keeps a PDF
            # export from re-fetching the same photo on every page.
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Length": str(asset.byte_size),
        },
    )


__all__ = ["router", "ACCEPTED"]
