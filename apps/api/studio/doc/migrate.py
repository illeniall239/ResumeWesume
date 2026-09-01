"""Reading a stored document, whatever version wrote it.

There is no migration tooling here -- schema creation is ``create_all`` -- so a
document is upgraded **lazily, on read**, and persisted at the next write. That
is not a compromise: it means no downtime step, no half-migrated table, and a
rollback is just deploying the old code, because a v2 document read by v1 code
would simply be rejected rather than silently mangled.

One function, one place. Every ``StudioDoc.model_validate(row.doc)`` in the
repository goes through here, so there is exactly one answer to "what version
is this and what do we do about it".
"""

from __future__ import annotations

from typing import Any

from studio.doc.autolayout import layout
from studio.doc.schema import StudioDoc

CURRENT_VERSION = 2


def load_doc(raw: dict[str, Any]) -> StudioDoc:
    """Parse a stored document, upgrading it if it predates the canvas."""
    if int(raw.get("schema_version", 1) or 1) < CURRENT_VERSION:
        return _upgrade_v1_to_v2(raw)
    return StudioDoc.model_validate(raw)


def _upgrade_v1_to_v2(raw: dict[str, Any]) -> StudioDoc:
    """Give a flowing document a page.

    The content subtree is untouched -- byte for byte the same nodes with the
    same ids -- and gains only a layout describing where it already was. That
    is what makes the upgrade safe to run on read: it cannot change what the
    resume says, only where it sits, and a document whose ``pages`` are empty
    renders exactly as it did before.
    """
    upgraded = dict(raw)
    upgraded["schema_version"] = CURRENT_VERSION
    upgraded.setdefault("blocks", [])
    upgraded.setdefault("reading_order", None)

    doc = StudioDoc.model_validate({**upgraded, "pages": []})
    doc.pages = layout(doc)
    return doc
