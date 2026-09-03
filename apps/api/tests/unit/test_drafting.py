"""Reading a tool call while it is still being written.

The stream carries everything needed to show an edit arriving -- which node,
and how much of the new text exists so far -- a second before the call
balances and the edit lands all at once.

Every input here is deliberately truncated. That is the normal case, not the
error case.
"""

from __future__ import annotations

from studio.agent.drafting import read


def stream(*fragments: str) -> list[str]:
    """Every prefix of the joined fragments, as the buffer actually grows."""
    whole = "".join(fragments)
    return [whole[:index] for index in range(1, len(whole) + 1)]


class TestWhatItReads:
    def test_a_target_and_a_partial_value(self) -> None:
        draft = read('{"nid": "blt_9c21x", "value": "Cut settlement laten')

        assert draft is not None
        assert draft.target == "blt_9c21x"
        assert draft.text == "Cut settlement laten"

    def test_a_finished_value(self) -> None:
        draft = read('{"nid": "blt_9c21x", "value": "Cut latency 96%.", "reason": "a')

        assert draft is not None
        assert draft.text == "Cut latency 96%."

    def test_a_field_path_target(self) -> None:
        """`set_personal_info` writes `personal.phone`, not a node id."""
        draft = read('{"target": "personal.phone", "value": "0313 290')

        assert draft is not None
        assert draft.target == "personal.phone"

    def test_the_bullet_tools_use_text(self) -> None:
        draft = read('{"nid": "exp_11111", "text": "Shipped the thing')

        assert draft is not None
        assert draft.text == "Shipped the thing"


class TestWhatItRefuses:
    def test_nothing_before_a_target(self) -> None:
        assert read('{"val') is None
        assert read("") is None

    def test_nothing_before_any_text(self) -> None:
        """A cleared line that then refills reads as a deletion.

        So a call whose arguments are still only a node id shows nothing at
        all, rather than briefly blanking the node it is about to rewrite.
        """
        assert read('{"nid": "blt_9c21x"') is None
        assert read('{"nid": "blt_9c21x", "value": "') is None

    def test_an_argument_that_is_not_text_is_not_a_draft(self) -> None:
        assert read('{"nid": "blt_9c21x", "reason": "tightening it') is None


class TestEscaping:
    def test_escapes_are_undone(self) -> None:
        draft = read('{"nid": "sum_1", "value": "She said \\"go\\" and')

        assert draft is not None
        assert draft.text == 'She said "go" and'

    def test_a_dangling_escape_is_dropped(self) -> None:
        """The second half has not arrived; drawing the first puts a stray
        backslash on the page for one frame."""
        draft = read('{"nid": "sum_1", "value": "line one\\')

        assert draft is not None
        assert draft.text == "line one"

    def test_a_newline_escape(self) -> None:
        draft = read('{"nid": "sum_1", "value": "one\ntwo')

        assert draft is not None
        assert draft.text == "one\ntwo"

    def test_a_quote_inside_the_text_does_not_end_it(self) -> None:
        draft = read('{"nid": "sum_1", "value": "the \\"payments\\" ledger and mor')

        assert draft is not None
        assert draft.text.endswith("ledger and mor")


class TestGrowth:
    def test_the_text_only_ever_grows(self) -> None:
        """Every prefix of a real call, checked in order.

        A draft that shortened would read as the model deleting what it just
        wrote.
        """
        whole = '{"nid": "blt_9c21x", "value": "Cut settlement latency 96%.", "reason": "x"}'
        longest = ""
        for prefix in stream(whole):
            draft = read(prefix)
            if draft is None:
                continue
            assert draft.text.startswith(longest[: len(draft.text)])
            if len(draft.text) >= len(longest):
                longest = draft.text

        assert longest == "Cut settlement latency 96%."

    def test_it_never_raises_on_any_prefix(self) -> None:
        for prefix in stream('{"nid": "a", "value": "b\\"c\\d\ne", "x": 1}'):
            read(prefix)


class TestFieldTargets:
    """Where the arguments do not simply name a node.

    The page draws a field as `nid.field`; a draft keyed on the bare nid points
    at the element that renders the whole entry, so nothing appears.
    """

    def test_a_field_edit_addresses_the_field(self) -> None:
        draft = read('{"nid": "exp_11111", "field": "title", "value": "Senior AI Eng')

        assert draft is not None
        assert draft.target == "exp_11111.title"
        assert draft.text == "Senior AI Eng"

    def test_a_contact_detail_has_no_node_id(self) -> None:
        """`set_personal_info` says field "phone" and means `personal.phone`."""
        draft = read(
            '{"field": "phone", "value": "0313 290', name="set_personal_info"
        )

        assert draft is not None
        assert draft.target == "personal.phone"

    def test_a_bare_field_without_the_tool_is_not_guessed(self) -> None:
        assert read('{"field": "phone", "value": "0313') is None

    def test_an_insertion_draws_nothing(self) -> None:
        """An added bullet has no element on the page to type into.

        The honest picture of an insertion is the line appearing when it lands.
        """
        assert (
            read('{"title": "System Developer", "company": "Geo TV", "years": "Sep')
            is None
        )
