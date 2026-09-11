"""The assistant is told how the page looks, not just what is on it.

A résumé is a visual document before it is a textual one: a reader takes in the
shape of the sheet before a word of it, and two lines spilling onto a second
page reads as carelessness however good the writing is. The outline told the
assistant which ids sat on which page and nothing about whether the result was
presentable, so it could not see the one thing the document is judged on.

No coordinates, for the reason `_layout_preamble` already gives -- there is no
tool that takes a position. These are the judgements a person makes looking at
the page, each with an ordinary editing tool behind it.
"""

from __future__ import annotations

from studio.agent.formatting import notes
from studio.agent.context import outline
from studio.doc.autolayout import layout
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    EducationNode,
    ExperienceNode,
    PageNode,
    PersonalInfo,
    Rect,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

MARGIN = 28.35
USABLE = 841.89 - MARGIN * 2


def a_resume() -> StudioDoc:
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        summary=TextNode(nid="sum_aaaaa", text="Ships things."),
        experience=[ExperienceNode(nid="exp_aaaaa", title="Engineer", company="N")],
        education=[EducationNode(nid="edu_aaaaa", institution="T", degree="BSc")],
        skills=[
            SkillGroup(nid="sgp_aaaaa", items=[SkillItem(nid="skl_aaaaa", text="Python")])
        ],
        sections=list(DEFAULT_SECTIONS),
    )
    doc.pages = layout(doc)
    return doc


def sheet(nid: str, frames: list[tuple[str, float, float]]) -> PageNode:
    """A page holding frames given as (ref, y, height)."""
    page = PageNode(nid=nid, size="A4", orientation="portrait", elements=[])
    template = a_resume().pages[0].elements[0]
    for ref, y, height in frames:
        element = template.model_copy(
            update={"nid": f"frm_{ref[:5]}{int(y)}", "ref": ref}, deep=True
        )
        element.rect = Rect(x=MARGIN, y=y, w=100.0, h=height)
        page.elements.append(element)
    return page


class TestWhenThereIsNothingToSay:
    def test_a_tidy_one_pager_says_nothing(self) -> None:
        # Silence is the point: a well-formatted résumé must cost nothing in
        # the prompt, and a note nobody needs is noise in a system prompt.
        assert notes(a_resume()) == []

    def test_an_empty_document_says_nothing(self) -> None:
        assert notes(StudioDoc(personal=PersonalInfo(name="A"))) == []


class TestTheStrayLastPage:
    """Two lines overleaf: the commonest complaint a résumé attracts."""

    def test_it_is_named(self) -> None:
        doc = a_resume()
        doc.pages = [
            sheet("pag_00001", [("experience", MARGIN, USABLE - 10)]),
            sheet("pag_00002", [("skills", MARGIN, 30.0)]),
        ]

        said = " ".join(notes(doc))

        assert "page 2" in said
        assert "spare page" in said

    def test_a_second_page_that_is_genuinely_used_is_not_flagged(self) -> None:
        doc = a_resume()
        doc.pages = [
            sheet("pag_00001", [("experience", MARGIN, USABLE - 10)]),
            sheet("pag_00002", [("skills", MARGIN, USABLE * 0.6)]),
        ]

        assert not any("spare page" in line for line in notes(doc))


class TestOverflow:
    def test_content_past_the_bottom_is_named(self) -> None:
        doc = a_resume()
        doc.pages = [sheet("pag_00001", [("experience", MARGIN, USABLE + 60)])]

        said = " ".join(notes(doc))

        assert "overflows" in said
        assert "experience" in said


class TestTheStrandedHeading:
    """A heading promises something follows it. At the foot of a page it
    promises the next one, which a skimming reader never turns to."""

    def test_a_heading_alone_at_the_foot_is_named(self) -> None:
        doc = a_resume()
        doc.pages = [
            sheet(
                "pag_00001",
                [("experience", MARGIN, USABLE * 0.9), ("skills", MARGIN + USABLE * 0.93, 20.0)],
            ),
            sheet("pag_00002", [("skl_aaaaa", MARGIN, 200.0)]),
        ]

        said = " ".join(notes(doc))

        assert "stranded" in said
        assert "skills" in said

    def test_a_heading_with_its_content_under_it_is_fine(self) -> None:
        doc = a_resume()
        doc.pages = [
            sheet(
                "pag_00001",
                [
                    ("experience", MARGIN, 200.0),
                    ("skills", MARGIN + 200.0, 20.0),
                    ("skl_aaaaa", MARGIN + 220.0, 60.0),
                ],
            )
        ]

        assert not any("stranded" in line for line in notes(doc))

    def test_an_entry_at_the_foot_is_not_a_stranded_heading(self) -> None:
        # An entry is content, not a promise. Only a section's own frame can
        # strand.
        doc = a_resume()
        doc.pages = [
            sheet("pag_00001", [("exp_aaaaa", MARGIN + USABLE * 0.95, 20.0)]),
            sheet("pag_00002", [("skills", MARGIN, 200.0)]),
        ]

        assert not any("stranded" in line for line in notes(doc))


class TestItReachesTheAssistant:
    def test_the_outline_carries_the_notes(self) -> None:
        doc = a_resume()
        doc.pages = [
            sheet("pag_00001", [("experience", MARGIN, USABLE - 10)]),
            sheet("pag_00002", [("skills", MARGIN, 30.0)]),
        ]

        text = outline(doc)

        assert "FORMATTING" in text

    def test_a_tidy_document_adds_nothing_to_the_outline(self) -> None:
        assert "FORMATTING" not in outline(a_resume())
