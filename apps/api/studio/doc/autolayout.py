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
    Layout,
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


#: Which sections go in the rail of a sidebar layout.
#:
#: The short, scannable ones. A rail is a narrow column: a list of skills or a
#: degree sits in it comfortably, and a job with four bullets does not -- set
#: at half width it runs to twice the lines and the layout stops saving any
#: space at all. Experience and projects stay in the wide column for the same
#: reason a newspaper puts its long copy there.
_RAIL_SECTIONS: frozenset[str] = frozenset({"skills", "education"})

#: How far short of the full width a frame may be and still count as spanning
#: it. Sub-point drift is float arithmetic, not a column.
_SPAN_SLACK = 0.5

#: Fraction of the content width the rail takes.
_RAIL_FRACTION = 0.32

#: Space between the two columns.
_COLUMN_GAP = 18.0


def _columns(arrangement: Layout, page: PageSpec) -> tuple[float, float, float, float]:
    """Left edge and width of the rail and of the main column, in points."""
    rail_w = page.content_width * _RAIL_FRACTION - _COLUMN_GAP / 2
    main_w = page.content_width - rail_w - _COLUMN_GAP
    if arrangement == "sidebar_right":
        return page.margin + main_w + _COLUMN_GAP, rail_w, page.margin, main_w
    return page.margin, rail_w, page.margin + rail_w + _COLUMN_GAP, main_w


def layout(
    doc: StudioDoc,
    *,
    page: PageSpec = A4,
    arrangement: Layout | None = None,
    heights: dict[str, float] | None = None,
) -> list[PageNode]:
    """One page, one frame per section with something in it.

    In ``stack`` -- what every document has been until now, and still the
    default -- frames are full content width and stacked, which is what makes
    the result identical to the flowing render it replaces and means a migrated
    document cannot come out overlapping or reordered.

    A sidebar puts the short sections in a narrow column beside the long ones.
    That is expressed here, in geometry, and not in the template CSS: the
    canvas gives a section heading and each of its entries a separate frame, so
    a grid inside one frame has nothing to span -- which is exactly why the
    two-column *template* that used to exist was removed. Frames are the only
    thing on this page wide enough to hold a column.

    The heights are advisory here as everywhere else; the client measures and
    corrects them, and it keeps each column on its own cursor while it does.

    ``heights`` overrides the estimates, keyed by ref. It is what makes a
    re-stack possible: the browser has already measured this document and those
    numbers are the accurate ones, so re-deriving the order must not throw them
    away and send every frame back to an estimate.
    """
    arrangement = arrangement or doc.layout
    measured = heights or {}
    rail_x, rail_w, main_x, main_w = _columns(arrangement, page)
    stacked = arrangement == "stack"

    elements: list[FrameElement] = []
    # One cursor per column. In a stack the two are the same cursor, which is
    # what keeps that path byte-identical to what it was.
    cursors = {"rail": page.margin, "main": page.margin}

    def place(ref: str, height: float, column: str = "main") -> None:
        height = measured.get(ref, height)
        if stacked:
            column, x, w = "main", page.margin, page.content_width
        elif column == "rail":
            x, w = rail_x, rail_w
        else:
            x, w = main_x, main_w

        elements.append(
            FrameElement(
                nid=derived_id(NodeKind.FRAME, ref),
                ref=ref,
                rect=Rect(x=x, y=cursors[column], w=w, h=height),
                autogrow="height",
            )
        )
        cursors[column] += height + _GAP

    def span(ref: str, height: float) -> None:
        """Full width, with both columns resuming below it."""
        height = measured.get(ref, height)
        below = max(cursors.values())
        cursors["rail"] = cursors["main"] = below
        elements.append(
            FrameElement(
                nid=derived_id(NodeKind.FRAME, ref),
                ref=ref,
                rect=Rect(x=page.margin, y=below, w=page.content_width, h=height),
                autogrow="height",
            )
        )
        cursors["rail"] = cursors["main"] = below + height + _GAP

    # The header is not a section and has no SectionMeta, but it is content and
    # the coverage gate counts it, so it gets a frame like everything else. It
    # spans in every arrangement: a name is the one thing on a résumé that is
    # never in a column.
    span("personal", _ESTIMATED_HEADER_HEIGHT)

    for meta in ordered_sections(doc):
        # A custom section is keyed by its own heading, so it is not an
        # attribute of the document: `_has_content` cannot find it and
        # `place(meta.key, ...)` would bind a frame to a ref that resolves to
        # nothing. It is placed by nid instead -- which is what the renderer
        # looks it up by, and what the coverage gate counts, so the section is
        # covered by construction rather than by a second rule that agrees.
        own = next((s for s in doc.custom if s.key == meta.key), None)
        if own is not None:
            # A section we have no schema for goes in the main column: its
            # shape is unknown, and the rail is only safe for short things.
            place(own.nid, _ESTIMATED_SECTION_HEIGHT)
            continue
        if not _has_content(doc, meta.key):
            continue
        column = "rail" if meta.key in _RAIL_SECTIONS else "main"
        place(meta.key, _ESTIMATED_HEADING_HEIGHT, column)
        # One frame per entry, not one per section. A frame moves whole, so a
        # section frame holding five jobs is a single 700pt box that cannot
        # share a page with anything -- which turned a two-page resume into
        # three at migration. The flowing renderer this replaces treated an
        # *entry* as the unbreakable unit for exactly the same reason, and
        # matching that keeps pagination identical. It is also what makes
        # "drag this job onto page two" a thing the document can express.
        for entry in _entries(doc, meta.key):
            # An entry sits under its own heading, so it takes the same column.
            place(entry.nid, _ESTIMATED_ENTRY_HEIGHT, column)

    # Free text blocks are content too. On a migrated document there are none;
    # this is here so a re-layout of a canvas document does not strand them.
    if doc.blocks:
        place("blocks", _ESTIMATED_SECTION_HEIGHT)

    return [PageNode(nid=derived_id(NodeKind.PAGE, "1"), size="A4", elements=list(elements))]


def reading_order(doc: StudioDoc) -> list[str]:
    """Every ref the flow can hold, in the order a reader meets them.

    The companion to ``layout``, and deliberately not the same walk. ``layout``
    decides what to *create*, so it skips a section with nothing in it and one
    the user has hidden -- there is no frame to make. This says where a frame
    *belongs* if the page has one, which is a different question, and the only
    one a re-stack can be answered by.

    Deriving both from ``layout`` is what stranded frames. A hidden section
    still owns a frame -- the content is intact and the coverage gate counts it
    -- and that frame was not in ``layout``'s output, so nothing repositioned
    it. Everything else stacked as though the section were not there and drew
    straight over the top of it.

    Hidden and empty sections are therefore included. A frame for one occupies
    no height (see ``restack``) but it keeps its place in the sequence, so
    showing the section again is a re-stack rather than a repair.
    """
    order = ["personal"]
    for meta in sorted(doc.sections, key=lambda meta: meta.order):
        own = next((section for section in doc.custom if section.key == meta.key), None)
        if own is not None:
            order.append(own.nid)
            continue
        order.append(meta.key)
        for entry in _entries(doc, meta.key):
            order.append(entry.nid)
    if doc.blocks:
        order.append("blocks")
    return order


def restack(doc: StudioDoc, *, page: PageSpec = A4) -> None:
    """Put the frames back in the document's own order, in place.

    The stack of frames is what a reader sees -- on screen and in the exported
    PDF, both of which draw by ``rect.y``. Reordering entries rewrote the list
    and moved nothing, so the document said one order and the page showed
    another; on the tailoring path the assistant would report the new order,
    the stored document would agree, and the PDF would keep the old one.

    This re-derives the positions and nothing else. Heights come from the
    frames that are already there, because the browser measured them and the
    server cannot: re-running the estimating layout would be correct in order
    and wrong in geometry, and the page would jump on every move.

    Anything without a ``ref`` -- a box or a line somebody placed by hand -- is
    left exactly where it was. It is not part of the flow and never was. Nor is
    anything ``pinned``: the moment somebody drags a frame they own its
    position, and the browser's own measure pass skips it for the same reason.

    **It paginates.** ``layout`` returns one page with a continuous ``y``,
    because pagination needs measured text and the server has none. Copying
    those numbers onto frames that still sit on their original pages is only
    correct while the resume fits on one sheet. On two, everything on page two
    was handed a page-one coordinate -- offset by the whole height of page one
    -- so the first frame landed near the foot of the sheet and the rest ran off
    it entirely:

        content area 28 .. 813
        page 2 before   28, 178, 328, 350, 438
        page 2 after   736, 886, 1036, 1058, 1146

    That reached the PDF as well as the screen, because the print route renders
    the stored document. The browser repaginated it on the next version, which
    is why dragging anything appeared to "fix" the page -- and why the frame
    that was dragged stayed broken, being pinned and therefore skipped.
    """
    movable = [
        element
        for node in doc.pages
        for element in node.elements
        if getattr(element, "ref", None) and not getattr(element, "pinned", False)
    ]
    if not movable:
        return

    existing = {element.ref: element for element in movable}
    fresh = layout(
        doc,
        page=page,
        heights={ref: element.rect.h for ref, element in existing.items()},
    )
    # `layout` for the column and the width, `reading_order` for the sequence.
    #
    # Not `layout` for both, which is what let a frame be stranded. `layout`
    # derives frames from *content* and skips a section that is hidden or has
    # nothing in it; this has to place what is *on the page*. When those two
    # sets differ the frames in the difference were left at whatever position
    # they last had, while everything else stacked around them as though they
    # were not there -- so hiding Awards drew Education straight on top of it,
    # and the section under that kept a gap the size of the hole.
    geometry = {
        element.ref: element.rect
        for node in fresh
        for element in node.elements
        if getattr(element, "ref", None)
    }
    rank = {ref: index for index, ref in enumerate(reading_order(doc))}
    flow = sorted(
        movable,
        # Anything the order does not name sorts to the end by where it already
        # sits, so an unknown frame is still placed rather than stranded.
        key=lambda element: (rank.get(element.ref, len(rank)), element.rect.y),
    )

    top = page.margin
    bottom = page.margin + page.content_height
    sheets: list[list[Any]] = [[]]

    # One cursor per column, exactly as ``layout`` keeps one -- and for the
    # same reason, which this did not have and needed.
    #
    # A single running cursor is right for a stack and wrong for a sidebar: it
    # gave the rail a `y` below the whole main column, so Education and Skills
    # were stacked *under* Experience instead of beside it. Nothing had shown
    # it, because a sidebar could only be chosen when a document was created
    # and this runs after every edit -- so the first edit to a two-column
    # résumé pulled it into one, and the browser then measured the two-column
    # render and sent geometry back that this flattened again, which is a loop
    # that writes a version per round.
    #
    # A stack is the degenerate case rather than a separate path: every frame
    # in it is full width, so every frame spans, and one cursor is what that
    # comes to.
    columns = {rect.x for rect in geometry.values()}
    cursors: dict[float, float] = {}

    def below_everything() -> float:
        return max(cursors.values(), default=top)

    for element in flow:
        # A frame that draws nothing occupies nothing. Hiding a section keeps
        # its frame -- the content is still there, and the coverage gate counts
        # it -- but the sheet must not reserve room for something no reader
        # will see, or hiding a section would leave its hole behind.
        height = element.rect.h if element.visible else 0.0

        # The column and the width come from `layout` where it has an opinion,
        # which is how a sidebar arrangement keeps its rail. A frame `layout`
        # did not emit keeps the ones it already has. Read before the position
        # is chosen, because which column it lands in decides where it can go.
        rect = geometry.get(element.ref)
        if rect is not None:
            element.rect.x = rect.x
            element.rect.w = rect.w

        # Full width: it sits below everything so far and both columns resume
        # under it. The header is the one of these on an ordinary résumé, and a
        # name is the one thing that is never in a column.
        spans = element.rect.w >= page.content_width - _SPAN_SLACK
        start = below_everything() if spans else cursors.get(element.rect.x, top)

        # A frame moves whole or not at all -- the rule the flowing renderer
        # used and the browser still uses, so the two cannot disagree about
        # where a page breaks. One taller than a whole sheet is left to
        # overflow rather than be split, which is what the browser does when it
        # cannot honour a break.
        if start + height > bottom and start > top and height <= page.content_height:
            sheets.append([])
            cursors = {}
            start = top

        element.rect.y = start
        sheets[-1].append(element)

        end = start + height + _GAP
        if spans:
            for column in columns | {element.rect.x}:
                cursors[column] = end
        else:
            cursors[element.rect.x] = end

    _redistribute(doc, sheets, page=page)


def _redistribute(doc: StudioDoc, sheets: list[list[Any]], *, page: PageSpec) -> None:
    """Move frames onto the sheet the stack put them on, and nothing else.

    Two orders live on a page and they are not the same thing. ``rect.y`` is
    where a frame is drawn; the position in ``elements`` is what is drawn on
    top of what. Rebuilding the list from the stack got the first right by
    destroying the second, so "bring to front" -- an ordinary reorder of this
    very list -- was undone by the next re-stack.

    So the list is edited, not rebuilt: an element that stays on its page keeps
    its place in it, and only one that genuinely changes page is moved.

    Anything the flow does not reach keeps its page and its position outright.
    That is a hand-placed box or a pinned frame, and it is also the frame of a
    *hidden* section -- which ``layout`` does not emit, and which the first
    version of this dropped from the page altogether. The coverage gate caught
    it, correctly: a frame that renders a job is not spare because the section
    is currently switched off.
    """
    flowed: dict[str, int] = {}
    for index, sheet in enumerate(sheets):
        for element in sheet:
            flowed[element.nid] = index

    while len(doc.pages) < len(sheets):
        doc.pages.append(
            PageNode(
                nid=derived_id(NodeKind.PAGE, str(len(doc.pages) + 1)),
                size=doc.pages[0].size if doc.pages else "A4",
                orientation=doc.pages[0].orientation if doc.pages else "portrait",
                elements=[],
            )
        )

    # Take out only what is moving, keeping every other element exactly where
    # it sits in the list.
    moving: dict[int, list[Any]] = {}
    for index, node in enumerate(doc.pages):
        staying = []
        for element in node.elements:
            target = flowed.get(element.nid)
            if target is None or target == index:
                staying.append(element)
            else:
                moving.setdefault(target, []).append(element)
        node.elements = staying

    # Put each migrant on its new page, in reading order, so a frame arriving
    # from the page before lands above one that was already there.
    for target, arrivals in moving.items():
        order = {element.nid: position for position, element in enumerate(sheets[target])}
        node = doc.pages[target]
        node.elements = sorted(
            [*node.elements, *arrivals],
            key=lambda element: order.get(element.nid, len(order)),
        )
