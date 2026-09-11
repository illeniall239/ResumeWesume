"""Whatever the request, the page comes back tidy.

Not a test for one bug. The rule is that after *any* batch the visible column
abuts: no frame drawn over another, and no space between two frames that
nothing is occupying. A résumé is judged on how it looks before a word of it is
read, so a page that is right in the model and janky on the sheet is simply
wrong.

The bugs this replaces were all the same shape, which is why it is written as a
property over many edits rather than an assertion about one:

  * `restack` positioned only the frames `layout` chose to emit. `layout`
    derives frames from *content*, so it skips a hidden or empty section --
    and those frames were left at whatever position they last had while
    everything else stacked around them. Hiding Awards drew Education straight
    on top of it.

  * `frames_bound_to` took a section key and a custom section's frame is bound
    by nid, so hiding one found no frames to hide and left it on the page
    drawing content the document had been told to stop showing. Silent: the
    caller got an empty list, which reads exactly like "there are none".

  * `restack` copied single-page coordinates onto a paginated document, so
    everything on page two was offset by the height of page one.

Each was found by a person looking at a sheet. This is the check that should
have found them first.
"""

from __future__ import annotations

import pytest

from studio.agent.tools import REGISTRY
from studio.doc.apply import OpContext, apply_ops
from studio.doc.autolayout import A4, layout
from studio.doc.ops import RemoveNode
from studio.doc.schema import (
    CustomSectionNode,
    EducationNode,
    ExperienceNode,
    PersonalInfo,
    ProjectNode,
    SectionMeta,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

AUTHORIZED = OpContext(granted_tiers={"A", "B", "C"})
BOTTOM = A4.margin + A4.content_height
#: Sub-point drift is float arithmetic, not a visible seam.
SLACK = 0.6


def a_resume() -> StudioDoc:
    """One of everything, so a mutation can reach any shape of section."""
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        summary=TextNode(nid="sum_aaaaa", text="Backend engineer."),
        experience=[
            ExperienceNode(
                nid="exp_aaaaa",
                title="Engineer",
                company="Northwind",
                bullets=[TextNode(nid="blt_aaaaa", text="Rebuilt the ledger.")],
            ),
            ExperienceNode(
                nid="exp_bbbbb",
                title="Analyst",
                company="Tessell",
                bullets=[TextNode(nid="blt_bbbbb", text="Built the dashboard.")],
            ),
        ],
        education=[EducationNode(nid="edu_aaaaa", institution="IBM", degree="BSc")],
        projects=[ProjectNode(nid="prj_aaaaa", name="EDI.ai", role="Founder")],
        skills=[
            SkillGroup(nid="sgp_aaaaa", items=[SkillItem(nid="skl_aaaaa", text="Python")])
        ],
        custom=[
            CustomSectionNode(
                nid="cst_award",
                key="awards",
                label="Awards",
                kind="stringList",
                strings=[SkillItem(nid="skl_bbbbb", text="Dean's List")],
            )
        ],
        sections=[
            SectionMeta(key="summary", label="Summary", order=0),
            SectionMeta(key="experience", label="Experience", order=1),
            SectionMeta(key="awards", label="Awards", order=2),
            SectionMeta(key="education", label="Education", order=3),
            SectionMeta(key="projects", label="Projects", order=4),
            SectionMeta(key="skills", label="Skills", order=5),
        ],
    )
    doc.pages = layout(doc)
    return doc


def tool(name: str, **args):
    """One edit, exactly as the assistant would issue it."""

    def run(doc: StudioDoc):
        spec = REGISTRY.get(name)
        return spec.compile(spec.Args(**args), doc)

    return run


def raw(*ops):
    return lambda doc: list(ops)


#: Every way a request can disturb the column.
EDITS = {
    "hide a custom section": tool("set_section", key="awards", visible=False),
    "hide a built-in section": tool("set_section", key="education", visible=False),
    "show a hidden section": tool("set_section", key="skills", visible=True),
    "move a section up": tool("set_section", key="skills", after="summary"),
    "move a section down": tool("set_section", key="summary", after="projects"),
    "move a custom section": tool("set_section", key="awards", before="summary"),
    "add a section": tool(
        "add_section", label="Publications", items=["A paper"], after="education"
    ),
    "add a section at the top": tool(
        "add_section", label="Volunteering", items=["A charity"], before="summary"
    ),
    "remove an entry": tool("remove_entry", nid="exp_bbbbb"),
    "remove the only entry of a section": tool("remove_entry", nid="edu_aaaaa"),
    "remove a project": tool("remove_entry", nid="prj_aaaaa"),
    "add a bullet": tool("add_bullet", parent="exp_aaaaa", value="Shipped it."),
    "remove a bullet": tool("remove_bullet", nid="blt_aaaaa"),
    "reorder entries": tool(
        "move_entry", nid="exp_bbbbb", section="experience", index=0
    ),
    "add a skill": tool("add_skill", skill="Rust", evidence="user_request"),
    "remove a skill": tool("remove_skill", skill="Python"),
    "rewrite the summary": tool(
        "rewrite_text", nid="sum_aaaaa", value="A much longer summary line."
    ),
    "delete a custom section's only item": raw(RemoveNode(nid="skl_bbbbb")),
}


def visible_flow(doc: StudioDoc):
    """What a reader actually sees, top to bottom, page by page."""
    for page in doc.pages:
        drawn = sorted(
            (
                element
                for element in page.elements
                if getattr(element, "ref", None) and element.visible
            ),
            key=lambda element: element.rect.y,
        )
        yield page, drawn


@pytest.mark.parametrize("name", sorted(EDITS))
class TestThePageIsTidyAfterAnyEdit:
    def _apply(self, name: str):
        doc = a_resume()
        ops = EDITS[name](doc)
        after, _, rejected = apply_ops(doc, ops, AUTHORIZED)
        assert rejected == [], f"{name}: {[r.message for r in rejected]}"
        return after

    def test_nothing_is_drawn_over_anything_else(self, name: str) -> None:
        after = self._apply(name)

        for page, drawn in visible_flow(after):
            for above, below in zip(drawn, drawn[1:]):
                assert above.rect.y + above.rect.h <= below.rect.y + SLACK, (
                    f"{name}: {above.ref} overlaps {below.ref}"
                )

    def test_there_is_no_space_nothing_is_using(self, name: str) -> None:
        after = self._apply(name)

        for page, drawn in visible_flow(after):
            for above, below in zip(drawn, drawn[1:]):
                gap = below.rect.y - (above.rect.y + above.rect.h)
                assert gap <= SLACK, (
                    f"{name}: {gap:.1f}pt of nothing between "
                    f"{above.ref} and {below.ref}"
                )

    def test_the_column_starts_at_the_top_of_its_page(self, name: str) -> None:
        after = self._apply(name)

        for page, drawn in visible_flow(after):
            if drawn:
                assert abs(drawn[0].rect.y - A4.margin) < SLACK, (
                    f"{name}: page starts at {drawn[0].rect.y:.1f}"
                )

    def test_nothing_is_left_off_the_sheet(self, name: str) -> None:
        after = self._apply(name)

        for page, drawn in visible_flow(after):
            for element in drawn:
                if element.rect.h > A4.content_height:
                    continue  # taller than a page: left to overflow, not split
                assert element.rect.y + element.rect.h <= BOTTOM + SLACK, (
                    f"{name}: {element.ref} runs off the page"
                )

    def test_a_hidden_section_is_not_drawn(self, name: str) -> None:
        # Tidy is not the same as right. With the stacking fixed, a frame that
        # was never hidden simply reserves its space and the column still abuts
        # -- so the page looks correct while showing a section the document was
        # told to stop showing. `frames_bound_to` took a section key and a
        # custom section's frame is bound by nid, so hiding one found nothing
        # to hide and answered with an empty list, which reads exactly like
        # "there are none".
        after = self._apply(name)
        hidden = {meta.key for meta in after.sections if not meta.visible}
        if not hidden:
            pytest.skip("this edit hides nothing")

        by_key = {section.nid: section.key for section in after.custom}
        for _, drawn in visible_flow(after):
            for element in drawn:
                key = by_key.get(element.ref, element.ref)
                assert key not in hidden, f"{name}: {key} is hidden but still drawn"

    def test_the_reading_order_matches_the_section_order(self, name: str) -> None:
        # The page must agree with the document, not merely be tidy: a neat
        # column in the wrong order is still the wrong résumé.
        from studio.doc.autolayout import reading_order

        after = self._apply(name)
        rank = {ref: index for index, ref in enumerate(reading_order(after))}

        seen = [
            rank[element.ref]
            for _, drawn in visible_flow(after)
            for element in drawn
            if element.ref in rank
        ]
        assert seen == sorted(seen), f"{name}: drawn out of order"
