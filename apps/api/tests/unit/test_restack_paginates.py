"""A re-stack must not hand page two a page-one coordinate.

``layout`` returns a single page with a continuous ``y``, because paginating
needs measured text and the server has none -- the browser measures and
corrects. ``restack`` copied those numbers onto frames that were still assigned
to their original pages, which is correct only while the resume fits on one
sheet.

On two, everything on page two was offset by the entire height of page one: the
first frame landed near the foot of the sheet and the rest ran off it.

    content area 28 .. 813
    page 2 before   28, 178, 328, 350, 438
    page 2 after   736, 886, 1036, 1058, 1146

It reached the exported PDF as well as the screen, because the print route
renders the stored document. The browser repaginated on the next version, which
is why dragging anything appeared to fix the page -- and why the frame that was
dragged stayed wrong, being pinned and so skipped by the measure pass.
"""

from __future__ import annotations

from studio.doc.autolayout import A4, layout, restack
from studio.doc.schema import (
    EducationNode,
    ExperienceNode,
    PageNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
    DEFAULT_SECTIONS,
)

BOTTOM = A4.margin + A4.content_height


def two_pages() -> StudioDoc:
    """A resume long enough to need a second sheet, paginated as the browser does."""
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        summary=TextNode(nid="sum_aaaaa", text="Ships things."),
        experience=[
            ExperienceNode(
                nid=f"exp_{i:05d}",
                title=f"Role {i}",
                company=f"Company {i}",
                bullets=[TextNode(nid=f"blt_{i}{j}aaa", text="x" * 90) for j in range(4)],
            )
            for i in range(6)
        ],
        education=[EducationNode(nid="edu_aaaaa", institution="Tessell", degree="BSc")],
        skills=[
            SkillGroup(nid="sgp_aaaaa", items=[SkillItem(nid="skl_aaaaa", text="Python")])
        ],
        sections=list(DEFAULT_SECTIONS),
    )
    doc.pages = layout(doc)
    # What the browser measured: an entry with four bullets is about 150pt.
    for element in doc.pages[0].elements:
        if element.ref.startswith("exp_"):
            element.rect.h = 150.0

    sheets: list[list] = [[]]
    cursor = A4.margin
    for element in list(doc.pages[0].elements):
        if cursor + element.rect.h > BOTTOM and sheets[-1]:
            sheets.append([])
            cursor = A4.margin
        element.rect.y = cursor
        cursor += element.rect.h
        sheets[-1].append(element)

    doc.pages = [
        PageNode(nid=f"pag_{i:05d}", size="A4", elements=els)
        for i, els in enumerate(sheets)
    ]
    return doc


def rows(doc: StudioDoc) -> list[list[float]]:
    return [[element.rect.y for element in page.elements] for page in doc.pages]


class TestRestackingATwoPageResume:
    def test_it_needs_two_pages_to_be_a_test_at_all(self) -> None:
        assert len(two_pages().pages) >= 2

    def test_nothing_is_left_below_the_bottom_of_its_page(self) -> None:
        # The defect, stated as what a reader would see: content off the sheet.
        doc = two_pages()

        restack(doc)

        stranded = [
            (element.ref, element.rect.y)
            for page in doc.pages
            for element in page.elements
            if element.rect.y > BOTTOM
        ]
        assert stranded == []

    def test_every_page_starts_at_the_top_margin(self) -> None:
        doc = two_pages()

        restack(doc)

        for page in doc.pages:
            if page.elements:
                assert abs(page.elements[0].rect.y - A4.margin) < 0.6

    def test_the_reading_order_is_unchanged(self) -> None:
        doc = two_pages()
        before = [element.ref for page in doc.pages for element in page.elements]

        restack(doc)

        after = [element.ref for page in doc.pages for element in page.elements]
        assert after == before

    def test_frames_still_abut_within_a_page(self) -> None:
        doc = two_pages()

        restack(doc)

        for page in doc.pages:
            placed = sorted((e.rect.y, e.rect.h) for e in page.elements)
            for (top, height), (next_top, _) in zip(placed, placed[1:]):
                assert next_top >= top + height - 0.6

    def test_a_one_page_resume_is_unaffected(self) -> None:
        # The path that already worked must keep working, byte for byte.
        doc = StudioDoc(
            personal=PersonalInfo(name="Alex Morgan"),
            summary=TextNode(nid="sum_aaaaa", text="Ships things."),
            experience=[ExperienceNode(nid="exp_aaaaa", title="Engineer", company="N")],
            sections=list(DEFAULT_SECTIONS),
        )
        doc.pages = layout(doc)
        before = rows(doc)

        restack(doc)

        assert rows(doc) == before
        assert len(doc.pages) == 1

    def test_a_pinned_frame_keeps_the_position_it_was_dragged_to(self) -> None:
        # The other half of what the user saw: a dragged frame is pinned, the
        # browser's measure skips it, and the server must agree -- otherwise the
        # two fight over it on every turn.
        doc = two_pages()
        target = doc.pages[1].elements[0]
        target.pinned = True
        where = target.rect.y

        restack(doc)

        assert target.rect.y == where

    def test_a_hand_placed_box_stays_on_its_page(self) -> None:
        doc = two_pages()
        page = doc.pages[1]
        # `deep=True`: a shallow copy shares the original's Rect, so moving
        # the frame it was cloned from would move this one too and the test
        # would be asserting on the same object twice.
        loose = page.elements[0].model_copy(
            update={"nid": "frm_loose", "ref": "txb_note", "pinned": True}, deep=True
        )
        loose.rect.y = 500.0
        page.elements.append(loose)

        restack(doc)

        still = [e for e in doc.pages[1].elements if e.nid == "frm_loose"]
        assert still and still[0].rect.y == 500.0


class TestABrokenColumnIsNeverStored:
    """No request may leave the résumé in a state a reader must not be shown.

    The re-stack used to run only for the ops we *knew* disturbed the flow, and
    a list like that is only as good as the last person to remember it: a new
    tool, or an old one used a new way, breaks the page and nothing notices.
    The engine now checks the result instead, so whatever op produced it, a
    frame off the sheet or two frames printed over each other is repaired
    before it can be stored -- and the exported PDF renders the stored
    document, so this is the difference between a clean file and a ruined one.
    """

    def test_overlapping_frames_are_repaired(self) -> None:
        from studio.doc.apply import OpContext, apply_ops
        from studio.doc.ops import SetGeometry

        doc = two_pages()
        victim = doc.pages[0].elements[2]
        neighbour = doc.pages[0].elements[3]

        result, _, rejected = apply_ops(
            doc,
            [SetGeometry(nid=victim.nid, y=neighbour.rect.y)],
            OpContext(granted_tiers={"A", "B", "C"}),
        )

        assert rejected == []
        for page in result.pages:
            ordered = sorted(page.elements, key=lambda e: e.rect.y)
            for above, below in zip(ordered, ordered[1:]):
                assert above.rect.y + above.rect.h <= below.rect.y + 1.0

    def test_a_frame_pushed_off_the_sheet_is_repaired(self) -> None:
        from studio.doc.apply import OpContext, apply_ops
        from studio.doc.ops import SetGeometry

        doc = two_pages()
        stray = doc.pages[1].elements[0]

        result, _, rejected = apply_ops(
            doc,
            [SetGeometry(nid=stray.nid, y=BOTTOM + 400.0)],
            OpContext(granted_tiers={"A", "B", "C"}),
        )

        assert rejected == []
        assert all(
            element.rect.y + element.rect.h <= BOTTOM + 1.0
            for page in result.pages
            for element in page.elements
        )

    def test_a_tidy_document_is_left_alone(self) -> None:
        # The check is a repair, not a policy. A well-formed column does not
        # satisfy it, so nothing straightens geometry somebody positioned.
        from studio.doc.apply import OpContext, apply_ops
        from studio.doc.ops import SetText

        doc = two_pages()
        before = [
            (e.nid, e.rect.y) for page in doc.pages for e in page.elements
        ]
        bullet = doc.experience[0].bullets[0].nid

        result, _, rejected = apply_ops(
            doc,
            [SetText(nid=bullet, value="Rewritten.")],
            OpContext(granted_tiers={"A", "B", "C"}),
        )

        assert rejected == []
        assert [
            (e.nid, e.rect.y) for page in result.pages for e in page.elements
        ] == before
