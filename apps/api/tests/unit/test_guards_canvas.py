"""Drift guards on a document that has a layout.

A guard correction is written through ``repo.replace``, which does not go
through ``apply_ops`` -- so it never meets the coverage gate. That makes these
the only checks standing between a correction and a document the engine would
have refused. The failure mode is nasty and silent: the invalid state persists,
and the *next* edit the user makes is rejected for something they never did.
"""

from __future__ import annotations

from studio.doc.apply import _first_orphan
from studio.doc.autolayout import layout
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    ExperienceNode,
    PersonalInfo,
    StudioDoc,
    TextBlockNode,
    TextNode,
)
from studio.guards.drift import _all_text, run_guards
from studio.guards.grants import GrantScope, IntentGrant, IntentLedger

EXP = "exp_11111"
OTHER = "exp_22222"


def placed() -> StudioDoc:
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Engineer",
                company="Northwind",
                bullets=[TextNode(nid="blt_aaaaa", text="Rebuilt the ledger.")],
            ),
            ExperienceNode(
                nid=OTHER,
                title="Lead",
                company="Contoso",
                bullets=[TextNode(nid="blt_bbbbb", text="Led the rewrite.")],
            ),
        ],
        sections=list(DEFAULT_SECTIONS),
    )
    doc.pages = layout(doc)
    return doc


def hand_arranged() -> StudioDoc:
    """Every job placed individually, with no section frame above them.

    Legal, because each entry carries its own frame -- and the case where a
    restored entry has nothing left to render it.
    """
    doc = placed()
    for page in doc.pages:
        page.elements = [
            element
            for element in page.elements
            if getattr(element, "ref", None) != "experience"
        ]
    return doc


def without(doc: StudioDoc, nid: str) -> StudioDoc:
    """What the agent's delete tool compiles to: the entry and its frames."""
    result = doc.model_copy(deep=True)
    result.experience = [entry for entry in result.experience if entry.nid != nid]
    for page in result.pages:
        page.elements = [
            element for element in page.elements if getattr(element, "ref", None) != nid
        ]
    return result


class TestRestoringAnEntry:
    def test_a_restored_entry_is_still_covered(self) -> None:
        before = placed()
        corrected, reports = run_guards(before, without(before, EXP), IntentLedger())

        assert any(report.reverted for report in reports)
        assert [entry.nid for entry in corrected.experience] == [EXP, OTHER]
        assert _first_orphan(corrected) is None

    def test_a_restored_entry_gets_its_frame_back(self) -> None:
        """The case a section frame does not cover.

        With the section arranged by hand there is no container frame to fall
        back on, so restoring the entry alone leaves it unrenderable -- and
        `replace` would persist that.
        """
        before = hand_arranged()
        assert _first_orphan(before) is None

        corrected, _ = run_guards(before, without(before, EXP), IntentLedger())

        refs = [
            getattr(element, "ref", None)
            for page in corrected.pages
            for element in page.elements
        ]
        assert EXP in refs
        assert _first_orphan(corrected) is None

    def test_a_granted_removal_is_left_alone(self) -> None:
        # The user asked for it, so neither the entry nor its frame comes back.
        before = hand_arranged()
        ledger = IntentLedger()
        ledger.grant(IntentGrant(GrantScope.ENTRY_REMOVE, EXP, origin="consent"))

        corrected, reports = run_guards(before, without(before, EXP), ledger)

        assert [entry.nid for entry in corrected.experience] == [OTHER]
        assert not any(report.reverted for report in reports)
        assert _first_orphan(corrected) is None

    def test_restoring_does_not_duplicate_a_frame_that_survived(self) -> None:
        before = hand_arranged()
        after = before.model_copy(deep=True)
        # Content gone, frame left behind -- the frame must not be added twice.
        after.experience = [e for e in after.experience if e.nid != EXP]

        corrected, _ = run_guards(before, after, IntentLedger())

        refs = [
            getattr(element, "ref", None)
            for page in corrected.pages
            for element in page.elements
        ]
        assert refs.count(EXP) == 1

    def test_a_page_the_user_deleted_is_not_resurrected(self) -> None:
        before = hand_arranged()
        after = without(before, EXP)
        # The user legitimately removed a page in the same window.
        after.pages = after.pages[:1]
        page_count = len(after.pages)

        corrected, _ = run_guards(before, after, IntentLedger())

        assert len(corrected.pages) == page_count

    def test_the_layout_of_everything_else_is_untouched(self) -> None:
        before = placed()
        after = without(before, EXP)
        # A drag that landed during the turn must survive the correction.
        after.pages[0].elements[0].rect.x = 123.0

        corrected, _ = run_guards(before, after, IntentLedger())

        assert corrected.pages[0].elements[0].rect.x == 123.0


class TestWordCount:
    def test_free_text_counts_as_content(self) -> None:
        """Otherwise the quality guard reads a collapse on an innocent turn."""
        doc = placed()
        doc.blocks = [
            TextBlockNode(
                nid="txb_aaaaa",
                role="body",
                lines=[TextNode(nid="sum_zzzzz", text="Available from June.", style="plain")],
            )
        ]
        assert "Available from June." in _all_text(doc)
