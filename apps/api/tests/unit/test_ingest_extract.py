"""Getting JSON out of a small model that was not asked nicely enough.

Every case here is a shape a local model actually produces, in the same spirit
as the adversarial cases in ``test_apply.py``. The point is not that the parser
is lenient; it is that each of these costs exactly one section when it fails,
and that a failure is reported rather than swallowed.

The one to keep an eye on is the containment test at the bottom, which asserts
the *number of model calls* as well as the result. Without that, a pipeline bug
that calls the model too many times would be hidden: ``ScriptedBackend`` raises
``BackendError`` once its script runs out, and our own containment would file
that away as a failed section and carry on looking healthy.
"""

from __future__ import annotations

import asyncio

import pytest

from studio.ingest.extract import (
    complete_json,
    parse_json_into,
    parse_section,
    strip_reasoning,
)
from studio.ingest.schemas import EducationOut, ExperienceOut, SummaryOut
from studio.llm.backend import BackendError, ModelSpec, StreamEnd, TextDelta
from studio.llm.scripted import ScriptedBackend

SPEC = ModelSpec(provider="scripted", model="scripted")

VALID = '{"entries": [{"title": "Engineer", "company": "Northwind", "bullets": ["Rebuilt the ledger"]}]}'


def says(*replies: str) -> ScriptedBackend:
    """A backend that returns each reply in turn, split across chunks.

    Split mid-string on purpose: providers break arguments at arbitrary
    boundaries, and a parser that only ever sees whole replies has not been
    tested against the thing it will actually be handed.
    """
    turns = []
    for reply in replies:
        middle = len(reply) // 2
        turns.append(
            [
                TextDelta(text=reply[:middle]),
                TextDelta(text=reply[middle:]),
                StreamEnd(finish_reason="stop"),
            ]
        )
    return ScriptedBackend(turns, spec=SPEC)


async def extract_one(backend: ScriptedBackend, *, kind: str = "experience", model=ExperienceOut):
    return await parse_section(backend, "Some resume text", kind=kind, model=model)


class TestStripReasoning:
    def test_a_closed_think_block_is_removed(self) -> None:
        assert strip_reasoning("<think>hmm {a} maybe</think>{\"x\": 1}") == '{"x": 1}'

    def test_an_unclosed_think_block_drops_the_marker(self) -> None:
        """A model that runs out of budget mid-thought, or a chat template that
        opens the block and never closes it.

        Only the marker goes here. The reasoning text stays, because there is
        no reliable way to tell where it ends -- so it falls to the candidate
        ranking in ``parse_json_into`` to prefer the answer over the braces
        that reasoning about JSON is inevitably full of.
        """
        assert strip_reasoning('<think>I should output {"x": 0}') == (
            'I should output {"x": 0}'
        )

    def test_alternative_thinking_markers_are_handled(self) -> None:
        assert strip_reasoning("<|thinking|>reasoning here") == "reasoning here"

    def test_plain_text_is_untouched(self) -> None:
        assert strip_reasoning('{"x": 1}') == '{"x": 1}'


class TestMalformedJson:
    def test_a_clean_object_parses(self) -> None:
        parsed = parse_json_into(VALID, ExperienceOut)
        assert parsed is not None
        assert parsed.entries[0].company == "Northwind"

    def test_reasoning_before_the_object_is_ignored(self) -> None:
        raw = f"<think>The user wants jobs. There are {{2}} of them.</think>{VALID}"
        assert parse_json_into(raw, ExperienceOut).entries[0].title == "Engineer"

    def test_a_decoy_object_in_unclosed_reasoning_loses_to_the_real_answer(
        self,
    ) -> None:
        """Reasoning aloud about the shape leaves a plausible object in front
        of the real one. The answer is the last thing said, not the first, and
        the decoy here validates perfectly -- it is simply empty."""
        raw = (
            '<think>Maybe {"entries": []} is the right shape, or maybe I list them.\n'
            + VALID
        )
        parsed = parse_json_into(raw, ExperienceOut)
        assert parsed is not None
        assert parsed.entries[0].company == "Northwind"

    def test_a_markdown_fence_is_stripped(self) -> None:
        assert parse_json_into(f"```json\n{VALID}\n```", ExperienceOut) is not None

    def test_prose_on_both_sides_is_ignored(self) -> None:
        raw = f"Here is the JSON you asked for:\n{VALID}\nLet me know if that helps!"
        assert parse_json_into(raw, ExperienceOut).entries[0].company == "Northwind"

    def test_a_trailing_comma_is_repaired(self) -> None:
        raw = '{"entries": [{"title": "Engineer", "company": "Northwind",},]}'
        assert parse_json_into(raw, ExperienceOut).entries[0].title == "Engineer"

    def test_a_python_literal_is_repaired(self) -> None:
        """Single quotes and ``None`` -- a model that has read too much Python."""
        raw = "{'entries': [{'title': 'Engineer', 'location': None}]}"
        parsed = parse_json_into(raw, ExperienceOut)
        assert parsed.entries[0].title == "Engineer"
        assert parsed.entries[0].location is None

    def test_a_bare_top_level_array_is_rehoused(self) -> None:
        """The single most likely reply to "list the jobs", and the one
        ``repair_json`` returns nothing for because it only yields dicts."""
        raw = '[{"title": "Engineer", "company": "Northwind"}]'
        parsed = parse_json_into(raw, ExperienceOut)
        assert parsed is not None
        assert parsed.entries[0].company == "Northwind"

    def test_a_bare_array_inside_prose_and_a_fence_is_rehoused(self) -> None:
        raw = 'Sure!\n```json\n[{"title": "Engineer"}]\n```'
        assert parse_json_into(raw, ExperienceOut).entries[0].title == "Engineer"

    def test_a_scalar_where_a_list_belongs_is_coerced(self) -> None:
        raw = '{"entries": [{"title": "Engineer", "bullets": "Rebuilt the ledger"}]}'
        assert parse_json_into(raw, ExperienceOut).entries[0].bullets == [
            "Rebuilt the ledger"
        ]

    def test_objects_where_strings_belong_are_flattened(self) -> None:
        raw = '{"entries": [{"bullets": [{"text": "Rebuilt the ledger"}]}]}'
        assert parse_json_into(raw, ExperienceOut).entries[0].bullets == [
            "Rebuilt the ledger"
        ]

    def test_a_single_entry_with_no_wrapper_is_rehoused(self) -> None:
        """Captured from qwen3 on the education fixture.

        Asked for {"entries": [...]} from a section holding one school, the
        model returns the school. Passing it through would validate cleanly as
        a section containing nothing, so one school silently becomes none.
        """
        raw = (
            '{"institution": "University of Texas at Austin", '
            '"degree": "B.S. Computer Science", "years": "2014 - 2018"}'
        )
        parsed = parse_json_into(raw, EducationOut)
        assert parsed is not None
        assert len(parsed.entries) == 1
        assert parsed.entries[0].institution == "University of Texas at Austin"

    def test_an_unrecognisable_object_is_not_invented_into_an_entry(self) -> None:
        """The other side of that: re-housing anything dict-shaped would turn a
        refusal or an error payload into a job with every field blank."""
        parsed = parse_json_into('{"error": "I could not read this"}', EducationOut)
        assert parsed is None or parsed.entries == []

    def test_a_duplicated_key_takes_the_last_value(self) -> None:
        """Also captured live: the model emitted "bullets" twice in one object,
        the second time with the full list."""
        raw = (
            '{"entries": [{"company": "Northwind", "bullets": ["one"], '
            '"bullets": ["one", "two"]}]}'
        )
        parsed = parse_json_into(raw, ExperienceOut)
        assert parsed.entries[0].bullets == ["one", "two"]

    def test_nothing_at_all_returns_none(self) -> None:
        assert parse_json_into("", ExperienceOut) is None
        assert parse_json_into("   ", ExperienceOut) is None

    def test_reasoning_with_no_answer_returns_none(self) -> None:
        assert parse_json_into("<think>I am not sure what to do</think>", ExperienceOut) is None

    def test_an_apology_returns_none_rather_than_an_empty_section(self) -> None:
        """A refusal must be reported as a failure, not quietly imported as a
        resume with no jobs in it."""
        assert parse_json_into("I'm sorry, I can't help with that.", ExperienceOut) is None


class TestParseSection:
    async def test_a_good_reply_parses(self) -> None:
        parsed, code = await extract_one(says(VALID))
        assert code is None
        assert parsed.entries[0].company == "Northwind"

    async def test_chunk_boundaries_do_not_break_parsing(self) -> None:
        """The reply arrives split down the middle of the JSON."""
        parsed, _ = await extract_one(says(VALID))
        assert parsed is not None

    async def test_unparseable_output_is_reported_not_raised(self) -> None:
        parsed, code = await extract_one(says("no idea, sorry"))
        assert parsed is None
        assert code == "no_json"

    async def test_a_provider_error_is_contained(self) -> None:
        backend = ScriptedBackend([], spec=SPEC, fail_with=BackendError("ollama is down"))
        parsed, code = await extract_one(backend)
        assert parsed is None
        assert code == "provider_error"

    async def test_an_exhausted_script_is_contained(self) -> None:
        parsed, code = await extract_one(ScriptedBackend([], spec=SPEC))
        assert parsed is None
        assert code == "provider_error"

    async def test_an_empty_section_never_reaches_the_model(self) -> None:
        """Minutes of local inference are not spent confirming that nothing is
        nothing."""
        backend = says(VALID)
        parsed, code = await parse_section(
            backend, "   ", kind="experience", model=ExperienceOut
        )
        assert (parsed, code) == (None, "empty")
        assert backend.calls == 0

    async def test_a_stalled_generation_times_out(self) -> None:
        """One hung section must not hold the whole import open."""

        class Hanging:
            spec = SPEC

            async def stream(self, messages, **kwargs):
                await asyncio.sleep(30)
                yield StreamEnd()

        parsed, code = await parse_section(
            Hanging(), "text", kind="experience", model=ExperienceOut, timeout=0.05
        )
        assert parsed is None
        assert code == "timeout"

    async def test_the_summary_schema_round_trips(self) -> None:
        parsed, code = await parse_section(
            says('{"summary": "Backend engineer."}'),
            "text",
            kind="summary",
            model=SummaryOut,
        )
        assert code is None
        assert parsed.summary == "Backend engineer."


class TestContainment:
    async def test_one_bad_section_costs_only_that_section(self) -> None:
        """Three sections, the middle one garbage.

        The call-count assertion is the real content of this test: without it a
        pipeline that re-ran a section would exhaust the script, and the
        resulting ``BackendError`` would be filed as a fourth failed section
        while everything still looked fine.
        """
        backend = says(
            VALID,
            "<think>I cannot work out the format</think>",
            '{"entries": [{"title": "Analyst", "company": "Contoso"}]}',
        )

        results = []
        for _ in range(3):
            results.append(await extract_one(backend))

        assert backend.calls == 3
        assert results[0][0].entries[0].company == "Northwind"
        assert results[1] == (None, "no_json")
        assert results[2][0].entries[0].company == "Contoso"


class TestCompleteJson:
    async def test_reasoning_deltas_are_not_treated_as_the_answer(self) -> None:
        """A provider that separates reasoning must not have it concatenated
        into the JSON."""
        from studio.llm.backend import ThinkingDelta

        backend = ScriptedBackend(
            [[ThinkingDelta(text="hmm {"), TextDelta(text='{"x": 1}'), StreamEnd()]],
            spec=SPEC,
        )
        assert await complete_json(backend, [{"role": "user", "content": "hi"}]) == '{"x": 1}'

    async def test_the_prompt_reaches_the_backend(self) -> None:
        backend = says(VALID)
        await extract_one(backend)
        sent = backend.received[0]["messages"]
        assert sent[0]["role"] == "system"
        assert "Some resume text" in sent[-1]["content"]

    async def test_extraction_turns_reasoning_off(self) -> None:
        """On qwen3 this is not a tuning choice. The model spends several
        hundred tokens deliberating before its first character of output, so a
        budget sized for the answer is spent mid-thought and the section
        returns nothing: measured at 35.2s and a token-limit failure with
        reasoning on, 4.0s and correct output with it off."""
        backend = says(VALID)
        await extract_one(backend)
        assert backend.received[0]["think"] is False

    async def test_extraction_asks_for_no_tools(self) -> None:
        """Tool schemas here would be several kilobytes of context spent on
        capabilities this call must not use."""
        backend = says(VALID)
        await extract_one(backend)
        assert not backend.received[0].get("tools")


@pytest.mark.parametrize(
    "reply",
    [
        VALID,
        f"```json\n{VALID}\n```",
        f"<think>reasoning</think>\n{VALID}",
        '[{"title": "Engineer", "company": "Northwind"}]',
        "{'entries': [{'title': 'Engineer', 'company': 'Northwind'}]}",
    ],
)
async def test_every_known_reply_shape_yields_a_job(reply: str) -> None:
    """One table of the shapes we have actually seen, so a regression in any
    rung of the ladder shows up as a named failure."""
    parsed, code = await extract_one(says(reply))
    assert code is None, reply
    assert parsed.entries[0].title == "Engineer"
