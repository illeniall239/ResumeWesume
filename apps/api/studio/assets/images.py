"""Taking an uploaded image apart and putting a safe one back together.

Every image is **re-encoded**, never stored as received. That is the whole
point of this module, and it buys three things at once.

*Metadata is dropped.* A photo from a phone carries GPS coordinates, a device
serial and a timestamp in its EXIF block. This document gets emailed to
strangers, so shipping that with it is a real disclosure, and the person
attaching a headshot has no idea it is there. Decoding to pixels and
re-encoding leaves the metadata behind by construction rather than by
remembering to strip each tag.

*The bytes stop being arbitrary.* What arrives is untrusted input claiming to
be an image. What is stored is something Pillow produced, so a payload that
happens to also parse as something else does not survive the round trip.

*The size becomes bounded.* A 12-megapixel phone photo in a resume is 4MB of
detail nobody will ever see at 40mm wide, and it would be embedded in every
PDF export.

The cost is one decode-encode per upload and a small quality loss on JPEG. Both
are worth it.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Final

#: What we accept. Deliberately short: each entry is a decoder that runs on
#: untrusted bytes, so the list is a liability as much as a feature.
ACCEPTED: Final[frozenset[str]] = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)

#: Longest edge kept. 2000px covers a full-page background on A4 at print
#: resolution; beyond that is detail the page cannot show.
MAX_EDGE: Final[int] = 2000

#: Refused before decoding. A decompression bomb is small on disk and enormous
#: in memory, so the ceiling has to sit in front of the decoder, not after it.
MAX_BYTES: Final[int] = 8 * 1024 * 1024

#: Also refused before decoding: pixel count, which is what actually costs
#: memory. 8000x8000 is 256MB decoded and would arrive as a 40KB PNG.
MAX_PIXELS: Final[int] = 40_000_000


class ImageError(Exception):
    """The upload cannot be stored, in terms the user can act on."""

    def __init__(self, message: str, *, code: str = "invalid_image") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StoredImage:
    data: bytes
    mime: str
    width: int
    height: int
    sha256: str

    @property
    def suffix(self) -> str:
        return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[
            self.mime
        ]


def sanitise(raw: bytes, *, declared: str | None = None) -> StoredImage:
    """Decode, resize if needed, and re-encode without metadata.

    ``declared`` is the browser's content type. It is a hint only -- the format
    is taken from what the decoder actually finds, on the same reasoning as the
    PDF importer's magic-number check: a header is a claim by the caller, and
    the bytes are the evidence.
    """
    if not raw:
        raise ImageError("That file was empty.", code="empty")
    if len(raw) > MAX_BYTES:
        raise ImageError(
            f"Images must be under {MAX_BYTES // (1024 * 1024)}MB.", code="too_large"
        )

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = (probe.format or "").upper()
            width, height = probe.size
    except UnidentifiedImageError as error:
        raise ImageError("That file is not an image we can read.") from error
    except Exception as error:  # noqa: BLE001 - a decoder failure is user input
        raise ImageError("That image could not be read.") from error

    if width * height > MAX_PIXELS:
        # Checked from the header, before any pixels are decoded.
        raise ImageError("That image has too many pixels to process.", code="too_large")

    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(fmt)
    if mime is None:
        raise ImageError(
            "Images must be PNG, JPEG or WebP.", code="unsupported_format"
        )

    with Image.open(io.BytesIO(raw)) as image:
        image.load()

        # Transparency only survives in formats that have it; flattening onto
        # white beats a black box, which is what a naive convert produces.
        if mime == "image/jpeg" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif mime != "image/jpeg" and image.mode not in ("RGB", "RGBA", "L"):
            image = image.convert("RGBA")

        if max(image.size) > MAX_EDGE:
            scale = MAX_EDGE / max(image.size)
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        # A fresh image object carrying only pixels, so nothing from
        # `image.info` -- EXIF, ICC profiles, XMP, PNG text chunks -- can ride
        # along into the output. Copying through raw frame data rather than
        # `getdata()`, which Pillow 14 removes.
        clean = Image.frombytes(image.mode, image.size, image.tobytes())

        if mime == "image/jpeg":
            clean.save(buffer, format="JPEG", quality=88, optimize=True)
        elif mime == "image/png":
            clean.save(buffer, format="PNG", optimize=True)
        else:
            clean.save(buffer, format="WEBP", quality=88)

        data = buffer.getvalue()
        return StoredImage(
            data=data,
            mime=mime,
            width=clean.width,
            height=clean.height,
            sha256=hashlib.sha256(data).hexdigest(),
        )
