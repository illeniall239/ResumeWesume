"""Assembly and salvage.

The assembler tests exist because firing a tool early is the difference between
"watch it work" and "wait, then a jump" — and because firing it *too* early, on
a prefix of its arguments, is a correctness bug.

The salvage tests are all real failure shapes from small local models.
"""

from __future__ import annotations

import pytest

from studio.agent.assembler import (
    AssembledCall,
    StreamFinished,
    TextEvent,
    ToolCallAssembler,
)
from studio.agent.salvage import (
    closest_tool,
    coerce_arguments,
    extract_embedded_calls,
    repair_json,
    resolve_legacy_path,
)
from studio.llm.backend import StreamEnd, TextDelta, ToolCallDelta
from studio.llm.scripted import call_tool, say
from studio.doc.schema import ExperienceNode, StudioDoc, TextNode

TOOLS = {"rewrite_text", "add_bullet", "remove_bullet", "add_skill"}


def drain(assembler: ToolCallAssembler, chunks) -> list:
    out = []
    for chunk in chunks:
        out.extend(assembler.feed(chunk))
    return out


class TestAssembly:
    def test_text_streams_through(self) -> None:
        assembler = ToolCallAssembler()
        events = drain(assembler, say("Tightening your summary."))
        assert all(isinstance(event, TextEvent) for event in events)
        assert assembler.text == "Tightening your summary."

    def test_tool_fires_when_arguments_balance(self) -> None:
        assembler = ToolCallAssembler()
        events = drain(
            assembler, call_tool("rewrite_text", {"nid": "blt_aaaaa", "value": "New."})
        )
        calls = [event for event in events if isinstance(event, AssembledCall)]
        assert len(calls) == 1
        assert calls[0].name == "rewrite_text"
        assert calls[0].arguments == {"nid": "blt_aaaaa", "value": "New."}

    def test_fires_before_the_stream_ends(self) -> None:
        """The whole reason this class exists: an edit must be visible while
        the model is still talking, not batched to the end."""
        assembler = ToolCallAssembler()
        chunks = list(call_tool("rewrite_text", {"nid": "blt_aaaaa", "value": "x"}))
        chunks.extend(say(" and now some trailing commentary that keeps streaming"))
        chunks.append(StreamEnd(finish_reason="tool_calls"))

        fired_at = None
        for position, chunk in enumerate(chunks):
            for event in assembler.feed(chunk):
                if isinstance(event, AssembledCall) and fired_at is None:
                    fired_at = position
        assert fired_at is not None
        assert fired_at < len(chunks) - 1

    def test_does_not_fire_on_a_prefix(self) -> None:
        """A brace inside a string must not look like the object closing."""
        assembler = ToolCallAssembler()
        events = drain(
            assembler,
            [
                ToolCallDelta(index=0, id="c1", name="rewrite_text", arguments=""),
                ToolCallDelta(index=0, arguments='{"nid": "blt_a", "value": "a } brace'),
            ],
        )
        assert not [event for event in events if isinstance(event, AssembledCall)]

    def test_escaped_quote_does_not_end_the_string(self) -> None:
        assembler = ToolCallAssembler()
        payload = '{"nid": "blt_a", "value": "she said \\"hi\\" }"}'
        events = drain(
            assembler,
            [
                ToolCallDelta(index=0, id="c1", name="rewrite_text", arguments=""),
                *[
                    ToolCallDelta(index=0, arguments=payload[i : i + 3])
                    for i in range(0, len(payload), 3)
                ],
            ],
        )
        calls = [event for event in events if isinstance(event, AssembledCall)]
        assert len(calls) == 1
        assert calls[0].arguments["value"] == 'she said "hi" }'

    def test_parallel_calls_are_tracked_by_index(self) -> None:
        assembler = ToolCallAssembler()
        chunks = [
            ToolCallDelta(index=0, id="c1", name="rewrite_text", arguments=""),
            ToolCallDelta(index=1, id="c2", name="add_bullet", arguments=""),
            ToolCallDelta(index=0, arguments='{"nid":"blt_a",'),
            ToolCallDelta(index=1, arguments='{"parent":"exp_1",'),
            ToolCallDelta(index=0, arguments='"value":"one"}'),
            ToolCallDelta(index=1, arguments='"value":"two"}'),
        ]
        calls = [
            event for event in drain(assembler, chunks) if isinstance(event, AssembledCall)
        ]
        assert [call.name for call in calls] == ["rewrite_text", "add_bullet"]
        assert calls[0].arguments["value"] == "one"
        assert calls[1].arguments["value"] == "two"

    def test_truncated_call_is_reported_not_dropped(self) -> None:
        """A silently ignored edit is indistinguishable from the model choosing
        not to make one."""
        assembler = ToolCallAssembler()
        drain(
            assembler,
            [
                ToolCallDelta(index=0, id="c1", name="rewrite_text", arguments=""),
                ToolCallDelta(index=0, arguments='{"nid": "blt_a", "val'),
            ],
        )
        leftover = assembler.unfired()
        assert len(leftover) == 1
        assert leftover[0].arguments is None

    def test_assistant_message_round_trips_calls(self) -> None:
        assembler = ToolCallAssembler()
        drain(assembler, call_tool("rewrite_text", {"nid": "blt_a", "value": "x"}))
        message = assembler.assistant_message()
        assert message["role"] == "assistant"
        assert message["tool_calls"][0]["function"]["name"] == "rewrite_text"

    def test_stream_end_is_surfaced(self) -> None:
        assembler = ToolCallAssembler()
        events = drain(assembler, [StreamEnd(finish_reason="stop", usage={"total_tokens": 5})])
        assert any(isinstance(event, StreamFinished) for event in events)
        assert assembler.usage["total_tokens"] == 5


class TestRepairJson:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ('{"a": 1}', {"a": 1}),
            ('```json\n{"a": 1}\n```', {"a": 1}),
            ('{"a": 1,}', {"a": 1}),
            ("{'a': 1}", {"a": 1}),
            ('{"a": True}', {"a": True}),
            ('Sure! {"a": 1} hope that helps', {"a": 1}),
            ('{"arguments": "{\\"nid\\": \\"blt_a\\"}"}', {"nid": "blt_a"}),
        ],
    )
    def test_repairs(self, raw: str, expected: dict) -> None:
        assert repair_json(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "not json at all", "[1, 2, 3]"])
    def test_unrepairable_returns_none(self, raw: str) -> None:
        assert repair_json(raw) is None


class TestEmbeddedCalls:
    def test_tool_call_tags(self) -> None:
        """The highest-frequency local failure: the call arrives as prose."""
        text = (
            'I will fix that.\n<tool_call>\n{"name": "rewrite_text", '
            '"arguments": {"nid": "blt_a", "value": "Better."}}\n</tool_call>'
        )
        calls = extract_embedded_calls(text, TOOLS)
        assert len(calls) == 1
        assert calls[0].name == "rewrite_text"
        assert calls[0].arguments["value"] == "Better."

    def test_fenced_json(self) -> None:
        text = '```json\n{"name": "add_bullet", "arguments": {"parent": "exp_1", "value": "x"}}\n```'
        calls = extract_embedded_calls(text, TOOLS)
        assert len(calls) == 1 and calls[0].name == "add_bullet"

    def test_bare_object(self) -> None:
        text = 'Here: {"name": "remove_bullet", "arguments": {"nid": "blt_a"}}'
        calls = extract_embedded_calls(text, TOOLS)
        assert len(calls) == 1 and calls[0].name == "remove_bullet"

    def test_unknown_tool_is_ignored(self) -> None:
        text = '<tool_call>{"name": "drop_database", "arguments": {}}</tool_call>'
        assert extract_embedded_calls(text, TOOLS) == []

    def test_duplicates_collapse(self) -> None:
        one = '<tool_call>{"name": "rewrite_text", "arguments": {"nid": "blt_a", "value": "x"}}</tool_call>'
        assert len(extract_embedded_calls(one + one, TOOLS)) == 1

    def test_plain_prose_yields_nothing(self) -> None:
        assert extract_embedded_calls("I have updated your summary.", TOOLS) == []


class TestToolNames:
    @pytest.mark.parametrize(
        "typo,expected",
        [
            ("rewrite_text", "rewrite_text"),
            ("Rewrite_Text", "rewrite_text"),
            ("rewrite_texts", "rewrite_text"),
            ("add_skills", "add_skill"),
            ("remove_bullets", "remove_bullet"),
        ],
    )
    def test_recovers_near_misses(self, typo: str, expected: str) -> None:
        assert closest_tool(typo, TOOLS) == expected

    @pytest.mark.parametrize("nonsense", ["", "completely_different", "xyzzy"])
    def test_rejects_distant_names(self, nonsense: str) -> None:
        assert closest_tool(nonsense, TOOLS) is None


class TestLegacyPaths:
    @pytest.fixture
    def doc(self) -> StudioDoc:
        return StudioDoc(
            summary=TextNode(nid="sum_00001", text="Summary."),
            experience=[
                ExperienceNode(
                    nid="exp_11111",
                    title="Engineer",
                    bullets=[
                        TextNode(nid="blt_aaaaa", text="One."),
                        TextNode(nid="blt_bbbbb", text="Two."),
                    ],
                )
            ],
        )

    @pytest.mark.parametrize(
        "path,expected",
        [
            ("summary", "sum_00001"),
            ("workExperience[0]", "exp_11111"),
            ("workExperience[0].description[0]", "blt_aaaaa"),
            ("workExperience[0].description[1]", "blt_bbbbb"),
        ],
    )
    def test_translates(self, doc: StudioDoc, path: str, expected: str) -> None:
        assert resolve_legacy_path(path, doc) == expected

    @pytest.mark.parametrize(
        "path", ["workExperience[9].description[0]", "nonsense", "", "education[0]"]
    )
    def test_unresolvable_returns_none(self, doc: StudioDoc, path: str) -> None:
        assert resolve_legacy_path(path, doc) is None

    def test_coercion_maps_a_path_onto_a_nid(self, doc: StudioDoc) -> None:
        fixed, notes = coerce_arguments(
            "rewrite_text",
            {"path": "workExperience[0].description[1]", "text": "Better."},
            doc,
        )
        assert fixed["nid"] == "blt_bbbbb"
        assert fixed["value"] == "Better."
        assert "path" not in fixed
        # Coercions are reported so a rising rate is visible.
        assert len(notes) == 2

    def test_coercion_renames_the_concurrency_field(self, doc: StudioDoc) -> None:
        fixed, _ = coerce_arguments(
            "rewrite_text", {"nid": "blt_aaaaa", "value": "x", "original": "One."}, doc
        )
        assert fixed["expect"] == "One."

    def test_coercion_wraps_a_scalar_order(self, doc: StudioDoc) -> None:
        fixed, _ = coerce_arguments("reorder_bullets", {"order": "blt_aaaaa"}, doc)
        assert fixed["order"] == ["blt_aaaaa"]
