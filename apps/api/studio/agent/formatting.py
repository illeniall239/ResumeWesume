"""How the résumé currently looks, in terms the assistant can act on.

A résumé is a visual document before it is a textual one. A recruiter sees the
shape of the page before a single word of it, and a sheet that runs two lines
onto a second page, or strands a heading at the bottom with its content
overleaf, reads as carelessness no matter how good the writing is. The
assistant could not see any of that: `_layout_preamble` tells it which ids sit
on which page, and nothing about whether the result is presentable.

**Still no coordinates**, for the reason `_layout_preamble` already gives: the
assistant has no tool that takes a position, and a model handed numbers it
cannot act on invents instructions the user cannot follow. What it gets here is
the *judgement* a person would make looking at the page -- "this spills two
lines onto page 2", "this heading is stranded" -- and every one of those has an
ordinary editing tool behind it: tighten a bullet, drop a weak line, shorten
the summary.

Each note names the problem and what would resolve it, because a note the
assistant cannot act on is noise, and noise in a system prompt is worse than
silence.
"""

from __future__ import annotations

from studio.doc.arrange import paper_of
from studio.doc.schema import PageNode, StudioDoc

#: The margin `render_pdf` passes to Chromium, and what `autolayout` places to.
_MARGIN = 28.35

#: Under this much of a sheet used, the page reads as a stray rather than a
#: page: a line or two of content adrift on otherwise blank paper. A résumé
#: that does this is the single most common formatting complaint there is.
_STRAY = 0.18

#: Over this, the sheet is effectively full and there is no room to add to it
#: without spilling. Not 1.0: a heading with nothing under it is not "fitting".
_FULL = 0.92

#: A heading this close to the bottom has nothing meaningful under it.
_STRANDED = 0.88


def _is_heading(ref: str) -> bool:
    """A section's own frame, as opposed to one of its entries or a stray box."""
    return not ref.startswith(("exp_", "edu_", "prj_", "txb_", "cst_"))


def _bottom(page: PageNode) -> float:
    return max((e.rect.y + e.rect.h for e in page.elements), default=_MARGIN)


def notes(doc: StudioDoc) -> list[str]:
    """Everything worth saying about the shape of this document, or nothing.

    Returns an empty list when the résumé is presentable, so a well-formatted
    document costs nothing in the prompt and says nothing to distract the model.
    """
    if not doc.pages:
        return []

    out: list[str] = []
    total = len(doc.pages)

    for number, page in enumerate(doc.pages, start=1):
        paper = paper_of(page)
        usable = paper.height - _MARGIN * 2
        if usable <= 0 or not page.elements:
            continue

        used = _bottom(page) - _MARGIN
        fill = used / usable

        spilling = [
            e.ref
            for e in page.elements
            if getattr(e, "ref", None) and e.rect.y + e.rect.h > _MARGIN + usable + 1
        ]
        if spilling:
            out.append(
                f"  page {number} overflows: {', '.join(spilling)} runs past the "
                "bottom of the sheet. Shorten the text above it."
            )

        # A last page holding almost nothing is the classic bad résumé: the
        # reader turns over for two lines. Worth naming every time.
        if number == total and total > 1 and fill < _STRAY:
            out.append(
                f"  page {number} holds only {fill:.0%} of a sheet. Tightening a "
                "few bullets earlier would pull it back onto page "
                f"{number - 1} and lose the spare page."
            )
        elif fill > _FULL:
            out.append(
                f"  page {number} is full ({fill:.0%}). Anything added here will "
                "spill onto a new page."
            )

        # A heading is a promise that something follows it. At the foot of a
        # sheet it promises the next page, which is exactly the thing a reader
        # skimming a résumé does not do.
        for element in page.elements:
            ref = getattr(element, "ref", None)
            if not ref or not _is_heading(ref):
                continue
            if (element.rect.y - _MARGIN) / usable < _STRANDED:
                continue
            follows = any(
                other.rect.y > element.rect.y
                for other in page.elements
                if getattr(other, "ref", None) and other.ref != ref
            )
            if not follows:
                out.append(
                    f"  the {ref} heading is stranded at the foot of page "
                    f"{number}, with its content overleaf. Shortening the text "
                    "above it would carry the heading over too."
                )

    if not out:
        return []
    return [
        f"FORMATTING ({total} page(s)) -- a résumé is judged on how it looks, "
        "so fix these when the edit you were asked for lets you:",
        *out,
    ]
