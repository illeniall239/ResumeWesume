"""Where a section actually lands on the page.

Every existing `set_section` test asserted on the op's *mechanics* -- that it
inverts, that hiding also hides the frames, that reordering emits no style op.
Not one asked where the section ended up, and the only test that passed an
`order` used 9: an unused value, out of range, which is the one number that
cannot collide with anything. So the collision case had no coverage at all.

What that hid: `order` is an absolute integer, and "put Projects between
Education and Skills" has no integer to use when those two are already 2 and 3.
The schema refuses 2.5. Setting Projects to 3 is *accepted* -- it ties with
Skills, the tie breaks on declaration order, the page does not move, and
nothing reports a problem. The assistant then says the section moved.

Getting it right needed two calls renumbering both sections, and nothing told
the model that. So placement is relative now and the tool does the arithmetic.
"""

from __future__ import annotations

import pytest

from studio.agent.tools import REGISTRY, ToolError
from studio.doc.apply import OpContext, apply_ops
from studio.doc.autolayout import layout
from studio.doc.schema import (
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

#: Deliberately gapless: education 2, skills 3, projects 4. There is no integer
#: between education and skills, which is exactly the arrangement that used to
#: make "put it between them" a silent no-op.
GAPLESS = [
    SectionMeta(key="summary", label="Summary", order=0),
    SectionMeta(key="experience", label="Experience", order=1),
    SectionMeta(key="education", label="Education", order=2),
    SectionMeta(key="skills", label="Skills", order=3),
    SectionMeta(key="projects", label="Projects", order=4),
]


def a_resume() -> StudioDoc:
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        summary=TextNode(nid="sum_aaaaa", text="Ships things."),
        experience=[
            ExperienceNode(nid="exp_aaaaa", title="Engineer", company="Northwind")
        ],
        education=[EducationNode(nid="edu_aaaaa", institution="Tessell", degree="BSc")],
        projects=[ProjectNode(nid="prj_aaaaa", name="EDI.ai", role="Founder")],
        skills=[
            SkillGroup(nid="sgp_aaaaa", items=[SkillItem(nid="skl_aaaaa", text="Python")])
        ],
        sections=list(GAPLESS),
    )
    doc.pages = layout(doc)
    return doc


def drawn(doc: StudioDoc) -> list[str]:
    """The order a reader gets: frames down the pages, by page then ``rect.y``.

    Not the section metadata. The metadata is what the document *says*; this is
    what the canvas draws and what Chromium prints.
    """
    pages = {page.nid: index for index, page in enumerate(doc.pages)}
    placed = [
        (pages[page.nid], element.rect.y, element.ref)
        for page in doc.pages
        for element in page.elements
        if getattr(element, "ref", None)
    ]
    return [ref for _, _, ref in sorted(placed)]


def move(doc: StudioDoc, **args):
    spec = REGISTRY.get("set_section")
    return apply_ops(doc, spec.compile(spec.Args(**args), doc), AUTHORIZED)


class TestPlacingASection:
    def test_between_two_adjacent_sections(self) -> None:
        # The whole defect, in one line of intent.
        doc = a_resume()
        assert drawn(doc).index("skills") < drawn(doc).index("projects")

        after, _, rejected = move(doc, key="projects", after="education")

        assert rejected == []
        order = drawn(after)
        assert order.index("education") < order.index("projects") < order.index("skills")

    def test_before_reads_the_same_way(self) -> None:
        doc = a_resume()

        after, _, rejected = move(doc, key="projects", before="skills")

        assert rejected == []
        order = drawn(after)
        assert order.index("education") < order.index("projects") < order.index("skills")

    def test_it_moves_only_what_has_to_move(self) -> None:
        # Renumbering the whole table would put every untouched section in the
        # undo stack and the op log, which reads as the assistant having
        # rearranged the resume when it moved one thing.
        doc = a_resume()
        spec = REGISTRY.get("set_section")

        ops = spec.compile(spec.Args(key="projects", after="education"), doc)

        assert {op.key for op in ops} == {"projects", "skills"}

    def test_to_the_top(self) -> None:
        doc = a_resume()

        after, _, rejected = move(doc, key="projects", before="summary")

        assert rejected == []
        assert drawn(after).index("projects") < drawn(after).index("summary")

    def test_to_the_bottom(self) -> None:
        doc = a_resume()

        after, _, rejected = move(doc, key="summary", after="projects")

        assert rejected == []
        order = drawn(after)
        assert order.index("projects") < order.index("summary")

    def test_no_section_is_lost_or_duplicated(self) -> None:
        doc = a_resume()
        before = sorted(meta.key for meta in doc.sections)

        after, _, _ = move(doc, key="projects", after="education")

        assert sorted(meta.key for meta in after.sections) == before
        assert len({meta.order for meta in after.sections}) == len(after.sections)

    def test_an_absolute_index_still_cannot_collide(self) -> None:
        # `order` is still accepted for a caller that genuinely means an index,
        # and it is renumbered the same way -- so the tie that used to silently
        # do nothing is now unrepresentable through this tool.
        doc = a_resume()

        after, _, rejected = move(doc, key="projects", order=3)

        assert rejected == []
        assert len({meta.order for meta in after.sections}) == len(after.sections)
        order = drawn(after)
        assert order.index("education") < order.index("projects") < order.index("skills")

    def test_hiding_still_works_and_still_hides_the_frames(self) -> None:
        # The tool's other job, which must survive the rewrite.
        doc = a_resume()

        after, _, rejected = move(doc, key="experience", visible=False)

        assert rejected == []
        frames = [
            element
            for page in after.pages
            for element in page.elements
            if getattr(element, "ref", None) == "experience"
        ]
        assert frames and all(not frame.visible for frame in frames)

    def test_a_hidden_section_keeps_its_place(self) -> None:
        # Renumbering must count hidden sections too. Skipping them would move
        # one the instant it was shown again.
        doc = a_resume()
        doc, _, _ = move(doc, key="skills", visible=False)

        after, _, _ = move(doc, key="projects", after="education")

        by_key = {meta.key: meta.order for meta in after.sections}
        assert by_key["education"] < by_key["projects"] < by_key["skills"]

    def test_an_unknown_section_says_what_there_is(self) -> None:
        doc = a_resume()

        with pytest.raises(ToolError) as caught:
            move(doc, key="publications", after="education")

        assert "publications" in str(caught.value)
        assert "education" in str(caught.value)

    def test_an_unknown_anchor_says_what_there_is(self) -> None:
        doc = a_resume()

        with pytest.raises(ToolError) as caught:
            move(doc, key="projects", after="publications")

        assert "publications" in str(caught.value)

    def test_it_refuses_to_place_a_section_beside_itself(self) -> None:
        doc = a_resume()

        with pytest.raises(ToolError):
            move(doc, key="projects", after="projects")

    def test_before_and_after_together_read_as_between(self) -> None:
        # Not a mistake to refuse: "between Education and Skills" is how the
        # ask is phrased, and a model answers it with both. Refusing cost a
        # real placement -- the observed recovery was to drop both arguments,
        # and the section then landed at the foot of the resume.
        doc = a_resume()

        after, _, rejected = move(
            doc, key="projects", after="education", before="skills"
        )

        assert rejected == []
        order = drawn(after)
        assert order.index("education") < order.index("projects") < order.index("skills")
