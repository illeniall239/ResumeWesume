"""Em dashes, and why the assistant does not get to use them.

Not because the punctuation is wrong. Because a résumé full of them reads as
machine-written to anyone who has seen a few, and this application exists to
produce a document that reads as the person's own.
"""

from __future__ import annotations

from studio.agent.typography import clean


class TestParentheticals:
    def test_a_spaced_em_dash_becomes_a_comma(self) -> None:
        assert clean("Rebuilt the ledger — and cut latency 96%.") == (
            "Rebuilt the ledger, and cut latency 96%."
        )

    def test_a_tight_double_hyphen_style_dash_too(self) -> None:
        assert clean("the ledger—and the settlement path") == (
            "the ledger, and the settlement path"
        )

    def test_an_en_dash_is_the_same_tell(self) -> None:
        assert clean("Shipped it – then measured it.") == "Shipped it, then measured it."


class TestRanges:
    def test_dates_keep_a_hyphen(self) -> None:
        """A comma between two endpoints would be nonsense."""
        assert clean("Sep 2025 — Present") == "Sep 2025 - Present"
        assert clean("2021 — 2024") == "2021 - 2024"

    def test_a_closed_up_range(self) -> None:
        assert clean("2021—2024") == "2021-2024"

    def test_a_clause_is_not_mistaken_for_a_range(self) -> None:
        """The narrow rule earns its keep here.

        "the ledger - and the settlement path" would read as a typo.
        """
        assert "," in clean("Rebuilt the ledger — and the settlement path")


class TestItLeavesTextAlone:
    def test_text_with_no_dashes_is_untouched(self) -> None:
        line = "Rebuilt the payments ledger, cutting latency 96%."
        assert clean(line) is line

    def test_a_plain_hyphen_survives(self) -> None:
        assert clean("end-to-end encryption") == "end-to-end encryption"

    def test_a_minus_sign_survives(self) -> None:
        assert clean("reduced errors 30-40%") == "reduced errors 30-40%"

    def test_empty_text(self) -> None:
        assert clean("") == ""

    def test_it_is_idempotent(self) -> None:
        once = clean("Rebuilt the ledger — and cut latency.")
        assert clean(once) == once


class TestPunctuationStaysReadable:
    def test_no_comma_is_stacked_on_existing_punctuation(self) -> None:
        """The substitution cannot see what follows it."""
        assert clean("Shipped the model —, then measured it") == (
            "Shipped the model, then measured it"
        )

    def test_a_dash_before_a_full_stop(self) -> None:
        assert clean("Cut latency — .") == "Cut latency."


class TestItReachesEveryOp:
    """Cleaned once, where every tool funnels through.

    Not per tool: nine call sites means the tenth tool, added later, quietly
    puts em dashes back on the résumé.
    """

    def test_a_rewrite(self) -> None:
        from studio.doc.ops import SetText
        from studio.agent.typography import clean_op

        op = clean_op(SetText(nid="blt_a", value="Rebuilt it — and shipped it."))

        assert op.value == "Rebuilt it, and shipped it."

    def test_a_field(self) -> None:
        from studio.doc.ops import SetField
        from studio.agent.typography import clean_op

        op = clean_op(SetField(target="exp_1.years", value="Sep 2025 — Present"))

        assert op.value == "Sep 2025 - Present"

    def test_a_whole_inserted_job_including_its_bullets(self) -> None:
        """`add_experience` arrives as a nested dict, not a flat value."""
        from studio.doc.ops import InsertNode
        from studio.agent.typography import clean_op

        op = clean_op(
            InsertNode(
                parent="experience",
                index=-1,
                node={
                    "nid": "exp_new",
                    "title": "Analyst — Systems",
                    "years": "2021 — 2024",
                    "bullets": [{"nid": "blt_x", "text": "Built it — then ran it."}],
                },
            )
        )

        assert op.node["title"] == "Analyst, Systems"
        assert op.node["years"] == "2021 - 2024"
        assert op.node["bullets"][0]["text"] == "Built it, then ran it."

    def test_expect_is_left_exactly_as_it_arrived(self) -> None:
        """It guards against a stale edit by matching the document byte for
        byte, so cleaning it would fail every guarded rewrite."""
        from studio.doc.ops import SetText
        from studio.agent.typography import clean_op

        op = clean_op(
            SetText(nid="blt_a", value="new", expect="the old — text")
        )

        assert op.expect == "the old — text"

    def test_an_op_with_nothing_to_clean_is_returned_unchanged(self) -> None:
        from studio.doc.ops import SetText
        from studio.agent.typography import clean_op

        op = SetText(nid="blt_a", value="Nothing to do here.")

        assert clean_op(op) is op
