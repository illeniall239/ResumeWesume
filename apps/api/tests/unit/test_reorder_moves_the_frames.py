"""Reordering entries has to move them on the page, not just in the model.

A résumé's entries are laid out as one frame each, stacked down the sheet, and
what a reader sees -- on screen and in the exported PDF -- is that stack, in
`rect.y` order. Nothing re-derives those positions after a batch of ops, so a
reorder rewrote the list and left every frame exactly where it was: the
document said one order and the page showed the other.

It surfaced on the feature it hurts most. Tailoring against a job posting forks
a copy and re-angles it, and the reordering is the substance of that -- putting
the work the posting asks about first. The assistant reported the new order,
the stored document agreed with it, and the PDF that goes to the employer kept
the old one. Silent, and wrong on the only output that leaves the machine.
"""

from __future__ import annotations

from studio.doc.apply import apply_ops
from studio.doc.autolayout import layout
from studio.doc.ops import Reorder
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    ExperienceNode,
    PersonalInfo,
    StudioDoc,
    TextNode,
)


def a_resume() -> StudioDoc:
    """Three jobs, laid out the way every document in the app is."""
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        experience=[
            ExperienceNode(
                nid="exp_aaaaa", title="Senior", company="Northwind",
                bullets=[TextNode(nid="blt_aaaaa", text="Rebuilt the ledger.")],
            ),
            ExperienceNode(nid="exp_bbbbb", title="Engineer", company="Fathom"),
            ExperienceNode(nid="exp_ccccc", title="Analyst", company="Tessell"),
        ],
        sections=list(DEFAULT_SECTIONS),
    )
    doc.pages = layout(doc)
    return doc


def companies(doc: StudioDoc) -> list[str]:
    return [entry.company for entry in doc.experience]


def rendered_order(doc: StudioDoc) -> list[str]:
    """The order a reader actually gets: frames down the page, by `rect.y`.

    This is what the canvas draws and what Chromium prints, so it is the only
    order that can be called the document's real one.
    """
    by_nid = {entry.nid: entry.company for entry in doc.experience}
    placed = [
        (element.rect.y, by_nid[element.ref])
        for page in doc.pages
        for element in page.elements
        if getattr(element, "ref", None) in by_nid
    ]
    return [name for _, name in sorted(placed)]


class TestAReorder:
    def test_moves_the_entries_in_the_model(self) -> None:
        doc = a_resume()
        order = [doc.experience[2].nid, doc.experience[0].nid, doc.experience[1].nid]

        after, _, rejected = apply_ops(doc, [Reorder(parent="experience", order=order)])

        assert rejected == []
        assert companies(after) == ["Tessell", "Northwind", "Fathom"]

    def test_moves_them_on_the_page_too(self) -> None:
        # The whole defect. Every assertion above passed while the rendered
        # resume and the exported PDF still read Northwind, Fathom, Tessell.
        doc = a_resume()
        order = [doc.experience[2].nid, doc.experience[0].nid, doc.experience[1].nid]

        after, _, _ = apply_ops(doc, [Reorder(parent="experience", order=order)])

        assert rendered_order(after) == ["Tessell", "Northwind", "Fathom"]

    def test_keeps_the_heights_the_client_measured(self) -> None:
        # The server cannot measure text, so the browser corrects every frame's
        # height on first open and those numbers are the accurate ones. A
        # reorder that re-ran the estimating layout would throw them away and
        # the page would jump on every move -- so the fix re-stacks, it does
        # not re-lay-out.
        doc = a_resume()
        measured = {}
        for page in doc.pages:
            for i, element in enumerate(page.elements):
                if getattr(element, "ref", None):
                    element.rect.h = 40.0 + i * 11.0
                    measured[element.ref] = element.rect.h

        order = [doc.experience[2].nid, doc.experience[0].nid, doc.experience[1].nid]
        after, _, _ = apply_ops(doc, [Reorder(parent="experience", order=order)])

        kept = {
            element.ref: element.rect.h
            for page in after.pages
            for element in page.elements
            if getattr(element, "ref", None)
        }
        assert kept == measured

    def test_leaves_frames_stacked_without_gaps_or_overlaps(self) -> None:
        # Entries have different heights, so the slots cannot simply be swapped
        # between them: each one has to start where the last one ended, or the
        # page shows two jobs on top of each other.
        doc = a_resume()
        for page in doc.pages:
            for i, element in enumerate(page.elements):
                if getattr(element, "ref", None):
                    element.rect.h = 30.0 + i * 17.0

        order = [doc.experience[2].nid, doc.experience[1].nid, doc.experience[0].nid]
        after, _, _ = apply_ops(doc, [Reorder(parent="experience", order=order)])

        placed = sorted(
            (
                (element.rect.y, element.rect.h)
                for page in after.pages
                for element in page.elements
                if getattr(element, "ref", None)
            )
        )
        for (top, height), (next_top, _) in zip(placed, placed[1:]):
            assert abs(top + height - next_top) < 0.51, "frames must abut exactly"

    def test_does_not_disturb_a_document_nobody_reordered(self) -> None:
        # A re-stack that runs on every batch would quietly rewrite geometry
        # somebody had dragged by hand, so it only runs when an op actually
        # changed the order.
        doc = a_resume()
        before = [
            (element.ref, element.rect.y)
            for page in doc.pages
            for element in page.elements
            if getattr(element, "ref", None)
        ]

        from studio.doc.ops import SetText

        after, _, rejected = apply_ops(
            doc, [SetText(nid="blt_aaaaa", value="Rebuilt the payments ledger.")]
        )

        assert rejected == []
        assert [
            (element.ref, element.rect.y)
            for page in after.pages
            for element in page.elements
            if getattr(element, "ref", None)
        ] == before
