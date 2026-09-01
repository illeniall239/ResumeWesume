"""Turning a flowing document into placed frames.

Every document that exists today is one column, and every fresh import still
arrives that way. This is the pass that gives it a page: one frame per visible
section, stacked down the sheet in the order the sections already declare.

**The heights it writes are advisory, and deliberately so.** The server cannot
measure text. It has no font metrics, no line-breaking and no idea how wide a
glyph is, and a table-driven estimate would disagree with the browser -- which
is the entire class of bug the page-measurement code was written to diagnose.
So every frame is created with ``autogrow="height"``, the client measures on
first open and corrects the geometry with ordinary ops. The numbers here only
have to be sane enough to render before that happens.

Pure, deterministic, and it never mints content: it reads the document and
returns pages. Nothing about the resume's meaning changes.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from studio.doc.nodes import NodeKind
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    FrameElement,
    PageNode,
    Rect,
    SectionMeta,
    StudioDoc,
)


class PageSpec(NamedTuple):
    """Paper, in points. A4 is 210x297mm; the margin matches the PDF export."""

    width: float = 595.276
    height: float = 841.89
    margin: float = 28.35  # 10mm, the margin render_pdf passes to Chromium

    @property
    def content_width(self) -> float:
        return self.width - self.margin * 2

    @property
    def content_height(self) -> float:
        return self.height - self.margin * 2


A4 = PageSpec()


def derived_id(kind: NodeKind, seed: str) -> str:
    """A stable id for a laid-out element, derived rather than minted.

    Migration runs lazily on *read* and is only persisted by the next write, so
    ``layout`` can be called many times for the same document before anything
    is saved. Minting random ids there produced a document whose frames had
    different ids on every request -- so no client could address one, and every
    geometry op was rejected against ids that no longer existed.

    Deriving from the ref makes repeated layouts of the same document
    identical. Ids only have to be unique *within* a document, and there is one
    frame per ref, so the ref is the natural seed.
    """
    from hashlib import blake2s

    from studio.doc.nodes import _ALPHABET, _SUFFIX_LEN

    digest = blake2s(f"{kind.value}:{seed}".encode(), digest_size=8).digest()
    number = int.from_bytes(digest, "big")
    suffix = ""
    for _ in range(_SUFFIX_LEN):
        number, position = divmod(number, len(_ALPHABET))
        suffix += _ALPHABET[position]
    return f"{kind.value}_{suffix}"


#: Rough height for a section before the browser measures it. Only has to be
#: plausible: the reflow pass replaces every one of these on first open.
_ESTIMATED_SECTION_HEIGHT = 96.0
_ESTIMATED_ENTRY_HEIGHT = 88.0
_ESTIMATED_HEADING_HEIGHT = 22.0
_ESTIMATED_HEADER_HEIGHT = 64.0
_GAP = 0.0


def _entries(doc: StudioDoc, key: str) -> list[Any]:
    """The individually placeable entries of a section, if it has any.

    Summary and skills are single blocks with nothing to pull apart, so they
    stay as one frame; the entry sections are where a page break can usefully
    fall.
    """
    if key in ("experience", "education", "projects"):
        return list(getattr(doc, key))
    return []


def ordered_sections(doc: StudioDoc) -> list[SectionMeta]:
    """Visible sections in render order.

    Mirrors what the renderer does, including its fallback: a document with no
    section metadata renders the default order rather than nothing.
    """
    declared = [meta for meta in doc.sections if meta.visible]
    if declared:
        return sorted(declared, key=lambda meta: meta.order)
    return list(DEFAULT_SECTIONS)


def _has_content(doc: StudioDoc, key: str) -> bool:
    if key == "summary":
        return doc.summary is not None
    return bool(getattr(doc, key, None))


def layout(doc: StudioDoc, *, page: PageSpec = A4) -> list[PageNode]:
    """One page, one frame per section with something in it.

    Frames are full content width and stacked, which is what makes the result
    identical to the flowing render it replaces -- and means a migrated
    document cannot come out overlapping or reordered.
    """
    elements: list[FrameElement] = []
    y = page.margin

    def place(ref: str, height: float) -> None:
        nonlocal y
        elements.append(
            FrameElement(
                nid=derived_id(NodeKind.FRAME, ref),
                ref=ref,
                rect=Rect(x=page.margin, y=y, w=page.content_width, h=height),
                autogrow="height",
            )
        )
        y += height + _GAP

    # The header is not a section and has no SectionMeta, but it is content and
    # the coverage gate counts it, so it gets a frame like everything else.
    place("personal", _ESTIMATED_HEADER_HEIGHT)

    for meta in ordered_sections(doc):
        if not _has_content(doc, meta.key):
            continue
        place(meta.key, _ESTIMATED_HEADING_HEIGHT)
        # One frame per entry, not one per section. A frame moves whole, so a
        # section frame holding five jobs is a single 700pt box that cannot
        # share a page with anything -- which turned a two-page resume into
        # three at migration. The flowing renderer this replaces treated an
        # *entry* as the unbreakable unit for exactly the same reason, and
        # matching that keeps pagination identical. It is also what makes
        # "drag this job onto page two" a thing the document can express.
        for entry in _entries(doc, meta.key):
            place(entry.nid, _ESTIMATED_ENTRY_HEIGHT)

    # Free text blocks are content too. On a migrated document there are none;
    # this is here so a re-layout of a canvas document does not strand them.
    if doc.blocks:
        place("blocks", _ESTIMATED_SECTION_HEIGHT)

    return [PageNode(nid=derived_id(NodeKind.PAGE, "1"), size="A4", elements=list(elements))]
