"""Putting a standalone line on the page.

The assistant could rewrite anything already in the resume and rearrange what
was on the sheet, but it had no way to *add* a caption -- so asked for a footer
it correctly reported that it could not, which read as a refusal and was really
a missing tool.

Named corners rather than coordinates, for the same reason `arrange` takes a
preset: the model cannot see the page, so a number from it is a guess.
"""

from __future__ import annotations

import pytest

from studio.agent.tools import REGISTRY, ToolError
from studio.doc.apply import OpContext, apply_ops
from studio.doc.schema import PageNode, StudioDoc


def spec():
    return REGISTRY.get("add_text_box")


def doc() -> StudioDoc:
    return StudioDoc(pages=[PageNode(nid="pag_aaaaa")])


def build(**kwargs):
    tool = spec()
    return tool.compile(tool.Args(**kwargs), doc())


class TestItLands:
    def test_the_ops_survive_every_gate(self) -> None:
        """The whole point: a frame whose `ref` does not resolve is refused.

        Content has to be inserted before the frame that renders it, and ops
        within a batch apply in order.
        """
        ops = build(value="Made with ResumeWesume", corner="bottom_right")
        result, applied, rejected = apply_ops(
            doc(), ops, OpContext(granted_tiers={"A", "B", "C"})
        )

        assert rejected == []
        assert len(applied) == 2
        assert result.blocks[0].lines[0].text == "Made with ResumeWesume"
        assert result.pages[0].elements[0].ref == result.blocks[0].nid

    def test_it_is_pinned(self) -> None:
        """Otherwise the reflow pass sweeps a footer back into the column."""
        result, _, _ = apply_ops(
            doc(),
            build(value="Made with ResumeWesume"),
            OpContext(granted_tiers={"A", "B", "C"}),
        )

        assert result.pages[0].elements[0].pinned is True


class TestCorners:
    def _rect(self, corner: str) -> dict[str, float]:
        return build(value="x", corner=corner)[1].node["rect"]

    def test_bottom_right_is_bottom_right(self) -> None:
        rect = self._rect("bottom_right")

        # A4 portrait is 595.276 x 841.89pt, with the export's 28.35pt margin.
        assert rect["x"] + rect["w"] == pytest.approx(595.276 - 28.35)
        assert rect["y"] + rect["h"] == pytest.approx(841.89 - 28.35)

    def test_top_left_is_top_left(self) -> None:
        rect = self._rect("top_left")

        assert rect["x"] == pytest.approx(28.35)
        assert rect["y"] == pytest.approx(28.35)

    def test_a_centred_box_is_centred(self) -> None:
        rect = self._rect("bottom_center")

        assert rect["x"] + rect["w"] / 2 == pytest.approx(595.276 / 2)

    def test_every_corner_stays_on_the_paper(self) -> None:
        for corner in (
            "top_left",
            "top_right",
            "top_center",
            "bottom_left",
            "bottom_right",
            "bottom_center",
        ):
            rect = self._rect(corner)
            assert rect["x"] >= 0
            assert rect["y"] >= 0
            assert rect["x"] + rect["w"] <= 595.276
            assert rect["y"] + rect["h"] <= 841.89


class TestItRefusesWhatItCannotDo:
    def test_a_document_with_no_pages(self) -> None:
        tool = spec()

        with pytest.raises(ToolError, match="no pages"):
            tool.compile(tool.Args(value="x"), StudioDoc(pages=[]))

    def test_a_page_that_does_not_exist(self) -> None:
        tool = spec()

        with pytest.raises(ToolError, match="no page 4"):
            tool.compile(tool.Args(value="x", page=4), doc())


class TestTheAssistantCanReachIt:
    def test_it_is_offered_without_asking_for_layout(self) -> None:
        """Tier A: it adds words, and touches nobody's history.

        Buried in a tier the model is only handed when the message mentions
        layout, "add a footer" would still have been answered with a refusal.
        """
        names = {tool.name for tool in REGISTRY.for_tiers({"A"})}

        assert "add_text_box" in names


class TestItSpeaksTheSameVocabularyAsEveryOtherTool:
    def test_the_field_is_value(self) -> None:
        """Salvage renames `text` to `value` for every tool.

        Named `text`, this tool's own argument was renamed out from under it
        before validation, and three calls in a row were rejected as
        "text: Field required" while the model insisted it had sent one.
        """
        from studio.agent.salvage import coerce_arguments

        coerced, _ = coerce_arguments(
            "add_text_box", {"text": "Made with ResumeWesume"}, doc()
        )

        assert coerced == {"value": "Made with ResumeWesume"}
        assert spec().Args(**coerced).value == "Made with ResumeWesume"


class TestTheWordsFaceTheRightWay:
    """A box against the right edge whose text is left-aligned is not, to a
    reader, against the right edge.

    The box is wider than a short line, so a left-aligned footer in the
    bottom-right corner sat a box-width in from the edge. Asked to push it
    further right, the assistant reached for `align_right` -- which aligns
    elements against each other and correctly refuses a selection of one.
    """

    def _align(self, corner: str) -> str:
        return build(value="Made with ResumeWesume", corner=corner)[1].node["style"][
            "align"
        ]

    def test_a_right_corner_right_aligns(self) -> None:
        assert self._align("bottom_right") == "right"
        assert self._align("top_right") == "right"

    def test_a_left_corner_left_aligns(self) -> None:
        assert self._align("bottom_left") == "left"

    def test_a_centre_position_centres(self) -> None:
        assert self._align("bottom_center") == "center"

    def test_an_explicit_choice_still_wins(self) -> None:
        """Auto is a default, not a rule."""
        ops = build(value="x", corner="bottom_right", align="left")

        assert ops[1].node["style"]["align"] == "left"


class TestRestylingWhatIsAlreadyThere:
    """Correcting a box after the fact.

    The op existed and was gated; no tool exposed it. So the assistant could
    move a box and never change how its text sat inside -- ask for a footer in
    the bottom-right, then move it to the left, and the words stayed pushed
    against the right edge of a left-placed box. Telling it again did not help,
    because the thing that was wrong was not a thing it could reach.
    """

    def _tool(self):
        return REGISTRY.get("set_element_style")

    def test_alignment_can_be_changed_afterwards(self) -> None:
        tool = self._tool()
        ops = tool.compile(tool.Args(nid="frm_foot", align="left"), doc())

        assert len(ops) == 1
        assert ops[0].nid == "frm_foot"
        assert ops[0].patch == {"align": "left"}

    def test_only_what_was_named_is_changed(self) -> None:
        """A partial patch, so restyling one thing does not reset the rest."""
        tool = self._tool()
        ops = tool.compile(tool.Args(nid="frm_foot", font_scale=1.2), doc())

        assert ops[0].patch == {"font_scale": 1.2}

    def test_several_at_once(self) -> None:
        tool = self._tool()
        ops = tool.compile(
            tool.Args(nid="frm_foot", align="center", opacity=0.5), doc()
        )

        assert ops[0].patch == {"align": "center", "opacity": 0.5}

    def test_a_call_that_changes_nothing_says_so(self) -> None:
        tool = self._tool()

        with pytest.raises(ToolError, match="Say what to change"):
            tool.compile(tool.Args(nid="frm_foot"), doc())

    def test_it_cannot_hide_anything(self) -> None:
        """`visible` is Tier C -- making something invisible is content loss
        however it is spelled -- and this tool is Tier A, so it does not offer
        the field at all rather than compiling an op the gate would refuse."""
        assert "visible" not in self._tool().Args.model_fields


class TestAddingADegreeOrAProject:
    """The gap that failed silently.

    `add_experience` existed; `add_education` and `add_project` did not. Asked
    for a degree the assistant filled in the template's empty education slot,
    which looked like success -- and asked for a *second* it read the document,
    searched it, and stopped with nothing applied and nothing said.
    """

    def test_a_degree_can_be_added(self) -> None:
        tool = REGISTRY.get("add_education")
        ops = tool.compile(
            tool.Args(
                institution="NUST", degree="BS Computer Science", years="2019 - 2023"
            ),
            doc(),
        )

        assert ops[0].parent == "education"
        assert ops[0].node["institution"] == "NUST"
        assert ops[0].node["nid"].startswith("edu_")

    def test_a_degree_with_no_detail_carries_none(self) -> None:
        """`detail` is a single optional node, not a list.

        An empty one puts a blank line under the degree on the page.
        """
        tool = REGISTRY.get("add_education")
        ops = tool.compile(tool.Args(institution="NUST", degree="BS"), doc())

        assert "detail" not in ops[0].node

    def test_a_degree_with_detail_carries_a_node(self) -> None:
        tool = REGISTRY.get("add_education")
        ops = tool.compile(
            tool.Args(institution="NUST", degree="BS", detail="First class honours"),
            doc(),
        )

        assert ops[0].node["detail"]["text"] == "First class honours"
        assert ops[0].node["detail"]["nid"].startswith("blt_")

    def test_a_project_with_bullets(self) -> None:
        tool = REGISTRY.get("add_project")
        ops = tool.compile(
            tool.Args(name="Ledger rebuild", bullets=["Cut latency 96%."]), doc()
        )

        assert ops[0].parent == "projects"
        assert ops[0].node["nid"].startswith("prj_")
        assert ops[0].node["bullets"][0]["text"] == "Cut latency 96%."

    def test_both_are_tier_c(self) -> None:
        """An institution or a date nobody said is a false claim on a document
        the person will be asked about, exactly as an employer is."""
        assert REGISTRY.get("add_education").tier == "C"
        assert REGISTRY.get("add_project").tier == "C"


class TestNewSkillGroupsAndSections:
    """Two more shapes of "I asked and nothing happened".

    `add_skill` needs an existing group and errors "No skill group 'languages'"
    -- so a group could only be created by hand. And the schema has carried
    custom sections all along with no tool touching them, so "add a
    Certifications section" reached nothing.
    """

    def test_a_key_is_derived_from_the_label(self) -> None:
        """Keys address a section in the layout and in `set_section`, so they
        have to survive the punctuation a heading carries."""
        from studio.agent.tools import _slug

        assert _slug("Languages") == "languages"
        assert _slug("Certifications & Training") == "certificationsTraining"
        assert _slug("   ") == "section"

    def test_a_group_is_created_with_its_skills(self) -> None:
        tool = REGISTRY.get("add_skill_group")
        ops = tool.compile(tool.Args(label="Languages", skills=["Urdu", "English"]), doc())

        node = ops[0].node
        assert node["key"] == "languages"
        assert node["label"] == "Languages"
        assert [item["text"] for item in node["items"]] == ["Urdu", "English"]

    def test_skills_the_user_named_are_marked_as_theirs(self) -> None:
        """`source` is how the grounding notice knows which lines nobody
        vouched for. A skill arriving with the group is the user's own."""
        tool = REGISTRY.get("add_skill_group")
        ops = tool.compile(tool.Args(label="Languages", skills=["Urdu"]), doc())

        assert ops[0].node["items"][0]["source"] == "user"

    def test_a_group_that_already_exists_is_refused_by_name(self) -> None:
        from studio.doc.schema import SkillGroup

        start = doc()
        start.skills = [SkillGroup(nid="sgp_ggggg", key="technicalSkills")]
        tool = REGISTRY.get("add_skill_group")

        # Loosely matched, as `add_skill` matches: a résumé imported from a PDF
        # carries whatever group names its author used, and "Technical" must
        # not create a second group beside "technicalSkills".
        with pytest.raises(ToolError, match="already exists"):
            tool.compile(tool.Args(label="Technical"), start)

    def test_a_section_holds_one_line_per_item(self) -> None:
        """`stringList`, not `itemList`: a certification is one line, and the
        item shape carries a title, dates and bullets that render empty."""
        tool = REGISTRY.get("add_section")
        ops = tool.compile(
            tool.Args(label="Certifications", items=["AWS Solutions Architect"]),
            doc(),
        )

        node = ops[0].node
        assert node["kind"] == "stringList"
        assert node["key"] == "certifications"
        assert [s["text"] for s in node["strings"]] == ["AWS Solutions Architect"]

    def test_a_duplicate_section_is_refused(self) -> None:
        from studio.doc.schema import CustomSectionNode

        start = doc()
        start.custom = [CustomSectionNode(nid="cst_aaaaa", key="certifications")]
        tool = REGISTRY.get("add_section")

        with pytest.raises(ToolError, match="already"):
            tool.compile(tool.Args(label="Certifications"), start)

    def test_both_bring_a_frame_when_nothing_covers_them(self) -> None:
        """The coverage gate refuses content no frame draws, so the *first*
        section of a kind has to arrive with somewhere to be drawn."""
        tool = REGISTRY.get("add_section")
        ops = tool.compile(tool.Args(label="Certifications"), doc())

        assert len(ops) == 2
        assert ops[1].node["ref"] == "custom"


class TestShapesAndImages:
    """The four things the insert toolbar offers and the assistant could not.

    A rule under the name, a band down an edge, a headshot at the top -- all
    reachable from the UI and none from the sidebar.
    """

    def test_a_line_is_a_rule_not_a_sliver(self) -> None:
        """A line is stroked and has no fill; giving it one paints a filled
        sliver where a rule was asked for."""
        tool = REGISTRY.get("add_shape")
        ops = tool.compile(tool.Args(shape="line", where="under_the_name"), doc())

        node = ops[0].node
        assert node["fill"] is None
        assert node["stroke"]
        assert node["rect"]["h"] == 0.0

    def test_a_box_is_filled(self) -> None:
        tool = REGISTRY.get("add_shape")
        ops = tool.compile(tool.Args(shape="rect", where="left_edge"), doc())

        assert ops[0].node["fill"]
        assert ops[0].node["stroke_width"] == 0.0

    def test_every_place_stays_on_the_paper(self) -> None:
        tool = REGISTRY.get("add_shape")
        for where in (
            "under_the_name",
            "top_of_page",
            "bottom_of_page",
            "left_edge",
            "right_edge",
        ):
            rect = tool.compile(tool.Args(shape="rect", where=where), doc())[0].node[
                "rect"
            ]
            assert rect["x"] >= 0 and rect["y"] >= 0
            assert rect["x"] + rect["w"] <= 595.276
            assert rect["y"] + rect["h"] <= 841.89

    def test_a_named_colour_is_honoured(self) -> None:
        tool = REGISTRY.get("add_shape")
        ops = tool.compile(
            tool.Args(shape="rect", where="left_edge", colour="#8b1a1a"), doc()
        )

        assert ops[0].node["fill"] == "#8b1a1a"

    def test_an_image_is_placed_by_asset_id(self) -> None:
        tool = REGISTRY.get("add_image")
        ops = tool.compile(tool.Args(asset="a" * 64, where="top_right"), doc())

        node = ops[0].node
        assert node["asset"] == "a" * 64
        # `contain`, not `cover`: a headshot cropped square by the renderer is
        # a worse default than one that fits.
        assert node["fit"] == "contain"

    def test_uploads_are_listed_only_when_there_are_any(self) -> None:
        """A résumé with no pictures pays nothing for the capability."""
        from studio.agent.context import uploads

        assert uploads([]) == ""

    def test_uploads_names_the_id_the_tool_needs(self) -> None:
        from studio.agent.context import uploads

        class Fake:
            id = "b" * 64
            mime = "image/png"
            width = 400
            height = 400

        listing = uploads([Fake()])

        assert "b" * 64 in listing
        assert "400x400" in listing


class TestARuleFollowsTheName:
    """Where the header ends is knowable, not a constant.

    A fixed offset drew the rule under the *title* rather than the name, and
    would draw it straight through a name that wrapped to two lines. The
    personal frame's own height is corrected by the browser on first paint, so
    it is the honest answer.
    """

    def test_it_sits_below_the_personal_frame(self) -> None:
        from studio.doc.schema import FrameElement, PageNode, Rect

        start = doc()
        start.pages = [
            PageNode(
                nid="pag_aaaaa",
                elements=[
                    FrameElement(
                        nid="frm_head",
                        ref="personal",
                        rect=Rect(x=28.35, y=28.35, w=538.0, h=120.0),
                    )
                ],
            )
        ]
        tool = REGISTRY.get("add_shape")
        ops = tool.compile(tool.Args(shape="line", where="under_the_name"), start)

        # Measured from the frame's *top* edge, which is real geometry. Its
        # stored height is the server's guess -- 64pt against a real 120pt --
        # so "below the frame" put the rule through the heading underneath.
        from studio.agent.tools import _HEADER_DROP

        assert ops[0].node["rect"]["y"] == 28.35 + _HEADER_DROP

    def test_a_page_with_no_header_still_places_it(self) -> None:
        """A blank page has nothing to measure; a sensible constant remains."""
        from studio.agent.tools import _NAME_BAND

        tool = REGISTRY.get("add_shape")
        ops = tool.compile(tool.Args(shape="line", where="under_the_name"), doc())

        assert ops[0].node["rect"]["y"] == _NAME_BAND


class TestTheSkeletonSlotsGetUsed:
    """`starter_doc` ships blanks to click into, and nothing consumed them.

    One empty skill and two empty bullets exist so a freshly created résumé is
    something to type into rather than a blank sheet. But adding a real skill
    appended *past* the blank, so a résumé the assistant filled with nine
    skills carried a tenth empty one -- which the page draws as a bullet with
    no words after it, straight into the PDF.
    """

    def _starter(self):
        from studio.doc.schema import starter_doc

        return starter_doc()

    def test_the_first_skill_takes_the_blank_slot(self) -> None:
        start = self._starter()
        group = start.skills[0]
        tool = REGISTRY.get("add_skill")

        ops = tool.compile(
            tool.Args(skill="Python", group=group.key, evidence="user_request"), start
        )

        # Removed and replaced, not written into: a blank ships as the user's
        # own and this skill may not be, and `source` is what the grounding
        # notice reads.
        assert [op.op for op in ops] == ["remove_node", "insert_node"]
        assert ops[0].nid == group.items[0].nid

    def test_the_second_skill_just_appends(self) -> None:
        """Only the first addition claims the slot."""
        from studio.doc.schema import SkillItem

        start = self._starter()
        start.skills[0].items = [SkillItem(nid="skl_ppppp", text="Python")]
        tool = REGISTRY.get("add_skill")

        ops = tool.compile(
            tool.Args(skill="Rust", group=start.skills[0].key, evidence="user_request"),
            start,
        )

        assert [op.op for op in ops] == ["insert_node"]

    def test_a_blank_does_not_count_as_a_duplicate(self) -> None:
        """Both blanks normalise to the empty key, which matched itself."""
        start = self._starter()
        tool = REGISTRY.get("add_skill")

        # Would have raised "already in" before the empty ones were excluded.
        tool.compile(
            tool.Args(skill="Python", group=start.skills[0].key, evidence="user_request"),
            start,
        )

    def test_the_first_bullet_fills_the_blank(self) -> None:
        start = self._starter()
        entry = start.experience[0]
        tool = REGISTRY.get("add_bullet")

        ops = tool.compile(
            tool.Args(parent=entry.nid, value="Rebuilt the ledger."), start
        )

        # Written into, not replaced: a bullet carries no `source`, so reusing
        # the node keeps the id anything pointing at it was given.
        assert [op.op for op in ops] == ["set_text"]
        assert ops[0].nid == entry.bullets[0].nid

    def test_an_explicit_position_still_inserts(self) -> None:
        """"Put this second" means insert there, not fill a blank elsewhere."""
        start = self._starter()
        tool = REGISTRY.get("add_bullet")

        ops = tool.compile(
            tool.Args(parent=start.experience[0].nid, value="x", position=0), start
        )

        assert [op.op for op in ops] == ["insert_node"]

    def test_a_filled_entry_appends_as_before(self) -> None:
        from studio.doc.schema import TextNode

        start = self._starter()
        start.experience[0].bullets = [TextNode(nid="blt_aaaaa", text="Already here.")]
        tool = REGISTRY.get("add_bullet")

        ops = tool.compile(
            tool.Args(parent=start.experience[0].nid, value="And this."), start
        )

        assert [op.op for op in ops] == ["insert_node"]


class TestNoBlankLinesFromTheAssistant:
    """An empty line is not invisible.

    The page draws a skill as a bullet and a bullet as a bullet, so a blank one
    is a dot with no words after it -- on screen and in the PDF. Nothing
    stopped a tool writing "" or "   ", which is how a tailored résumé that
    already had a dozen skills still carried an empty one.
    """

    @pytest.mark.parametrize(
        "tool_name,args",
        [
            ("add_skill", {"skill": "", "group": "technical", "evidence": "user_request"}),
            ("add_skill", {"skill": "   ", "group": "technical", "evidence": "user_request"}),
            ("add_bullet", {"parent": "exp_11111", "value": ""}),
            ("rewrite_text", {"nid": "blt_aaaaa", "value": "  "}),
            ("add_text_box", {"value": ""}),
        ],
    )
    def test_empty_text_is_refused(self, tool_name: str, args: dict) -> None:
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            REGISTRY.get(tool_name).Args(**args)

    def test_the_message_says_what_to_do_instead(self) -> None:
        """So the model repairs rather than retrying the same call."""
        import pydantic

        with pytest.raises(pydantic.ValidationError, match="remove_bullet"):
            REGISTRY.get("add_bullet").Args(parent="exp_11111", value="")

    def test_real_text_is_untouched(self) -> None:
        assert REGISTRY.get("add_bullet").Args(
            parent="exp_11111", value="Rebuilt the ledger."
        ).value == "Rebuilt the ledger."
