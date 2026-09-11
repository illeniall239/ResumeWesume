"""A two-column résumé survives being edited.

``layout`` keeps a cursor per column; ``restack`` kept one for the whole page.
For a stack those are the same thing, and every test there was used a stack, so
nothing showed it. In a sidebar they are not: the rail got a ``y`` below the
entire main column, so Education and Skills were stacked *under* Experience
rather than beside it.

``restack`` runs after every batch, so the failure was not cosmetic and not
rare -- the first edit to a two-column document pulled it into one. Then the
browser measured the two-column render it was still drawing, sent the geometry
back, and this flattened it again: a loop that writes a version per round. A
document sitting open through it reached version 97.

The arrangement could only be chosen when a document was created, which is why
nobody had hit it. That is not a reason for it to stay broken -- ``layout`` has
accepted ``sidebar_left`` on a create request the whole time.
"""

from __future__ import annotations

import pytest

from studio.doc.autolayout import A4, layout, restack
from studio.doc.schema import (
    EducationNode,
    ExperienceNode,
    Layout,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

SLACK = 0.6


def a_resume(arrangement: Layout) -> StudioDoc:
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        summary=TextNode(nid="sum_aaaaa", text="Backend engineer."),
        experience=[
            ExperienceNode(
                nid="exp_aaaaa",
                title="Engineer",
                company="Northwind",
                bullets=[TextNode(nid="blt_aaaaa", text="Rebuilt the ledger.")],
            )
        ],
        education=[EducationNode(nid="edu_aaaaa", institution="Tessell", degree="BSc")],
        skills=[
            SkillGroup(nid="sgp_aaaaa", items=[SkillItem(nid="skl_aaaaa", text="Python")])
        ],
        layout=arrangement,
    )
    doc.pages = layout(doc)
    return doc


def placed(doc: StudioDoc) -> dict[str, tuple[float, float, float]]:
    return {
        element.ref: (element.rect.x, element.rect.y, element.rect.w)
        for page in doc.pages
        for element in page.elements
        if getattr(element, "ref", None)
    }


SIDEBARS = ("sidebar_left", "sidebar_right")


@pytest.mark.parametrize("arrangement", SIDEBARS)
class TestASidebarStaysTwoColumns:
    def test_a_restack_changes_nothing_it_did_not_have_to(
        self, arrangement: Layout
    ) -> None:
        # The strongest form of it: `layout` and `restack` agree, so a batch
        # that moves nothing leaves the page where it was.
        doc = a_resume(arrangement)
        before = placed(doc)

        restack(doc)

        assert placed(doc) == before

    def test_the_rail_starts_beside_the_main_column_not_below_it(
        self, arrangement: Layout
    ) -> None:
        doc = a_resume(arrangement)
        restack(doc)
        after = placed(doc)

        rail_top = min(after["education"][1], after["skills"][1])
        main_top = min(after["summary"][1], after["experience"][1])

        assert abs(rail_top - main_top) < SLACK, (
            "the rail was pushed below the main column"
        )

    def test_the_two_columns_do_not_overlap(self, arrangement: Layout) -> None:
        doc = a_resume(arrangement)
        restack(doc)
        after = placed(doc)

        rail_x, _, rail_w = after["education"]
        main_x, _, main_w = after["summary"]

        assert rail_x + rail_w <= main_x + SLACK or main_x + main_w <= rail_x + SLACK

    def test_the_header_still_spans_both(self, arrangement: Layout) -> None:
        # A name is the one thing on a résumé that is never in a column, and a
        # cursor per column has to leave room for something that is in neither.
        doc = a_resume(arrangement)
        restack(doc)
        after = placed(doc)

        assert after["personal"][2] == pytest.approx(A4.content_width, abs=SLACK)
        assert after["personal"][1] < min(after["summary"][1], after["education"][1])


class TestAStackIsUnchanged:
    def test_one_column_still_stacks(self) -> None:
        # The path every existing document is on. A stack is the degenerate
        # case of the same rule -- every frame is full width, so every frame
        # spans, and a cursor per column comes to one cursor.
        doc = a_resume("stack")
        before = placed(doc)

        restack(doc)

        assert placed(doc) == before
        assert len({x for x, _, _ in placed(doc).values()}) == 1
