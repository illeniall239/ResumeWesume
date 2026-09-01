"""What the agent can see and do once a document has a layout.

The 16 original tools address content nids and keep working untouched. These
tests cover the seam: the things the assistant would otherwise be blind to
(hand-placed text, which page something is on) and the two operations that only
make sense on a canvas.
"""

from __future__ import annotations

import pytest

from studio.agent.context import find, full_section, outline
from studio.agent.tools import REGISTRY, ToolError, tiers_for_message
from studio.doc.apply import OpContext, _first_orphan, apply_ops, tier_of
from studio.doc.index import NodeIndex
from studio.doc.autolayout import layout
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    Rect,
    ShapeElement,
    ExperienceNode,
    PersonalInfo,
    StudioDoc,
    TextBlockNode,
    TextNode,
)


def doc_with_text_box() -> StudioDoc:
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid="exp_11111",
                title="Engineer",
                company="Northwind",
                bullets=[TextNode(nid="blt_aaaaa", text="Rebuilt the ledger.")],
            )
        ],
        blocks=[
            TextBlockNode(
                nid="txb_aaaaa",
                role="body",
                lines=[
                    TextNode(
                        nid="sum_zzzzz",
                        text="Available from June, relocating to Berlin.",
                        style="plain",
                    )
                ],
            )
        ],
        sections=list(DEFAULT_SECTIONS),
    )
    doc.pages = layout(doc)
    return doc


def _bind_to_block(doc: StudioDoc):
    """Turn the autolayout's ``blocks`` frame into one bound to a single block.

    That is the shape the canvas's "Add text" button produces: a ``txb_`` block
    plus a frame naming it, rather than one frame covering every block.
    """
    frame = next(
        element
        for element in doc.pages[0].elements
        if getattr(element, "ref", None) == "blocks"
    )
    frame.ref = "txb_aaaaa"
    return frame


def all_tiers() -> OpContext:
    return OpContext(granted_tiers={"A", "B", "C"})


class TestSeeingHandPlacedText:
    def test_search_finds_words_in_a_text_box(self) -> None:
        """Otherwise a request about that text is unanswerable.

        The assistant would search, find nothing, and tell the user their
        resume does not contain text that is plainly on the page.
        """
        hits = find(doc_with_text_box(), "relocating Berlin")
        assert any(hit["nid"] == "sum_zzzzz" for hit in hits)

    def test_the_outline_lists_text_boxes(self) -> None:
        text = outline(doc_with_text_box())
        assert "TEXT BOXES:" in text
        assert "sum_zzzzz" in text

    def test_a_section_read_can_return_them(self) -> None:
        text = full_section(doc_with_text_box(), "blocks")
        assert "sum_zzzzz" in text
        assert "Available from June" in text

    def test_a_document_without_text_boxes_says_nothing_about_them(self) -> None:
        doc = doc_with_text_box()
        doc.blocks = []
        assert "TEXT BOXES" not in outline(doc)


class TestLayoutPreamble:
    def test_names_the_pages_and_what_is_on_them(self) -> None:
        text = outline(doc_with_text_box())
        assert "LAYOUT: 1 page(s)." in text
        assert "page 1:" in text

    def test_carries_no_coordinates(self) -> None:
        """The model has no geometry tool, so numbers it cannot act on would
        only produce instructions the user cannot follow."""
        text = outline(doc_with_text_box())
        for banned in ("rect", "x=", "y=", "28.35"):
            assert banned not in text

    def test_a_document_with_no_layout_gets_no_preamble(self) -> None:
        doc = doc_with_text_box()
        doc.pages = []
        assert "LAYOUT:" not in outline(doc)

    def test_a_single_section_read_stays_narrow(self) -> None:
        # Asking for one section should not pay for the whole page summary.
        assert "LAYOUT:" not in outline(doc_with_text_box(), section="summary")


class TestAddPage:
    def test_adds_a_blank_page_at_the_end(self) -> None:
        doc = doc_with_text_box()
        spec = REGISTRY.get("add_page")
        ops = spec.compile(spec.Args(), doc)
        result, applied, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        assert len(result.pages) == 2
        assert result.pages[-1].elements == []

    def test_is_tier_a_because_blank_paper_destroys_nothing(self) -> None:
        assert REGISTRY.get("add_page").tier == "A"

    def test_can_insert_after_a_given_page(self) -> None:
        doc = doc_with_text_box()
        spec = REGISTRY.get("add_page")
        first = doc.pages[0].nid
        ops = spec.compile(spec.Args(after=0), doc)
        result, _, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        assert result.pages[-1].nid == first


class TestRemoveElement:
    """Restrictions are asserted through ``compile``, which is the only hook
    the turn loop calls. An earlier version of this put them in a ``validate``
    method that nothing invoked: the tests passed and the guard never ran."""

    def test_refuses_a_content_node(self) -> None:
        spec = REGISTRY.get("remove_element")
        with pytest.raises(ToolError) as caught:
            spec.compile(spec.Args(nid="blt_aaaaa"), doc_with_text_box())
        assert "remove_entry" in str(caught.value)

    def test_accepts_an_image_a_shape_or_a_box(self) -> None:
        spec = REGISTRY.get("remove_element")
        doc = doc_with_text_box()
        for nid in ("img_aaaaa", "shp_aaaaa", "frm_aaaaa"):
            assert spec.compile(spec.Args(nid=nid), doc)

    def test_points_at_the_page_tool_for_a_page(self) -> None:
        spec = REGISTRY.get("remove_element")
        with pytest.raises(ToolError) as caught:
            spec.compile(spec.Args(nid="pag_aaaaa"), doc_with_text_box())
        assert "remove_page" in str(caught.value)

    def test_removing_a_box_returns_its_content_to_the_flow(self) -> None:
        doc = doc_with_text_box()
        frame = next(
            element
            for element in doc.pages[0].elements
            if getattr(element, "ref", None) == "exp_11111"
        )
        spec = REGISTRY.get("remove_element")
        result, applied, rejected = apply_ops(
            doc, spec.compile(spec.Args(nid=frame.nid), doc), all_tiers()
        )

        assert rejected == []
        # The words survive; only the box is gone.
        assert result.experience[0].nid == "exp_11111"

    def test_removing_a_free_text_box_takes_its_words(self) -> None:
        """The case the section rule above does not cover.

        A frame bound to a section is a view of content that lives in the
        resume, so removing it deletes nothing. A frame bound to a ``txb_``
        block is the only home those words have: remove it alone and the block
        is covered by nothing, the coverage gate calls it stranded, and the
        whole batch is rolled back -- so the tool failed outright on the one
        kind of box a user is most likely to ask to remove.
        """
        doc = doc_with_text_box()
        frame = _bind_to_block(doc)

        spec = REGISTRY.get("remove_element")
        ops = spec.compile(spec.Args(nid=frame.nid), doc)
        result, _, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == [], [reject.message for reject in rejected]
        assert result.blocks == []
        assert _first_orphan(result) is None
        # The resume itself is untouched.
        assert result.experience[0].bullets[0].text == "Rebuilt the ledger."

    def test_keeps_a_block_another_box_still_shows(self) -> None:
        doc = doc_with_text_box()
        frame = _bind_to_block(doc)
        twin = frame.model_copy(deep=True, update={"nid": "frm_twin1"})
        doc.pages[0].elements.append(twin)

        spec = REGISTRY.get("remove_element")
        ops = spec.compile(spec.Args(nid=frame.nid), doc)
        result, _, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        assert [block.nid for block in result.blocks] == ["txb_aaaaa"]

    def test_deleting_hand_placed_words_needs_consent(self) -> None:
        # Tier is derived from what the op touches, and what this one touches
        # is words with nowhere to return to. Without this the model could
        # delete somebody's caption under an ungated Tier A grant.
        doc = doc_with_text_box()
        frame = _bind_to_block(doc)

        spec = REGISTRY.get("remove_element")
        ops = spec.compile(spec.Args(nid=frame.nid), doc)
        index = NodeIndex(doc)

        assert [tier_of(op, index) for op in ops] == ["C", "B"]


class TestArrange:
    """The one way the assistant can change where anything sits.

    Deliberately not a geometry tool: the model names a preset and some ids,
    and the arithmetic happens on the server, so it can neither invent a
    coordinate nor push anything off the page.
    """

    def _with_shapes(self) -> StudioDoc:
        doc = doc_with_text_box()
        doc.pages[0].elements.extend(
            [
                ShapeElement(nid="shp_aaaaa", shape="rect", rect=Rect(x=40, y=40, w=100, h=50)),
                ShapeElement(nid="shp_bbbbb", shape="rect", rect=Rect(x=300, y=200, w=60, h=90)),
            ]
        )
        return doc

    def test_the_model_never_names_a_coordinate(self) -> None:
        # The argument model is the guarantee: there is nowhere to put a number.
        spec = REGISTRY.get("arrange")
        assert set(spec.Args.model_fields) == {"preset", "nids", "reason"}

    def test_aligning_two_shapes(self) -> None:
        doc = self._with_shapes()
        spec = REGISTRY.get("arrange")
        ops = spec.compile(
            spec.Args(preset="align_left", nids=["shp_aaaaa", "shp_bbbbb"]), doc
        )
        result, _, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        placed = {e.nid: e.rect for p in result.pages for e in p.elements}
        assert placed["shp_aaaaa"].x == placed["shp_bbbbb"].x == 40

    def test_arranging_a_frame_pins_it(self) -> None:
        """Or the reflow owns it again and stacks it back into the column on
        the next load -- the arrangement surviving the turn and being gone by
        morning."""
        doc = self._with_shapes()
        frames = [
            e.nid
            for e in doc.pages[0].elements
            if getattr(e, "ref", None) in ("summary", "experience")
        ]
        spec = REGISTRY.get("arrange")
        # `align_top`, because the autolayout already gives every section
        # frame the same width -- `match_width` would be a no-op here and the
        # tool would refuse it rather than pin anything.
        ops = spec.compile(spec.Args(preset="align_top", nids=frames), doc)
        result, _, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        pinned = {e.nid for p in result.pages for e in p.elements if getattr(e, "pinned", False)}
        assert set(frames) <= pinned

    def test_a_shape_needs_no_pin(self) -> None:
        # Only frames are the reflow's to manage, and `pinned` is not a field
        # a shape has -- an op setting it would be rejected.
        doc = self._with_shapes()
        spec = REGISTRY.get("arrange")
        ops = spec.compile(
            spec.Args(preset="align_left", nids=["shp_aaaaa", "shp_bbbbb"]), doc
        )
        assert all(op.op != "set_element_style" for op in ops)

    def test_is_tier_a_because_it_cannot_lose_anything(self) -> None:
        doc = self._with_shapes()
        spec = REGISTRY.get("arrange")
        ops = spec.compile(
            spec.Args(preset="align_left", nids=["shp_aaaaa", "shp_bbbbb"]), doc
        )
        index = NodeIndex(doc)
        assert {tier_of(op, index) for op in ops} == {"A"}

    def test_a_refusal_says_what_to_do_instead(self) -> None:
        doc = self._with_shapes()
        spec = REGISTRY.get("arrange")
        with pytest.raises(ToolError) as caught:
            spec.compile(spec.Args(preset="align_left", nids=["shp_aaaaa"]), doc)
        assert "center_on_page" in str(caught.value)

    def test_an_arrangement_that_changes_nothing_says_so(self) -> None:
        # Rather than reporting success for a layout that never moved.
        doc = self._with_shapes()
        doc.pages[0].elements.append(
            ShapeElement(nid="shp_ccccc", shape="rect", rect=Rect(x=40, y=400, w=20, h=20))
        )
        spec = REGISTRY.get("arrange")
        with pytest.raises(ToolError, match="already"):
            spec.compile(
                spec.Args(preset="align_left", nids=["shp_aaaaa", "shp_ccccc"]), doc
            )

    def test_the_content_is_untouched(self) -> None:
        doc = self._with_shapes()
        spec = REGISTRY.get("arrange")
        ops = spec.compile(
            spec.Args(preset="center_on_page", nids=["shp_aaaaa"]), doc
        )
        result, _, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        assert result.experience[0].bullets[0].text == "Rebuilt the ledger."
        assert _first_orphan(result) is None


class TestHidingASection:
    def test_hiding_also_hides_the_frames_that_draw_it(self) -> None:
        """Otherwise the flag flips and the page looks identical."""
        doc = doc_with_text_box()
        spec = REGISTRY.get("set_section")
        ops = spec.compile(spec.Args(key="experience", visible=False), doc)
        result, _, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        frames = [
            element
            for page in result.pages
            for element in page.elements
            if getattr(element, "ref", None) == "experience"
        ]
        assert frames and all(not frame.visible for frame in frames)

    def test_reordering_alone_leaves_visibility_untouched(self) -> None:
        doc = doc_with_text_box()
        spec = REGISTRY.get("set_section")
        ops = spec.compile(spec.Args(key="experience", order=0), doc)
        assert all(op.op != "set_element_style" for op in ops)


class TestExistingToolsStillWork:
    def test_every_original_tool_is_still_registered(self) -> None:
        # The content tools address nids in the same lists as before; a layout
        # must not have cost any of them.
        for name in (
            "rewrite_text",
            "add_bullet",
            "remove_bullet",
            "reorder_bullets",
            "set_bullet_style",
            "add_experience",
            "remove_entry",
            "move_entry",
            "set_entry_field",
            "set_entry_identity",
            "set_personal_info",
            "add_skill",
            "remove_skill",
            "set_section",
            "find_text",
            "read_document",
        ):
            assert REGISTRY.get(name) is not None, name

    def test_rewriting_a_bullet_works_on_a_laid_out_document(self) -> None:
        doc = doc_with_text_box()
        spec = REGISTRY.get("rewrite_text")
        ops = spec.compile(
            spec.Args(nid="blt_aaaaa", value="Rebuilt the ledger, cutting close time 40%."),
            doc,
        )
        result, applied, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        assert "40%" in result.experience[0].bullets[0].text

    def test_rewriting_text_inside_a_hand_placed_box_works_too(self) -> None:
        doc = doc_with_text_box()
        spec = REGISTRY.get("rewrite_text")
        ops = spec.compile(spec.Args(nid="sum_zzzzz", value="Available immediately."), doc)
        result, _, rejected = apply_ops(doc, ops, all_tiers())

        assert rejected == []
        assert result.blocks[0].lines[0].text == "Available immediately."


class TestRemovePage:
    def test_removes_a_page_holding_only_decoration(self) -> None:
        doc = doc_with_text_box()
        add = REGISTRY.get("add_page")
        doc, _, _ = apply_ops(doc, add.compile(add.Args(), doc), all_tiers())

        spec = REGISTRY.get("remove_page")
        result, _, rejected = apply_ops(
            doc, spec.compile(spec.Args(page=2), doc), all_tiers()
        )
        assert rejected == []
        assert len(result.pages) == 1

    def test_says_what_is_in_the_way_rather_than_failing_late(self) -> None:
        """The coverage gate would reject the batch with a node id, which is
        not something a user can act on. Naming the section lets the assistant
        offer to move it instead."""
        doc = doc_with_text_box()
        spec = REGISTRY.get("remove_page")
        # Give it a second page so the "needs one page" rule is not what fires.
        add = REGISTRY.get("add_page")
        doc, _, _ = apply_ops(doc, add.compile(add.Args(), doc), all_tiers())

        with pytest.raises(ToolError) as caught:
            spec.compile(spec.Args(page=1), doc)
        assert "experience" in str(caught.value)

    def test_refuses_the_last_page(self) -> None:
        spec = REGISTRY.get("remove_page")
        with pytest.raises(ToolError) as caught:
            spec.compile(spec.Args(page=1), doc_with_text_box())
        assert "at least one page" in str(caught.value)

    def test_reports_a_page_that_does_not_exist(self) -> None:
        spec = REGISTRY.get("remove_page")
        with pytest.raises(ToolError) as caught:
            spec.compile(spec.Args(page=9), doc_with_text_box())
        assert "no page 9" in str(caught.value)

    def test_takes_hand_placed_text_with_it(self) -> None:
        """A box added from the toolbar carries its own frame.

        Leaving the words behind would keep them in the ATS export while being
        invisible and unreachable in the editor -- worse than deleting them.
        """
        doc = doc_with_text_box()
        add = REGISTRY.get("add_page")
        doc, _, _ = apply_ops(doc, add.compile(add.Args(), doc), all_tiers())

        # Rebind the collective frame to this one block, which is the shape the
        # "Add text" button produces.
        frame = next(
            element
            for element in doc.pages[0].elements
            if getattr(element, "ref", None) == "blocks"
        )
        frame.ref = "txb_aaaaa"
        doc.pages[0].elements.remove(frame)
        doc.pages[1].elements.append(frame)

        spec = REGISTRY.get("remove_page")
        ops = spec.compile(spec.Args(page=2), doc)
        assert ops[0].nid == "txb_aaaaa"

        result, _, rejected = apply_ops(doc, ops, all_tiers())
        assert rejected == []
        assert result.blocks == []

    def test_is_tier_c_because_it_can_destroy_work(self) -> None:
        assert REGISTRY.get("remove_page").tier == "C"


class TestTierExposure:
    """Which tools the model is even shown.

    Tier C costs prompt tokens, so it is only offered when the message looks
    like it needs it. The failure this pins is subtle: a confirmation reply
    ("yes, remove page 3") matched no hint, so the tool the assistant had
    proposed one turn earlier was no longer in its list -- and it reported
    having no way to do the thing it had just offered to do.
    """

    def test_a_page_request_exposes_tier_c(self) -> None:
        assert "C" in tiers_for_message("Remove the blank page 3 from my resume.")

    def test_a_confirmation_reply_keeps_tier_c(self) -> None:
        for reply in ("Yes, go ahead.", "yes please", "do it", "confirm"):
            assert "C" in tiers_for_message(reply), reply

    def test_an_ordinary_request_does_not(self) -> None:
        # Every tool sent costs context that comes out of the document.
        assert "C" not in tiers_for_message("Make my summary punchier.")


class TestConsentIsSpecific:
    def test_confirming_one_page_does_not_authorise_another(self) -> None:
        """The consent token is `tool:ref`. Without the page number every
        page-removal would share the empty ref, so a "yes" to deleting a blank
        page would also authorise deleting the one holding the resume."""
        from studio.agent.loop import _consent_ref

        spec = REGISTRY.get("remove_page")
        assert _consent_ref("remove_page", spec.Args(page=3)) == "3"
        assert _consent_ref("remove_page", spec.Args(page=1)) != _consent_ref(
            "remove_page", spec.Args(page=3)
        )

    def test_a_node_tool_still_keys_on_its_nid(self) -> None:
        from studio.agent.loop import _consent_ref

        spec = REGISTRY.get("remove_element")
        assert _consent_ref("remove_element", spec.Args(nid="img_aaaaa")) == "img_aaaaa"
