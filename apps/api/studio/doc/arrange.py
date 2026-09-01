"""Semantic arrangement: the model picks the intent, the server does the sums.

This exists because the alternative was rejected on purpose. A ``set_geometry``
tool would hand a model free coordinates, and geometry is Tier A -- it cannot
change a word, cannot delete anything and is exactly invertible, so it is
ungated by design. That is right for a person dragging a box and wrong for a
model: ``set_geometry(frm_experience, y=-9999)`` would push an entire
employment history off the page under an ungated grant, and no drift guard
could tell it from a legitimate nudge, because every guard compares *words*.

So the model never names a number. It names a preset and the elements to apply
it to, and every coordinate is computed here from rects that already exist.
That is what makes the capability safe rather than merely convenient, and it is
the doctrine ``tools.py`` already states: move work out of the unreliable model
and into the schema.

Two properties are load-bearing, and both are asserted by the tests:

**Nothing new goes off-page.** Every rect produced is clamped to the sheet, so
an arrangement cannot hide anything. That is the exact abuse a geometry tool
would have allowed, and closing it is what keeps this Tier A.

**Nothing changes size unless the preset says so.** Alignment and distribution
move elements; only ``match_*`` resizes. A preset that silently resized would
be a content-shape change wearing a layout name.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

from studio.doc.ops import SetGeometry
from studio.doc.schema import AnyElement, PageNode, Rect, StudioDoc

#: Every arrangement the model may ask for. Named for what the user would say,
#: not for the arithmetic, because the name is what the model has to choose
#: from a description.
Preset = Literal[
    "align_left",
    "align_right",
    "align_top",
    "align_bottom",
    "align_horizontal_centers",
    "align_vertical_centers",
    "distribute_horizontally",
    "distribute_vertically",
    "center_on_page_horizontally",
    "center_on_page_vertically",
    "center_on_page",
    "match_width",
    "match_height",
]

PRESETS: tuple[str, ...] = (
    "align_left",
    "align_right",
    "align_top",
    "align_bottom",
    "align_horizontal_centers",
    "align_vertical_centers",
    "distribute_horizontally",
    "distribute_vertically",
    "center_on_page_horizontally",
    "center_on_page_vertically",
    "center_on_page",
    "match_width",
    "match_height",
)

#: Presets that need three elements to mean anything: with two, "spread these
#: evenly" is already true and the op would be a no-op that burned a version.
NEEDS_THREE: frozenset[str] = frozenset(
    {"distribute_horizontally", "distribute_vertically"}
)

#: Presets that act on one element as sensibly as on ten, because the page is
#: the reference rather than the other elements.
WORKS_ALONE: frozenset[str] = frozenset(
    {
        "center_on_page_horizontally",
        "center_on_page_vertically",
        "center_on_page",
    }
)


class ArrangeError(Exception):
    """The arrangement cannot be computed, in terms the model can act on."""


class Paper(NamedTuple):
    """The sheet an arrangement is measured against, in points."""

    width: float = 595.276
    height: float = 841.89


#: A4 portrait and US Letter, the two sizes ``PageNode`` allows.
_PAPER: dict[tuple[str, str], Paper] = {
    ("A4", "portrait"): Paper(595.276, 841.89),
    ("A4", "landscape"): Paper(841.89, 595.276),
    ("Letter", "portrait"): Paper(612.0, 792.0),
    ("Letter", "landscape"): Paper(792.0, 612.0),
}


def paper_of(page: PageNode) -> Paper:
    return _PAPER.get((page.size, page.orientation), Paper())


def page_holding(doc: StudioDoc, nid: str) -> PageNode | None:
    for page in doc.pages:
        if any(element.nid == nid for element in page.elements):
            return page
    return None


def _clamp(rect: Rect, paper: Paper) -> Rect:
    """Keep a rect on the sheet.

    The safety property, not a nicety: this is what makes it impossible for an
    arrangement to move something out of sight, which is the whole reason a
    free ``set_geometry`` tool was refused. Size is capped first, so an
    over-wide ``match_width`` cannot then be shoved off the right edge.
    """
    w = min(rect.w, paper.width)
    h = min(rect.h, paper.height)
    return Rect(
        x=min(max(rect.x, 0.0), max(0.0, paper.width - w)),
        y=min(max(rect.y, 0.0), max(0.0, paper.height - h)),
        w=w,
        h=h,
    )


def _rects(page: PageNode, nids: list[str]) -> list[tuple[str, Rect]]:
    """The chosen elements' rects, in the order the caller named them."""
    by_nid: dict[str, AnyElement] = {e.nid: e for e in page.elements}
    return [
        (nid, by_nid[nid].rect.model_copy()) for nid in nids if nid in by_nid
    ]


def compute(page: PageNode, preset: str, nids: list[str]) -> dict[str, Rect]:
    """The rect each named element should end up with.

    Pure, and separate from the tool, because this is the part with the edge
    cases -- an even distribution of three boxes where the outer two are the
    anchors, a match against the largest rather than the first named -- and it
    is worth testing without a document, a registry or a model anywhere near
    it.
    """
    chosen = _rects(page, nids)
    if not chosen:
        return {}

    paper = paper_of(page)
    lefts = [rect.x for _, rect in chosen]
    rights = [rect.x + rect.w for _, rect in chosen]
    tops = [rect.y for _, rect in chosen]
    bottoms = [rect.y + rect.h for _, rect in chosen]
    result: dict[str, Rect] = {}

    if preset == "align_left":
        edge = min(lefts)
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"x": edge})

    elif preset == "align_right":
        edge = max(rights)
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"x": edge - rect.w})

    elif preset == "align_top":
        edge = min(tops)
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"y": edge})

    elif preset == "align_bottom":
        edge = max(bottoms)
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"y": edge - rect.h})

    elif preset == "align_horizontal_centers":
        # The centre of the selection's bounding box, not the mean of the
        # centres: the mean drifts towards wherever the most boxes happen to
        # be, which is not what "line these up" means.
        centre = (min(lefts) + max(rights)) / 2
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"x": centre - rect.w / 2})

    elif preset == "align_vertical_centers":
        centre = (min(tops) + max(bottoms)) / 2
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"y": centre - rect.h / 2})

    elif preset == "distribute_horizontally":
        result = _distribute(chosen, axis="x")

    elif preset == "distribute_vertically":
        result = _distribute(chosen, axis="y")

    elif preset in ("center_on_page_horizontally", "center_on_page"):
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"x": (paper.width - rect.w) / 2})

    if preset in ("center_on_page_vertically", "center_on_page"):
        for nid, rect in chosen:
            # Built on whatever the horizontal pass produced, so
            # ``center_on_page`` centres on both axes in one arrangement
            # rather than one axis winning.
            base = result.get(nid, rect)
            result[nid] = base.model_copy(update={"y": (paper.height - base.h) / 2})

    if preset == "match_width":
        widest = max(rect.w for _, rect in chosen)
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"w": widest})

    elif preset == "match_height":
        tallest = max(rect.h for _, rect in chosen)
        for nid, rect in chosen:
            result[nid] = rect.model_copy(update={"h": tallest})

    return {nid: _clamp(rect, paper) for nid, rect in result.items()}


def _distribute(chosen: list[tuple[str, Rect]], *, axis: str) -> dict[str, Rect]:
    """Equal *gaps* between elements, outermost two left where they are.

    Gaps rather than equal centre spacing, which is the other plausible
    reading: with boxes of different sizes, even centres leave visibly uneven
    space between them, and space is what the eye actually reads.
    """
    key = (lambda r: r.x) if axis == "x" else (lambda r: r.y)
    size = (lambda r: r.w) if axis == "x" else (lambda r: r.h)

    order = sorted(chosen, key=lambda pair: key(pair[1]))
    first, last = order[0][1], order[-1][1]

    span = (key(last) + size(last)) - key(first)
    occupied = sum(size(rect) for _, rect in order)
    gap = (span - occupied) / (len(order) - 1)

    result: dict[str, Rect] = {}
    cursor = key(first)
    for nid, rect in order:
        result[nid] = rect.model_copy(update={axis: cursor})
        cursor += size(rect) + gap
    return result


def arrange(doc: StudioDoc, preset: str, nids: list[str]) -> list[SetGeometry]:
    """Compile an arrangement into ops, or explain why it cannot be one.

    Every failure is raised rather than returned so the caller can hand the
    model a sentence it can act on. A silent empty list would read to a model
    as success and it would tell the user the layout had changed.
    """
    if preset not in PRESETS:
        raise ArrangeError(
            f"{preset!r} is not an arrangement. Choose one of: "
            f"{', '.join(PRESETS)}."
        )

    unique = list(dict.fromkeys(nids))
    if not unique:
        raise ArrangeError("Name the elements to arrange.")

    missing = [nid for nid in unique if page_holding(doc, nid) is None]
    if missing:
        raise ArrangeError(
            f"Not placed elements on any page: {', '.join(missing)}. "
            "Arrange works on the boxes, images and shapes listed under LAYOUT."
        )

    # One page, because each sheet has its own coordinate space: "align these
    # left" across two pages has no meaning, and quietly picking one page's
    # edge would move things the user never mentioned.
    pages = {nid: page_holding(doc, nid) for nid in unique}
    distinct = {page.nid for page in pages.values() if page is not None}
    if len(distinct) > 1:
        raise ArrangeError(
            "Those elements are on different pages, and each page has its own "
            "coordinates. Arrange the ones on a single page at a time."
        )

    page = next(iter(pages.values()))
    assert page is not None

    if preset in NEEDS_THREE and len(unique) < 3:
        raise ArrangeError(
            "Distributing needs at least three elements -- with two there is "
            "nothing to even out between them."
        )
    if preset not in WORKS_ALONE and len(unique) < 2:
        raise ArrangeError(
            f"{preset} needs at least two elements to arrange against each "
            "other. To place one on the page, use center_on_page."
        )

    computed = compute(page, preset, unique)
    by_nid = {element.nid: element for element in page.elements}

    ops: list[SetGeometry] = []
    for nid, rect in computed.items():
        before = by_nid[nid].rect
        if _same(before, rect):
            # Nothing to say. An op per element regardless would make every
            # arrangement look like it moved everything in the change flash.
            continue
        ops.append(
            SetGeometry(
                nid=nid,
                x=rect.x,
                y=rect.y,
                w=rect.w,
                h=rect.h,
                # What it was measured from, so an arrangement computed against
                # a document the user has since dragged is caught rather than
                # silently overwriting them.
                expect={"x": before.x, "y": before.y, "w": before.w, "h": before.h},
            )
        )
    return ops


def _same(a: Rect, b: Rect) -> bool:
    return (
        abs(a.x - b.x) < 0.01
        and abs(a.y - b.y) < 0.01
        and abs(a.w - b.w) < 0.01
        and abs(a.h - b.h) < 0.01
    )
