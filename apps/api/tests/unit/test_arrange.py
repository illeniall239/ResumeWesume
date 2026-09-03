"""Arranging elements, without a model or a document in sight.

``arrange`` is the only way the assistant can change where anything sits, and
it exists in this shape for a safety reason rather than a convenience one: a
``set_geometry`` tool would hand a model free coordinates under a Tier A grant,
and ``set_geometry(frm_experience, y=-9999)`` would hide an employment history
where no drift guard could see it, because every guard compares words.

So the arithmetic lives here and is tested here. Two properties carry the
design and are asserted directly: **nothing lands off the page**, and **nothing
changes size unless the preset says so**.
"""

from __future__ import annotations

import pytest

from studio.doc.arrange import (
    PRESETS,
    ArrangeError,
    arrange,
    compute,
    paper_of,
)
from studio.doc.schema import (
    ElementStyle,
    FrameElement,
    PageNode,
    PersonalInfo,
    Rect,
    ShapeElement,
    StudioDoc,
)


def shape(nid: str, x: float, y: float, w: float = 100, h: float = 50) -> ShapeElement:
    return ShapeElement(nid=nid, shape="rect", rect=Rect(x=x, y=y, w=w, h=h))


def frame(nid: str, ref: str, x: float, y: float, w: float = 100, h: float = 50):
    return FrameElement(
        nid=nid, ref=ref, rect=Rect(x=x, y=y, w=w, h=h), style=ElementStyle()
    )


def page(*elements) -> PageNode:
    return PageNode(nid="pag_aaaaa", elements=list(elements))


def doc_of(*pages: PageNode) -> StudioDoc:
    return StudioDoc(personal=PersonalInfo(name="Alex"), pages=list(pages))


#: Three boxes of different sizes at different places: enough for every preset
#: to have something to actually do.
def spread() -> PageNode:
    return page(
        shape("shp_aaaaa", 10, 10, 100, 50),
        shape("shp_bbbbb", 200, 80, 60, 90),
        shape("shp_ccccc", 400, 30, 40, 20),
    )


ALL = ["shp_aaaaa", "shp_bbbbb", "shp_ccccc"]


class TestAligning:
    def test_left_edges_meet_at_the_leftmost(self) -> None:
        result = compute(spread(), "align_left", ALL)
        assert {round(rect.x, 3) for rect in result.values()} == {10.0}

    def test_right_edges_meet_at_the_rightmost(self) -> None:
        result = compute(spread(), "align_right", ALL)
        assert {round(rect.x + rect.w, 3) for rect in result.values()} == {440.0}

    def test_top_edges_meet_at_the_topmost(self) -> None:
        result = compute(spread(), "align_top", ALL)
        assert {round(rect.y, 3) for rect in result.values()} == {10.0}

    def test_bottom_edges_meet_at_the_bottommost(self) -> None:
        result = compute(spread(), "align_bottom", ALL)
        assert {round(rect.y + rect.h, 3) for rect in result.values()} == {170.0}

    def test_centres_use_the_bounding_box_not_the_average(self) -> None:
        """Two boxes clustered left and one far right.

        Averaging the centres drags the result towards the cluster, which is
        not what "line these up" means -- the answer should be the middle of
        what is selected, wherever the boxes happen to bunch.
        """
        sheet = page(
            shape("shp_aaaaa", 0, 0, 100, 10),
            shape("shp_bbbbb", 10, 40, 100, 10),
            shape("shp_ccccc", 400, 80, 100, 10),
        )
        result = compute(sheet, "align_horizontal_centers", ALL)
        centres = {round(rect.x + rect.w / 2, 3) for rect in result.values()}
        assert centres == {250.0}  # (0 + 500) / 2, not the mean of 50/60/450

    def test_aligning_never_resizes(self) -> None:
        before = {e.nid: e.rect for e in spread().elements}
        for preset in ("align_left", "align_right", "align_top", "align_bottom"):
            for nid, rect in compute(spread(), preset, ALL).items():
                assert (rect.w, rect.h) == (before[nid].w, before[nid].h), preset


class TestDistributing:
    def test_gaps_between_neighbours_are_equal(self) -> None:
        result = compute(spread(), "distribute_horizontally", ALL)
        spans = sorted((rect.x, rect.x + rect.w) for rect in result.values())
        gaps = [round(spans[i + 1][0] - spans[i][1], 6) for i in range(len(spans) - 1)]
        assert len(set(gaps)) == 1

    def test_the_outermost_two_do_not_move(self) -> None:
        """They are the anchors: distributing is about the space between."""
        result = compute(spread(), "distribute_horizontally", ALL)
        assert round(result["shp_aaaaa"].x, 3) == 10.0
        assert round(result["shp_ccccc"].x + result["shp_ccccc"].w, 3) == 440.0

    def test_vertically_too(self) -> None:
        result = compute(spread(), "distribute_vertically", ALL)
        spans = sorted((rect.y, rect.y + rect.h) for rect in result.values())
        gaps = [round(spans[i + 1][0] - spans[i][1], 6) for i in range(len(spans) - 1)]
        assert len(set(gaps)) == 1

    def test_two_elements_is_refused(self) -> None:
        # With two, "space these evenly" is already true, and an op that
        # changes nothing still burns a document version.
        with pytest.raises(ArrangeError, match="three"):
            arrange(doc_of(spread()), "distribute_horizontally", ALL[:2])


class TestCentringOnThePage:
    def test_horizontally(self) -> None:
        sheet = spread()
        paper = paper_of(sheet)
        result = compute(sheet, "center_on_page_horizontally", ["shp_aaaaa"])
        rect = result["shp_aaaaa"]
        assert round(rect.x + rect.w / 2, 3) == round(paper.width / 2, 3)

    def test_both_axes_in_one_arrangement(self) -> None:
        """`center_on_page` must not have one axis overwrite the other."""
        sheet = spread()
        paper = paper_of(sheet)
        rect = compute(sheet, "center_on_page", ["shp_aaaaa"])["shp_aaaaa"]
        assert round(rect.x + rect.w / 2, 3) == round(paper.width / 2, 3)
        assert round(rect.y + rect.h / 2, 3) == round(paper.height / 2, 3)

    def test_works_on_a_single_element(self) -> None:
        # The page is the reference, so there is nothing to align against.
        assert arrange(doc_of(spread()), "center_on_page", ["shp_aaaaa"])


class TestMatching:
    def test_width_matches_the_widest(self) -> None:
        result = compute(spread(), "match_width", ALL)
        assert {rect.w for rect in result.values()} == {100.0}

    def test_height_matches_the_tallest(self) -> None:
        result = compute(spread(), "match_height", ALL)
        assert {rect.h for rect in result.values()} == {90.0}

    def test_matching_never_moves(self) -> None:
        before = {e.nid: e.rect for e in spread().elements}
        for nid, rect in compute(spread(), "match_width", ALL).items():
            assert (rect.x, rect.y) == (before[nid].x, before[nid].y)


class TestNothingLeavesThePage:
    """The property that keeps this Tier A.

    A free geometry tool was refused because a model could push content out of
    sight under an ungated grant. An arrangement cannot, and that has to be
    true of every preset rather than of the ones that happen to be safe.
    """

    def test_a_match_too_wide_for_the_sheet_is_capped(self) -> None:
        sheet = page(
            shape("shp_aaaaa", 500, 10, 90, 50),
            shape("shp_bbbbb", 10, 200, 580, 50),
        )
        result = compute(sheet, "match_width", ["shp_aaaaa", "shp_bbbbb"])
        paper = paper_of(sheet)
        for rect in result.values():
            assert rect.x >= 0
            assert rect.x + rect.w <= paper.width + 0.001

    @pytest.mark.parametrize("preset", PRESETS)
    def test_every_preset_leaves_everything_on_the_sheet(self, preset: str) -> None:
        sheet = page(
            shape("shp_aaaaa", 0, 0, 500, 400),
            shape("shp_bbbbb", 300, 500, 280, 300),
            shape("shp_ccccc", 560, 780, 30, 55),
        )
        paper = paper_of(sheet)
        placed = compute(sheet, preset, ALL)
        # Or the loop below would pass by having nothing to check.
        assert len(placed) == 3, preset

        for rect in placed.values():
            assert rect.x >= -0.001, preset
            assert rect.y >= -0.001, preset
            assert rect.x + rect.w <= paper.width + 0.001, preset
            assert rect.y + rect.h <= paper.height + 0.001, preset


class TestCompilingToOps:
    def test_only_what_actually_moved(self) -> None:
        # Two already share a left edge; only the third has anything to do.
        sheet = page(
            shape("shp_aaaaa", 10, 10),
            shape("shp_bbbbb", 10, 100),
            shape("shp_ccccc", 300, 200),
        )
        ops = arrange(doc_of(sheet), "align_left", ALL)
        assert [op.nid for op in ops] == ["shp_ccccc"]

    def test_carries_what_it_measured_from(self) -> None:
        """So an arrangement computed against a document the user has since
        dragged is caught by `expect` rather than silently overwriting them."""
        ops = arrange(doc_of(spread()), "align_left", ALL)
        assert ops[0].expect == {"x": 200.0, "y": 80.0, "w": 60.0, "h": 90.0}

    def test_an_arrangement_that_changes_nothing_is_no_ops(self) -> None:
        sheet = page(shape("shp_aaaaa", 10, 10), shape("shp_bbbbb", 10, 100))
        assert arrange(doc_of(sheet), "align_left", ["shp_aaaaa", "shp_bbbbb"]) == []


class TestRefusals:
    """Every one names what to do differently, because the reader is a model
    that will try again."""

    def test_an_unknown_preset_lists_the_real_ones(self) -> None:
        with pytest.raises(ArrangeError, match="align_left"):
            arrange(doc_of(spread()), "make_it_pretty", ALL)

    def test_an_element_that_is_not_placed(self) -> None:
        with pytest.raises(ArrangeError, match="blt_aaaaa"):
            arrange(doc_of(spread()), "align_left", ["shp_aaaaa", "blt_aaaaa"])

    def test_elements_on_different_pages(self) -> None:
        # Each sheet has its own coordinate space, so this has no meaning --
        # and picking one page's edge would move something unasked.
        first = page(shape("shp_aaaaa", 10, 10))
        second = PageNode(nid="pag_bbbbb", elements=[shape("shp_bbbbb", 300, 10)])
        with pytest.raises(ArrangeError, match="different pages"):
            arrange(doc_of(first, second), "align_left", ["shp_aaaaa", "shp_bbbbb"])

    def test_one_element_where_two_are_needed(self) -> None:
        with pytest.raises(ArrangeError, match="center_on_page"):
            arrange(doc_of(spread()), "align_left", ["shp_aaaaa"])

    def test_no_elements_at_all(self) -> None:
        with pytest.raises(ArrangeError):
            arrange(doc_of(spread()), "align_left", [])

    def test_a_repeated_id_counts_once(self) -> None:
        # Otherwise ["shp_a", "shp_a"] would pass the two-element check while
        # naming one element, and align against itself.
        with pytest.raises(ArrangeError, match="two elements"):
            arrange(doc_of(spread()), "align_left", ["shp_aaaaa", "shp_aaaaa"])


class TestFramesAreArrangeableToo:
    def test_a_frame_moves_like_anything_else(self) -> None:
        sheet = page(
            frame("frm_aaaaa", "summary", 10, 10),
            frame("frm_bbbbb", "skills", 300, 100),
        )
        ops = arrange(doc_of(sheet), "align_left", ["frm_aaaaa", "frm_bbbbb"])
        assert [op.nid for op in ops] == ["frm_bbbbb"]
        assert round(ops[0].x, 3) == 10.0


class TestNudgingOneBox:
    """Moving a single element to an edge of the sheet.

    Asked to push a footer further right, the only preset that sounded right
    was `align_right` -- which aligns elements against *each other* and
    correctly refuses a selection of one. So a box could be placed once and
    then never adjusted, and the refusal explained itself without offering a
    way forward.
    """

    def _page(self) -> PageNode:
        return PageNode(
            nid="pag_aaaaa",
            elements=[
                FrameElement(
                    nid="frm_foot",
                    ref="txb_foot",
                    rect=Rect(x=100.0, y=400.0, w=200.0, h=18.0),
                )
            ],
        )

    def test_right_puts_it_against_the_margin(self) -> None:
        page = self._page()
        moved = compute(page, "snap_page_right", ["frm_foot"])
        rect = moved["frm_foot"]

        # A4 portrait, less the export's 28.35pt margin.
        assert rect.x + rect.w == pytest.approx(595.276 - 28.35)
        # And nothing else moved.
        assert rect.y == 400.0

    def test_bottom_puts_it_against_the_foot(self) -> None:
        moved = compute(self._page(), "snap_page_bottom", ["frm_foot"])
        rect = moved["frm_foot"]

        assert rect.y + rect.h == pytest.approx(841.89 - 28.35)
        assert rect.x == 100.0

    def test_left_and_top(self) -> None:
        page = self._page()
        assert compute(page, "snap_page_left", ["frm_foot"]).get(
            "frm_foot"
        ).x == pytest.approx(28.35)
        assert compute(page, "snap_page_top", ["frm_foot"]).get(
            "frm_foot"
        ).y == pytest.approx(28.35)

    def test_one_element_is_enough(self) -> None:
        """The whole point: the page is the reference, not the other boxes."""
        from studio.doc.arrange import WORKS_ALONE

        for preset in (
            "snap_page_left",
            "snap_page_right",
            "snap_page_top",
            "snap_page_bottom",
        ):
            assert preset in WORKS_ALONE
