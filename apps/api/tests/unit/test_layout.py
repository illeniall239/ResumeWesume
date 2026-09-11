"""Pages, frames, and getting a flowing document onto one.

Three things are load-bearing here and everything else follows from them.

*Content does not change.* Migration adds a layout describing where the resume
already was; if it can alter what the resume says, it is not safe to run on
read.

*Layout is deterministic.* Migration is lazy, so the same stored row is laid out
again on every read until something writes. Random ids there produce a document
whose frames have a different id on every request, which makes every one of
them unaddressable.

*Coverage holds.* Every content node is rendered by exactly one frame, or free
placement becomes a way to lose a job without being told.
"""

from __future__ import annotations

from studio.doc.apply import OpContext, apply_ops, tier_of
from studio.doc.autolayout import A4, derived_id, layout
from studio.doc.index import NodeIndex
from studio.doc.migrate import load_doc
from studio.doc.nodes import NodeKind, is_valid
from studio.doc.ops import (
    InsertNode,
    MoveNode,
    RejectCode,
    RemoveNode,
    Reorder,
    SetElementStyle,
    SetField,
    SetGeometry,
)
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    EducationNode,
    ExperienceNode,
    FrameElement,
    PageNode,
    PersonalInfo,
    Rect,
    ShapeElement,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

EXP = "exp_11111"
BULLET = "blt_aaaaa"
GROUP = "sgp_ggggg"


def flowing() -> StudioDoc:
    """A v1-shaped document: content, no pages."""
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Senior Engineer",
                company="Northwind",
                bullets=[TextNode(nid=BULLET, text="Rebuilt the ledger.")],
            )
        ],
        education=[EducationNode(nid="edu_11111", institution="UT", degree="BS")],
        skills=[SkillGroup(nid=GROUP, items=[SkillItem(nid="skl_pppp1", text="Python")])],
        sections=list(DEFAULT_SECTIONS),
    )


def placed() -> StudioDoc:
    """The same document, laid out."""
    doc = flowing()
    doc.pages = layout(doc)
    return doc


def all_tiers() -> OpContext:
    return OpContext(granted_tiers={"A", "B", "C"})


class TestAutoLayout:
    def test_a_frame_for_each_heading_and_each_entry(self) -> None:
        """One frame per *entry*, not per section.

        A frame moves whole, so a section frame holding five jobs is one 700pt
        box that cannot share a page -- which turned a two-page resume into
        three at migration. The flowing renderer treated an entry as the
        unbreakable unit for the same reason.
        """
        doc = placed()
        refs = [e.ref for p in doc.pages for e in p.elements]
        assert refs == [
            "personal",
            "summary",
            "experience",
            EXP,
            "education",
            "edu_11111",
            "skills",
        ]

    def test_a_section_frame_survives_its_entries_being_pulled_out(self) -> None:
        """It still has a heading to draw."""
        doc = placed()
        assert any(e.ref == "experience" for e in doc.pages[0].elements)
        assert any(e.ref == EXP for e in doc.pages[0].elements)

    def test_frames_follow_the_documents_own_section_order(self) -> None:
        """A migrated document must render the way it already rendered."""
        doc = flowing()
        doc.sections = [
            meta.model_copy(update={"order": {"skills": 0, "experience": 1}.get(meta.key, 9)})
            for meta in DEFAULT_SECTIONS
        ]
        refs = [e.ref for p in layout(doc) for e in p.elements]
        assert refs.index("skills") < refs.index("experience")

    def test_frames_are_full_width_and_stacked(self) -> None:
        """Which is what makes the result identical to the flow it replaces --
        a migrated document cannot come out overlapping or reordered."""
        elements = placed().pages[0].elements
        assert all(e.rect.w == A4.content_width for e in elements)
        tops = [e.rect.y for e in elements]
        assert tops == sorted(tops)
        for above, below in zip(elements, elements[1:]):
            assert above.rect.y + above.rect.h <= below.rect.y

    def test_heights_are_advisory(self) -> None:
        """The server cannot measure text and must not pretend to; the browser
        corrects these on first open."""
        assert all(e.autogrow == "height" for e in placed().pages[0].elements)

    def test_an_empty_document_still_gets_a_page(self) -> None:
        pages = layout(StudioDoc())
        assert len(pages) == 1
        assert [e.ref for e in pages[0].elements] == ["personal"]


class TestDeterministicIds:
    def test_the_same_document_lays_out_identically_every_time(self) -> None:
        """Migration runs on every read until a write persists it. Minting
        random ids there made every frame unaddressable, because the id a
        client read was never the id the next request saw."""
        doc = flowing()
        first = [e.nid for p in layout(doc) for e in p.elements]
        second = [e.nid for p in layout(doc) for e in p.elements]
        assert first == second

    def test_derived_ids_are_well_formed_and_distinct(self) -> None:
        ids = [derived_id(NodeKind.FRAME, ref) for ref in
               ("personal", "summary", "experience", "education", "projects", "skills")]
        assert all(is_valid(nid) for nid in ids)
        assert len(set(ids)) == len(ids)

    def test_a_page_id_differs_from_a_frame_id_for_the_same_seed(self) -> None:
        assert derived_id(NodeKind.PAGE, "1") != derived_id(NodeKind.FRAME, "1")


class TestMigration:
    def test_a_v1_document_gains_pages(self) -> None:
        raw = flowing().model_dump(mode="json")
        raw["schema_version"] = 1
        raw.pop("pages", None)
        upgraded = load_doc(raw)
        assert upgraded.schema_version == 2
        assert len(upgraded.pages) == 1

    def test_migration_does_not_touch_content(self) -> None:
        """The property that makes it safe to run on read."""
        raw = flowing().model_dump(mode="json")
        raw["schema_version"] = 1
        before = StudioDoc.model_validate(raw)
        after = load_doc(raw)
        for field in (
            "personal", "summary", "experience", "education",
            "projects", "skills", "custom", "sections",
        ):
            assert getattr(after, field) == getattr(before, field), field

    def test_a_v2_document_is_left_alone(self) -> None:
        doc = placed()
        raw = doc.model_dump(mode="json")
        assert load_doc(raw).pages == doc.pages

    def test_migration_is_idempotent(self) -> None:
        raw = flowing().model_dump(mode="json")
        raw["schema_version"] = 1
        once = load_doc(raw)
        twice = load_doc(once.model_dump(mode="json"))
        assert once.model_dump() == twice.model_dump()


class TestCoverage:
    def test_removing_an_entrys_frame_returns_it_to_the_section(self) -> None:
        """"Put it back in the flow" is just a delete.

        Coverage is over containers, so an entry whose own frame is gone is
        still covered by the section frame above it -- which renders whatever
        no other frame has claimed. Nothing is stranded and nothing is copied.
        """
        doc = placed()
        frame = next(e for e in doc.pages[0].elements if e.ref == EXP)
        result, applied, rejected = apply_ops(doc, [RemoveNode(nid=frame.nid)], all_tiers())
        assert rejected == []
        assert len(applied) == 1
        assert result.experience[0].nid == EXP

    def test_deleting_every_frame_that_covers_content_is_refused(self) -> None:
        doc = placed()
        doomed = [
            e.nid for e in doc.pages[0].elements
            if getattr(e, "ref", None) in {"experience", EXP}
        ]
        result, applied, rejected = apply_ops(
            doc, [RemoveNode(nid=nid) for nid in doomed], all_tiers()
        )
        assert applied == []
        assert rejected[0].code == RejectCode.INVARIANT_VIOLATION
        assert EXP in rejected[0].message
        assert result.model_dump() == doc.model_dump()

    def test_deleting_a_frame_and_its_content_together_succeeds(self) -> None:
        """The gate runs after the batch, so "delete this box and the job in
        it" is one legal action rather than an impossible ordering problem."""
        doc = placed()
        frames = [
            e.nid for e in doc.pages[0].elements
            if getattr(e, "ref", None) in {"experience", EXP}
        ]
        result, applied, rejected = apply_ops(
            doc, [*(RemoveNode(nid=nid) for nid in frames), RemoveNode(nid=EXP)], all_tiers()
        )
        assert rejected == []
        assert len(applied) == 3
        assert result.experience == []

    def test_a_frame_pointing_at_nothing_is_refused(self) -> None:
        doc = placed()
        _, applied, rejected = apply_ops(
            doc,
            [InsertNode(parent=doc.pages[0].nid, node={"nid": "frm_zzzzz", "ref": "exp_ghost"})],
            all_tiers(),
        )
        assert applied == []
        assert rejected[0].code == RejectCode.INVARIANT_VIOLATION

    def test_a_flowing_document_is_never_gated(self) -> None:
        """No pages means one column, which renders everything by definition --
        so v1 documents, fresh imports and the ATS path are all unaffected."""
        doc = flowing()
        _, applied, rejected = apply_ops(doc, [RemoveNode(nid=EXP)], all_tiers())
        assert rejected == []
        assert len(applied) == 1

    def test_a_bullet_added_later_is_covered_by_its_ancestor(self) -> None:
        """Coverage is over containers, not leaves. This is what stops the
        layout becoming a second structure that drifts out of step."""
        doc = placed()
        _, applied, rejected = apply_ops(
            doc,
            [InsertNode(parent=EXP, index=-1, node={"nid": "blt_bbbbb", "text": "New."})],
            all_tiers(),
        )
        assert rejected == []
        assert len(applied) == 1


class TestLayoutOps:
    def frame_of(self, doc: StudioDoc, ref: str) -> FrameElement:
        return next(e for e in doc.pages[0].elements if getattr(e, "ref", None) == ref)

    def test_geometry_moves_an_element(self) -> None:
        """A drag, as the canvas actually sends one.

        `use-drag` pushes `pinned` ahead of the geometry in the same batch,
        because moving something by hand takes it out of the flow's care. The
        pin is what makes the move stick: without it the frame is still part of
        the column, and the column decides where its own frames go.
        """
        doc = placed()
        frame = self.frame_of(doc, "skills")
        result, applied, rejected = apply_ops(
            doc,
            [
                SetElementStyle(nid=frame.nid, patch={"pinned": True}),
                SetGeometry(nid=frame.nid, x=100.0, y=200.0),
            ],
            all_tiers(),
        )
        assert rejected == []
        moved = self.frame_of(result, "skills")
        assert (moved.rect.x, moved.rect.y) == (100.0, 200.0)
        # Untouched fields keep their value: a move is not a resize.
        assert moved.rect.w == frame.rect.w

    def test_an_unpinned_frame_dropped_anywhere_is_put_back(self) -> None:
        """Geometry alone carries no intent to leave the flow.

        The engine repairs a column it finds broken, whatever op broke it --
        the guarantee being that no request can store a résumé with frames
        printed over each other or hanging off the sheet, since the exported
        PDF renders the stored document exactly as it stands.
        """
        doc = placed()
        frame = self.frame_of(doc, "skills")
        was = frame.rect.y

        result, _, rejected = apply_ops(
            doc, [SetGeometry(nid=frame.nid, x=100.0, y=200.0)], all_tiers()
        )

        assert rejected == []
        assert self.frame_of(result, "skills").rect.y == was

    def test_geometry_expect_catches_a_stale_drag(self) -> None:
        doc = placed()
        frame = self.frame_of(doc, "skills")
        _, applied, rejected = apply_ops(
            doc,
            [SetGeometry(nid=frame.nid, x=10.0, expect={"x": 999.0})],
            all_tiers(),
        )
        assert applied == []
        assert rejected[0].code == RejectCode.STALE_EXPECT

    def test_geometry_expect_tolerates_float_noise(self) -> None:
        """Geometry arrives as floats from a browser; exact equality would
        reject honest values over the last bit."""
        doc = placed()
        frame = self.frame_of(doc, "skills")
        _, applied, rejected = apply_ops(
            doc,
            [SetGeometry(nid=frame.nid, x=10.0, expect={"x": frame.rect.x + 0.2})],
            all_tiers(),
        )
        assert rejected == []
        assert len(applied) == 1

    def test_geometry_on_content_is_refused(self) -> None:
        doc = placed()
        _, applied, rejected = apply_ops(doc, [SetGeometry(nid=EXP, x=1.0)], all_tiers())
        assert applied == []
        assert rejected[0].code == RejectCode.KIND_MISMATCH

    def test_element_style_changes_presentation(self) -> None:
        doc = placed()
        frame = self.frame_of(doc, "skills")
        result, _, rejected = apply_ops(
            doc, [SetElementStyle(nid=frame.nid, patch={"opacity": 0.5})], all_tiers()
        )
        assert rejected == []
        assert self.frame_of(result, "skills").style.opacity == 0.5

    def test_an_unknown_style_key_is_rejected_not_ignored(self) -> None:
        """A silently dropped key leaves the UI showing a change that never
        happened."""
        doc = placed()
        frame = self.frame_of(doc, "skills")
        _, applied, rejected = apply_ops(
            doc, [SetElementStyle(nid=frame.nid, patch={"clr": "red"})], all_tiers()
        )
        assert applied == []
        assert rejected[0].code == RejectCode.INVALID_ARGS

    def test_set_field_cannot_reach_layout(self) -> None:
        """It gates only on the attribute *name* existing, so without an
        explicit guard `frm_x.rect` passes the per-op check and fails at the
        whole-batch revalidation -- discarding the user's other work with it."""
        doc = placed()
        frame = self.frame_of(doc, "skills")
        _, applied, rejected = apply_ops(
            doc, [SetField(target=f"{frame.nid}.rect", value="nonsense")], all_tiers()
        )
        assert applied == []
        assert rejected[0].code == RejectCode.INVALID_ARGS
        assert "set_geometry" in rejected[0].message

    def test_z_order_is_list_order(self) -> None:
        """Which makes "bring to front" an ordinary reorder, with no integer to
        normalise and no ties to break."""
        doc = placed()
        page = doc.pages[0]
        original = [e.nid for e in page.elements]
        result, _, rejected = apply_ops(
            doc, [Reorder(parent=page.nid, order=list(reversed(original)))], all_tiers()
        )
        assert rejected == []
        assert [e.nid for e in result.pages[0].elements] == list(reversed(original))

    def test_a_pinned_element_moves_between_pages_and_stays(self) -> None:
        """Deliberate placement is what `pinned` means, and it is honoured.

        Dragging a frame sets it (`use-drag`), and both the re-stack here and
        the browser's measure pass skip anything carrying it -- so the page a
        person put something on is the page it stays on.
        """
        doc = placed()
        doc.pages.append(PageNode(nid="pag_bbbbb"))
        frame = self.frame_of(doc, "skills")
        frame.pinned = True

        result, _, rejected = apply_ops(
            doc, [MoveNode(nid=frame.nid, parent="pag_bbbbb", index=0)], all_tiers()
        )

        assert rejected == []
        assert [e.nid for e in result.pages[1].elements] == [frame.nid]

    def test_an_unpinned_frame_is_reclaimed_by_the_flow(self) -> None:
        """The column decides where column frames sit, and says so immediately.

        `move_node` onto a page is how pagination is *expressed* -- it is what
        the browser emits when a frame no longer fits -- so a bare one carries
        no intent to override the flow. The re-stack that follows every
        reordering batch paginates from the measured heights and pulls a frame
        that fits back onto the page it belongs on.

        Before the re-stack paginated, this move survived and the document was
        left with page two holding a frame the flow had already accounted for
        on page one, which is how a two-page résumé ended up with its second
        sheet pushed to the bottom.
        """
        doc = placed()
        doc.pages.append(PageNode(nid="pag_bbbbb"))
        frame = self.frame_of(doc, "skills")

        result, _, rejected = apply_ops(
            doc, [MoveNode(nid=frame.nid, parent="pag_bbbbb", index=0)], all_tiers()
        )

        assert rejected == []
        assert result.pages[1].elements == []
        assert frame.nid in [e.nid for e in result.pages[0].elements]

    def test_content_cannot_be_moved_onto_a_page(self) -> None:
        """A page holds elements. A job dropped straight onto one would be a
        job the flow no longer renders and the coverage gate cannot see."""
        doc = placed()
        _, applied, rejected = apply_ops(
            doc, [MoveNode(nid=EXP, parent=doc.pages[0].nid, index=0)], all_tiers()
        )
        assert applied == []
        assert rejected[0].code == RejectCode.KIND_MISMATCH

    def test_a_shape_needs_no_content_to_cover(self) -> None:
        doc = placed()
        result, applied, rejected = apply_ops(
            doc,
            [InsertNode(parent=doc.pages[0].nid, index=0,
                        node={"nid": "shp_aaaaa", "shape": "rect", "fill": "#eee"})],
            all_tiers(),
        )
        assert rejected == []
        assert len(applied) == 1
        assert isinstance(result.pages[0].elements[0], ShapeElement)


class TestLayoutTiers:
    def test_moving_a_box_never_needs_consent(self) -> None:
        """It cannot change a word and cannot delete anything."""
        doc = placed()
        frame = doc.pages[0].elements[0]
        assert tier_of(SetGeometry(nid=frame.nid, x=1.0), NodeIndex(doc)) == "A"

    def test_adding_a_page_never_needs_consent(self) -> None:
        """Blank paper destroys nothing. Without an explicit branch "pages"
        falls into the top-level rule and adding one would be tier C."""
        doc = placed()
        op = InsertNode(parent="pages", node={"nid": "pag_bbbbb"})
        assert tier_of(op, NodeIndex(doc)) == "A"

    def test_hiding_an_element_does_need_consent(self) -> None:
        doc = placed()
        frame = doc.pages[0].elements[0]
        index = NodeIndex(doc)
        assert tier_of(SetElementStyle(nid=frame.nid, patch={"visible": False}), index) == "C"
        assert tier_of(SetElementStyle(nid=frame.nid, patch={"opacity": 0.5}), index) == "A"

    def test_deleting_a_page_needs_consent_but_an_element_does_not(self) -> None:
        doc = placed()
        index = NodeIndex(doc)
        assert tier_of(RemoveNode(nid=doc.pages[0].nid), index) == "C"
        assert tier_of(RemoveNode(nid=doc.pages[0].elements[0].nid), index) == "B"


class TestLayoutUndo:
    def test_geometry_round_trips(self) -> None:
        from studio.doc.apply import invert

        doc = placed()
        frame = doc.pages[0].elements[0]
        op = SetGeometry(nid=frame.nid, x=123.0, y=456.0)
        once, _, _ = apply_ops(doc, [op], all_tiers())

        undo = invert(op)
        assert undo is not None
        twice, _, rejected = apply_ops(once, [undo], all_tiers())
        assert rejected == []
        assert twice.model_dump() == doc.model_dump()

    def test_element_style_round_trips(self) -> None:
        from studio.doc.apply import invert

        doc = placed()
        frame = doc.pages[0].elements[0]
        op = SetElementStyle(nid=frame.nid, patch={"opacity": 0.25})
        once, _, _ = apply_ops(doc, [op], all_tiers())

        undo = invert(op)
        assert undo is not None
        twice, _, rejected = apply_ops(once, [undo], all_tiers())
        assert rejected == []
        assert twice.model_dump() == doc.model_dump()


class TestArrangements:
    """Layouts, which are geometry rather than CSS.

    A two-column *template* was tried here and removed, and `globals.css` still
    records why: "the canvas gives a section heading and its entries separate
    frames, so a grid spanning both had nothing to span." A layout that is a
    class on `.flow` can only reach inside one frame, and a column is wider
    than any one frame. So an arrangement has to be expressed where the frames
    are placed, which is here.
    """

    def _by_ref(self, pages: list[PageNode]) -> dict[str, Rect]:
        return {e.ref: e.rect for e in pages[0].elements}

    def test_stack_is_unchanged(self) -> None:
        """Every document that exists is a stack, so this path has to stay
        exactly what it was -- a regression here moves somebody's résumé
        rather than offering them a new arrangement."""
        rects = self._by_ref(layout(flowing(), arrangement="stack"))

        assert {r.x for r in rects.values()} == {A4.margin}
        assert {r.w for r in rects.values()} == {A4.content_width}

    def test_stack_is_still_the_default(self) -> None:
        doc = flowing()

        assert doc.layout == "stack"
        assert self._by_ref(layout(doc)) == self._by_ref(
            layout(doc, arrangement="stack")
        )

    def test_a_sidebar_puts_the_short_sections_in_the_rail(self) -> None:
        rects = self._by_ref(layout(flowing(), arrangement="sidebar_left"))

        assert rects["skills"].x == rects["education"].x
        assert rects["skills"].w == rects["education"].w
        # The rail is genuinely narrow, not a slightly thinner column.
        assert rects["skills"].w < rects["experience"].w
        assert rects["experience"].x > rects["skills"].x

    def test_a_right_sidebar_is_the_mirror(self) -> None:
        left = self._by_ref(layout(flowing(), arrangement="sidebar_left"))
        right = self._by_ref(layout(flowing(), arrangement="sidebar_right"))

        assert right["skills"].w == left["skills"].w
        assert right["experience"].w == left["experience"].w
        # The rail moved to the far side; the main column to the margin.
        assert right["skills"].x > right["experience"].x
        assert right["experience"].x == A4.margin

    def test_the_header_spans_whichever_arrangement(self) -> None:
        """A name is the one thing on a résumé that is never in a column."""
        for arrangement in ("stack", "sidebar_left", "sidebar_right"):
            rects = self._by_ref(layout(flowing(), arrangement=arrangement))

            assert rects["personal"].x == A4.margin
            assert rects["personal"].w == A4.content_width

    def test_both_columns_start_below_the_header(self) -> None:
        """Not at the top of the page. A column that ignores the spanning frame
        above it is drawn over the person's name."""
        rects = self._by_ref(layout(flowing(), arrangement="sidebar_left"))
        header_bottom = rects["personal"].y + rects["personal"].h

        assert rects["skills"].y >= header_bottom
        assert rects["summary"].y >= header_bottom

    def test_the_columns_do_not_overlap(self) -> None:
        rects = self._by_ref(layout(flowing(), arrangement="sidebar_left"))
        rail_right = rects["skills"].x + rects["skills"].w

        assert rail_right <= rects["experience"].x

    def test_nothing_runs_off_the_paper(self) -> None:
        for arrangement in ("sidebar_left", "sidebar_right"):
            for rect in self._by_ref(layout(flowing(), arrangement=arrangement)).values():
                assert rect.x >= A4.margin
                assert rect.x + rect.w <= A4.width - A4.margin + 0.01

    def test_an_entry_sits_in_its_own_section_column(self) -> None:
        """A job under a heading in the wide column cannot be in the rail."""
        pages = layout(flowing(), arrangement="sidebar_left")
        rects = {e.ref: e.rect for e in pages[0].elements}

        assert rects[EXP].x == rects["experience"].x
        assert rects[EXP].w == rects["experience"].w

    def test_an_arrangement_still_covers_every_node(self) -> None:
        """The coverage gate is the invariant the whole canvas rests on: every
        content node rendered by exactly one frame. A layout that drops a
        section is a way to lose a job without being told."""
        stacked = {e.ref for e in layout(flowing(), arrangement="stack")[0].elements}

        for arrangement in ("sidebar_left", "sidebar_right"):
            placed = {e.ref for e in layout(flowing(), arrangement=arrangement)[0].elements}
            assert placed == stacked

    def test_ids_do_not_depend_on_the_arrangement(self) -> None:
        """Ids are derived from the ref, not minted, because migration runs on
        read -- so the same document laid out two ways is still addressable by
        the same names."""
        stacked = {e.nid for e in layout(flowing(), arrangement="stack")[0].elements}
        sidebar = {e.nid for e in layout(flowing(), arrangement="sidebar_left")[0].elements}

        assert stacked == sidebar
