"""A new section belongs where the section order says, not at the bottom.

A section nothing on the page draws yet arrives with its own frame, minted by
``cover_for`` and placed at the foot of the last page -- which is the only
honest thing to say at the moment it is minted, and is not where the section
goes. The document already declares an order and ``layout`` already reads it.

The bug this pins: asked for "a Projects section between Education and Skills",
the assistant added both projects, the section metadata said Projects came
third, and the sheet drew it last, under Skills, at the very bottom of the
resume. The assistant then issued a `set_section` to move it, which worked --
so the page eventually corrected itself, one turn later, for a request that
should never have needed two.
"""

from __future__ import annotations

from studio.agent.tools import cover_for
from studio.doc.apply import OpContext, apply_ops
from studio.doc.autolayout import layout
from studio.doc.ops import InsertNode
from studio.doc.nodes import NodeKind, mint
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

#: Tier C is "add a claim about this person", which is exactly what adding a
#: project is. These tests are about where the frame lands, not about consent,
#: so the batch is granted it outright.
AUTHORIZED = OpContext(granted_tiers={"A", "B", "C"})

#: Projects sits third, between education and skills -- the arrangement the
#: request in the bug report asked for.
SECTIONS = [
    SectionMeta(key="summary", label="Summary", order=0),
    SectionMeta(key="experience", label="Experience", order=1),
    SectionMeta(key="education", label="Education", order=2),
    SectionMeta(key="projects", label="Projects", order=3),
    SectionMeta(key="skills", label="Skills", order=4),
]


def a_resume_without_projects() -> StudioDoc:
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        summary=TextNode(nid="sum_aaaaa", text="Ships things."),
        experience=[ExperienceNode(nid="exp_aaaaa", title="Engineer", company="Northwind")],
        education=[EducationNode(nid="edu_aaaaa", institution="Tessell", degree="BSc")],
        skills=[
            SkillGroup(nid="sgp_aaaaa", items=[SkillItem(nid="skl_aaaaa", text="Python")])
        ],
        sections=SECTIONS,
    )
    doc.pages = layout(doc)
    return doc


def rendered_order(doc: StudioDoc) -> list[str]:
    """The order a reader gets: frames down the pages, by page then ``rect.y``.

    This is what the canvas draws and what Chromium prints, so it is the only
    order that can be called the document's real one.
    """
    pages = {page.nid: index for index, page in enumerate(doc.pages)}
    placed = [
        (pages[page.nid], element.rect.y, element.ref)
        for page in doc.pages
        for element in page.elements
        if getattr(element, "ref", None)
    ]
    return [ref for _, _, ref in sorted(placed)]


def adding_a_project(doc: StudioDoc) -> list:
    """What `add_project` compiles to for the first project on a resume."""
    return [
        InsertNode(
            parent="projects",
            index=0,
            node={
                "nid": mint(NodeKind.PROJECT),
                "name": "EDI.ai",
                "role": "Founder",
                "bullets": [{"nid": mint(NodeKind.BULLET), "text": "Built EDI."}],
            },
        ),
        *cover_for(doc, "projects"),
    ]


class TestTheFirstProject:
    def test_the_section_is_drawn_where_the_order_says(self) -> None:
        # The whole defect. Every other assertion passed while the sheet, and
        # the exported PDF, drew Projects underneath Skills.
        doc = a_resume_without_projects()

        after, _, rejected = apply_ops(doc, adding_a_project(doc), AUTHORIZED)

        assert rejected == []
        order = rendered_order(after)
        assert order.index("education") < order.index("projects")
        assert order.index("projects") < order.index("skills")

    def test_the_section_below_it_is_pushed_down(self) -> None:
        # The other half of the same move: Projects can only take the slot if
        # Skills gives it up. Left where it was, the two would be drawn on top
        # of each other.
        doc = a_resume_without_projects()
        before = next(
            element.rect.y
            for page in doc.pages
            for element in page.elements
            if getattr(element, "ref", None) == "skills"
        )

        after, _, _ = apply_ops(doc, adding_a_project(doc), AUTHORIZED)

        moved = next(
            element.rect.y
            for page in after.pages
            for element in page.elements
            if getattr(element, "ref", None) == "skills"
        )
        assert moved > before

    def test_a_hand_placed_box_is_left_alone(self) -> None:
        # The re-stack now runs on an insert, so the guarantee that it never
        # touches something somebody positioned themselves has to hold here
        # too. A text box is not part of the flow and never was.
        doc = a_resume_without_projects()
        # A text box is a block of its own plus the frame that draws it. Both,
        # or the coverage gate rightly refuses a frame rendering nothing.
        block = mint(NodeKind.BLOCK)
        doc, _, rejected = apply_ops(
            doc,
            [
                InsertNode(
                    parent="blocks",
                    index=-1,
                    node={
                        "nid": block,
                        "lines": [{"nid": mint(NodeKind.BULLET), "text": "A note."}],
                    },
                ),
                InsertNode(
                    parent=doc.pages[0].nid,
                    index=-1,
                    node={
                        "nid": mint(NodeKind.FRAME),
                        "ref": block,
                        "rect": {"x": 33.0, "y": 640.0, "w": 120.0, "h": 40.0},
                        "rotation": 0.0,
                        "autogrow": "none",
                        "pinned": True,
                    },
                ),
            ],
            AUTHORIZED,
        )
        assert rejected == []

        after, _, _ = apply_ops(doc, adding_a_project(doc), AUTHORIZED)

        placed = next(
            element
            for node in after.pages
            for element in node.elements
            if getattr(element, "ref", None) == block
        )
        assert (placed.rect.x, placed.rect.y) == (33.0, 640.0)
