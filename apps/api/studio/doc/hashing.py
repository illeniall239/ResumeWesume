"""Content hashing for optimistic concurrency.

Ported from Resume-Matcher's ``_hash_improved_data``
(``apps/backend/app/routers/resumes.py:154``), including the fix that motivated
it: canonicalise through the model *before* hashing, so a payload that omits
optional fields hashes identically to the same document with those fields
defaulted. Without that step a client round-tripping a partial document gets a
spurious 409, which is indistinguishable from a real conflict.

Unicode normalisation matters for the same reason: "é" typed as one codepoint
and as "e" plus a combining accent are the same text to a user and to a
renderer, and must not look like a concurrent edit.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

from studio.doc.schema import StudioDoc


def _normalize(value: Any) -> Any:
    """NFC-normalise every string in a nested structure, keys included."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {_normalize(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def content_hash(doc: StudioDoc) -> str:
    """A stable SHA-256 over the document's canonical form."""
    canonical = StudioDoc.model_validate(doc.model_dump()).model_dump(mode="json")
    serialized = json.dumps(
        _normalize(canonical),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def etag(version: int, digest: str) -> str:
    """The weak ETag sent to clients and matched on write."""
    return f'W/"{version}-{digest[:12]}"'
