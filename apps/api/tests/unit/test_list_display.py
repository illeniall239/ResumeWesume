"""A run of short items can be asked to stack.

Reported as a gap, and it was one: asked to show Technical Skills as bullets,
the assistant reached for ``set_bullet_style`` on a skill, was told
``skl_rhge3 is not a bullet``, and concluded the product could not do it. Both
halves of that were true and neither was useful.

A skill is not a bullet -- the *run* is a line or a list, no item has a shape
of its own -- so the rejection was right. But the shape of the run was decided
by looking at the words: a group whose items carried commas or ran long was
stacked, everything else was joined into a line. That is a good default and it
was the only rule, so the shape of someone's résumé was a property of how long
their skills happened to be, and the one way to get a list was to write an item
long enough to trip the test.

The shape is now something the document says and the page obeys, ``auto`` is
the old rule under its own name, and the dead-end rejection names the tool that
does the job.
"""

from __future__ import annotations

import pytest

from studio.agent.context import outline
from studio.agent.tools import REGISTRY
from studio.doc.apply import OpContext, apply_ops, tier_of
from studio.doc.index import NodeIndex
from studio.doc.ops import SetStyle
from studio.doc.schema import (
    CustomSectionNode,
    PersonalInfo,
    SectionMeta,
    SkillGroup,
    SkillItem,
    StudioDoc,
)

#: What the assistant is granted. Deliberately not Tier B or C: the point of
#: half these assertions is that setting the shape of a list needs neither.
COSMETIC = OpContext(granted_tiers={"A"})


def a_resume() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        skills=[
            SkillGroup(
                nid="sgp_aaaaa",
                key="technical",
                label="Technical Skills",
                items=[
                    SkillItem(nid="skl_aaaaa", text="Python"),
                    SkillItem(nid="skl_bbbbb", text="Go"),
                ],
            )
        ],
        custom=[
            CustomSectionNode(
                nid="cst_aaaaa",
                key="certifications",
                label="Certifications",
                kind="stringList",
                strings=[SkillItem(nid="skl_ccccc", text="IBM Data Analysis")],
            )
        ],
        sections=[
            SectionMeta(key="skills", label="Skills", order=0),
            SectionMeta(key="certifications", label="Certifications", order=1),
        ],
    )


def run(doc: StudioDoc, **args):
    spec = REGISTRY.get("set_list_display")
    return apply_ops(doc, spec.compile(spec.Args(**args), doc), COSMETIC)


class TestTheShapeIsSomethingTheDocumentSays:
    def test_a_document_that_says_nothing_still_says_nothing(self) -> None:
        # `auto` is the default so no stored résumé changes shape on upgrade.
        doc = a_resume()

        assert doc.skills[0].display == "auto"
        assert doc.custom[0].display == "auto"

    def test_a_skills_group_can_be_asked_to_stack(self) -> None:
        after, _, rejected = run(a_resume(), nid="sgp_aaaaa", display="list")

        assert rejected == []
        assert after.skills[0].display == "list"

    def test_it_can_be_put_back(self) -> None:
        after, _, _ = run(a_resume(), nid="sgp_aaaaa", display="list")
        after, _, rejected = run(after, nid="sgp_aaaaa", display="inline")

        assert rejected == []
        assert after.skills[0].display == "inline"

    def test_a_certifications_section_is_the_same_widget(self) -> None:
        after, _, rejected = run(a_resume(), nid="cst_aaaaa", display="list")

        assert rejected == []
        assert after.custom[0].display == "list"

    def test_nonsense_is_read_as_auto(self) -> None:
        # A value from an older document, or a model's invention, must not make
        # a résumé fail to load.
        doc = StudioDoc.model_validate(
            {**a_resume().model_dump(), "skills": [
                {"nid": "sgp_aaaaa", "key": "technical", "label": "T",
                 "items": [], "display": "columns"}
            ]}
        )

        assert doc.skills[0].display == "auto"


class TestItIsAimedAtWhatTheModelCanSee:
    def test_naming_a_skill_sets_its_group(self) -> None:
        # What an outline shows for a skill is the item. Rejecting a call that
        # names one would be a second dead end in the same place.
        after, _, rejected = run(a_resume(), nid="skl_aaaaa", display="list")

        assert rejected == []
        assert after.skills[0].display == "list"

    def test_naming_a_line_sets_its_section(self) -> None:
        after, _, rejected = run(a_resume(), nid="skl_ccccc", display="list")

        assert rejected == []
        assert after.custom[0].display == "list"

    def test_an_unknown_address_is_still_reported(self) -> None:
        # Guessing an owner would turn a typo into a silent edit elsewhere.
        _, _, rejected = run(a_resume(), nid="sgp_zzzzz", display="list")

        assert rejected and "sgp_zzzzz" in rejected[0].message


class TestTheDeadEndPointsSomewhere:
    def test_styling_a_skill_names_the_tool_and_the_group(self) -> None:
        doc = a_resume()

        _, _, rejected = apply_ops(
            doc, [SetStyle(nid="skl_aaaaa", style="bullet")], COSMETIC
        )

        assert rejected
        message = rejected[0].message
        assert "set_list_display" in message
        assert "sgp_aaaaa" in message

    def test_a_real_bullet_is_untouched_by_that(self) -> None:
        from studio.doc.schema import ExperienceNode, TextNode

        doc = a_resume()
        doc.experience = [
            ExperienceNode(
                nid="exp_aaaaa",
                title="Engineer",
                bullets=[TextNode(nid="blt_aaaaa", text="Shipped it.")],
            )
        ]

        after, _, rejected = apply_ops(
            doc, [SetStyle(nid="blt_aaaaa", style="plain")], COSMETIC
        )

        assert rejected == []
        assert after.experience[0].bullets[0].style == "plain"


class TestHowItIsSetIsNotAClaim:
    @pytest.mark.parametrize("field", ["display", "label"])
    def test_presentation_needs_no_consent(self, field: str) -> None:
        # The same reasoning the file already applies to a section heading:
        # renaming a group, or listing it as bullets, changes how the résumé is
        # set and asserts nothing about the person. Both fell through to the
        # catch-all and were gated as claims, and a cosmetic change that asks
        # for consent teaches the user to grant it without reading.
        from studio.doc.ops import SetField

        doc = a_resume()
        op = SetField(target=f"sgp_aaaaa.{field}", value="list")

        assert tier_of(op, NodeIndex(doc)) == "A"

    def test_an_employer_is_still_a_claim(self) -> None:
        from studio.doc.ops import SetField
        from studio.doc.schema import ExperienceNode

        doc = a_resume()
        doc.experience = [ExperienceNode(nid="exp_aaaaa", company="Northwind")]
        op = SetField(target="exp_aaaaa.company", value="Initech")

        assert tier_of(op, NodeIndex(doc)) == "C"


class TestTheAssistantCanSeeTheShape:
    def test_the_outline_says_how_a_group_is_set(self) -> None:
        # Not knowing was half the bug: the assistant could not have reported
        # the current shape, let alone that it was changeable.
        doc = a_resume()
        doc.skills[0].display = "list"

        assert "bulleted list" in outline(doc)

    def test_and_says_so_when_the_page_decides(self) -> None:
        assert "whichever fits" in outline(a_resume())
