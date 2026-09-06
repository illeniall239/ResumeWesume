"""The turn loop, end to end, with no network.

Every test drives the real loop, the real gates, the real guards and real
persistence. Only the model is scripted, which is the whole point of the port
in ``llm/backend.py``: if the seam were any higher, these would be testing
mocks instead of behaviour.
"""

from __future__ import annotations

import pytest

from studio.agent.budget import BudgetExceeded, TurnBudget
from studio.agent.context import find
from studio.agent.loop import TurnRequest, TurnRunner, _was_asked_for
from studio.agent.tools import REGISTRY
from studio.doc.schema import (
    ExperienceNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, turn
from studio.persistence.repo import DocumentRepo
from studio.streaming import events as ev
from studio.streaming.channel import TurnChannel

EXP = "exp_11111"
BULLET_A = "blt_aaaaa"
BULLET_B = "blt_bbbbb"
GROUP = "sgp_ggggg"
SKILL_PY = "skl_ppppp"


def make_doc() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(
            name="Alex Morgan", email="alex@example.com", phone="+1-555-0142"
        ),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Senior Engineer",
                company="Northwind",
                years="2021 - Present",
                bullets=[
                    TextNode(nid=BULLET_A, text="Rebuilt the ledger."),
                    TextNode(nid=BULLET_B, text="Led the migration."),
                ],
            )
        ],
        skills=[
            SkillGroup(
                nid=GROUP,
                key="technical",
                items=[
                    SkillItem(nid=SKILL_PY, text="Python"),
                    SkillItem(nid="skl_ggggo", text="Go"),
                ],
            )
        ],
    )


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


async def run_turn(
    repo: DocumentRepo,
    backend: ScriptedBackend,
    message: str,
    *,
    budget: TurnBudget | None = None,
    consent: set[str] | None = None,
):
    state = await repo.create(make_doc(), title="Alex Morgan")
    runner = TurnRunner(repo=repo, backend=backend, budget=budget or TurnBudget())
    channel = TurnChannel(turn_id="turn-1", document_id=state.id)
    request = TurnRequest(
        document_id=state.id, message=message, consent_tokens=consent or set()
    )
    result = await runner.run(request, channel)
    final = await repo.get(state.id)
    return result, final, channel


def emitted(channel: TurnChannel, kind: type) -> list:
    return [event for event in channel.replay_from(0) if isinstance(event, kind)]


class TestAnswerBoxedCalls:
    r"""A model that presents its call instead of making it.

    Reported from a real turn on qwen3:4b. The reply explained what it intended
    to do and finished with a maths-style final answer:

        $$\boxed{set\_personal\_info: \{"name": "Rao Muhammad Hamza"\}}$$

    No structured tool call was ever emitted, so the engine -- correctly --
    applied nothing, and the resume did not change. Recovering this is what the
    salvage ladder is for; it simply had no rung for LaTeX.
    """

    ANSWER = (
        "To tailor the resume the safe change is the name field.\n\n"
        "---\n\nFinal Answer\n\n"
        r'$$\boxed{set\_personal\_info: \{"field": "name", '
        r'"value": "Rao Muhammad Hamza"\}}$$'
    )

    async def test_a_boxed_call_still_edits_the_document(
        self, repo: DocumentRepo
    ) -> None:
        backend = ScriptedBackend(
            [turn(say(self.ANSWER), done("stop")), turn(say("Done."), done("stop"))]
        )
        result, final, channel = await run_turn(
            repo, backend, "change my name to Rao Muhammad Hamza"
        )

        # The call reached the engine, which is the whole point: before this it
        # was read as prose and thrown away.
        assert [e.name for e in emitted(channel, ev.ToolStart)] == ["set_personal_info"]
        assert result.applied == 1
        assert final.doc.personal.name == "Rao Muhammad Hamza"

    async def test_a_recovered_call_is_treated_exactly_like_a_real_one(
        self, repo: DocumentRepo
    ) -> None:
        """Salvage decides what the model meant, and nothing more.

        The recovered call now applies and is reported, which is what a
        structured call in the same position does. The property worth keeping
        is that the two paths are indistinguishable afterwards -- this rung must
        never be a way to get treatment a normal call would not get.
        """
        backend = ScriptedBackend([turn(say(self.ANSWER), done("stop"))])
        result, final, channel = await run_turn(repo, backend, "rename me")

        assert result.applied == 1
        assert final.doc.personal.name == "Rao Muhammad Hamza"
        assert [
            warning
            for warning in emitted(channel, ev.Warning)
            if warning.source == "identity"
        ]

    async def test_prose_that_only_mentions_a_tool_changes_nothing(
        self, repo: DocumentRepo
    ) -> None:
        """The looser pattern must not fire on a model talking about its tools.

        Recovering a call the model described but did not make is the whole
        point; inventing one it never described would be the same class of bug
        pointing the other way.
        """
        backend = ScriptedBackend(
            [
                turn(
                    say(
                        "I could use set_personal_info here, but the name you "
                        "gave is already what the document says, so {} nothing "
                        "needs doing."
                    ),
                    done("stop"),
                )
            ]
        )
        result, final, _ = await run_turn(repo, backend, "check my name")

        assert result.applied == 0
        assert final.doc.personal.name == "Alex Morgan"


class TestWorklistDrivesATailoring:
    """A scaffold turn does not end on the model's say-so.

    The failure: one good edit, then "Here's your tailored resume:" and the turn
    over. The model was not confused and not incapable -- it believed it had
    finished, and nothing contradicted it.
    """

    async def test_the_turn_continues_past_a_model_that_thinks_it_is_done(
        self, repo: DocumentRepo
    ) -> None:
        doc = make_doc()
        doc.scaffold = True
        state = await repo.create(doc, title="From a template")

        # Exactly the observed shape: one edit, then a farewell with no tool
        # calls at all. Before the work list that second turn ended everything.
        backend = ScriptedBackend(
            [
                turn(
                    call_tool("rewrite_text", {"nid": BULLET_A, "value": "Built ML pipelines."}),
                    done("tool_calls"),
                ),
                turn(say("Here's your tailored resume:"), done("stop")),
                turn(
                    call_tool("rewrite_text", {"nid": BULLET_B, "value": "Served models."}),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        # Enough sections named to mean the whole document.
        backend.scope_answer = "HEADLINE SUMMARY JOB_TITLES BULLETS SKILLS"
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        channel = TurnChannel(turn_id="t1", document_id=state.id)
        result = await runner.run(
            TurnRequest(document_id=state.id, message="tailor this for an AI engineer"),
            channel,
        )
        final = await repo.get(state.id)

        # It kept going after the farewell and did the second edit.
        assert result.applied == 2
        assert final.doc.experience[0].bullets[1].text == "Served models."

    async def test_a_real_resume_gets_the_same_thoroughness(
        self, repo: DocumentRepo
    ) -> None:
        """No lazier on somebody's own document.

        The list used to be scaffolding-only, which said a real résumé deserved
        less. It does not: what changes on a real résumé is the licence -- the
        assistant reframes rather than invents -- not whether the job is
        finished. Same shape as the template case: an edit, a farewell, and the
        turn must continue anyway.
        """
        state = await repo.create(make_doc(), title="Mine")
        backend = ScriptedBackend(
            [
                turn(
                    call_tool("rewrite_text", {"nid": BULLET_A, "value": "Reframed."}),
                    done("tool_calls"),
                ),
                turn(say("Here's your tailored resume:"), done("stop")),
                turn(
                    call_tool("rewrite_text", {"nid": BULLET_B, "value": "Also reframed."}),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        backend.scope_answer = "HEADLINE SUMMARY JOB_TITLES BULLETS SKILLS"
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        channel = TurnChannel(turn_id="t3", document_id=state.id)
        result = await runner.run(
            TurnRequest(document_id=state.id, message="tailor this for an AI role"),
            channel,
        )
        final = await repo.get(state.id)

        assert result.applied == 2
        assert final.doc.experience[0].bullets[1].text == "Also reframed."

    async def test_a_targeted_request_builds_no_list(self, repo: DocumentRepo) -> None:
        """"Tighten my first bullet" means that bullet.

        The list follows the request, so a narrow ask ends when the model says
        it is done -- marching through every node would be the runaway the
        guards exist to stop.
        """
        state = await repo.create(make_doc(), title="Mine")
        backend = ScriptedBackend(
            [
                turn(
                    call_tool("rewrite_text", {"nid": BULLET_A, "value": "Tighter."}),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        channel = TurnChannel(turn_id="t2", document_id=state.id)
        result = await runner.run(
            TurnRequest(document_id=state.id, message="tighten my first bullet"), channel
        )

        assert result.applied == 1
        assert backend.calls == 2


class TestScaffoldBudget:
    """The special case that stopped needing to exist.

    Tailoring a template used to trip three caps in sequence -- the touch ratio
    at 50%, ``max_ops`` at twelve edits, ``max_iterations`` at six rounds -- and
    each stopped the turn part-way through work it had been asked to do. The
    first fix raised all three for scaffolding. The better fix was to stop those
    caps blocking anything at all, at which point a scaffold turn and an ordinary
    one need exactly the same budget.
    """

    def test_scaffolding_needs_no_special_budget(self) -> None:
        base = TurnBudget()
        assert base.for_scaffold() is base

    def test_size_never_stops_a_turn(self) -> None:
        """Both of the caps that stopped the tailoring are advisory now."""
        budget = TurnBudget(max_ops=1, max_touch_ratio=0.01)
        budget.record_ops(9, ["blt_aaaaa"])
        # Neither raises; both are reported at the end instead.
        assert len(budget.review(total_nodes=40)) == 2

    def test_a_stall_still_stops(self) -> None:
        budget = TurnBudget(max_stalled_rounds=2)
        budget.end_iteration(applied_this_round=0)
        budget.end_iteration(applied_this_round=0)
        with pytest.raises(BudgetExceeded):
            budget.end_iteration(applied_this_round=0)

    def test_progress_clears_a_stall(self) -> None:
        """However long the turn runs, work resets the counter."""
        budget = TurnBudget(max_stalled_rounds=1)
        for _ in range(20):
            budget.end_iteration(applied_this_round=0)
            budget.end_iteration(applied_this_round=1)


class TestSearch:
    """What `find_text` can and cannot answer.

    It is token overlap over the text already in the document, which is the
    right design -- a search that needed a model would fail exactly when the
    model is already struggling -- but it has one consequence worth stating
    plainly, because a whole turn was lost to it: **it can only find words that
    are already there.** Asked to tailor a resume towards AI, a model searches
    for the concept it means to introduce, and every such search must miss.
    """

    def test_a_two_character_skill_is_findable(self) -> None:
        """"Go" is a skill in this document and was unfindable.

        The matcher dropped every term of two characters or fewer, so a search
        for a language named "Go" -- or "AI", "ML", "UX", "QA", "R" -- was
        discarded before it ran and answered "No matches." A miss for a word
        that is absent is an answer; a miss for one sitting in the document is
        indistinguishable from it and sends the caller looking again.
        """
        doc = make_doc()
        doc.skills[0].items[1].text = "Go"

        assert [hit["nid"] for hit in find(doc, "Go")] == ["skl_ggggo"]

    def test_stopwords_alone_still_match_nothing(self) -> None:
        assert find(make_doc(), "the and of") == []

    def test_a_concept_not_in_the_document_cannot_be_found(self) -> None:
        """The property that cost the turn, pinned so it stays understood.

        This is not a defect to fix in the matcher -- it is what a search over
        existing text means. It is a defect only in what the caller is told
        afterwards, which is why a miss now returns the outline.
        """
        assert find(make_doc(), "machine learning") == []


class TestReadOnlyLoops:
    """A turn that searches instead of editing.

    Reported as a schedule reading `read document`, then `find text` five times,
    then `Stopped after 6 rounds without settling.` -- an entire turn spent
    looking, with no edit ever attempted. A search that found nothing answered
    with the two words "No matches.", which leaves the model knowing no more
    than before it asked, so it rephrased and asked again until the rounds ran
    out.
    """

    async def test_a_miss_hands_back_the_document(self, repo: DocumentRepo) -> None:
        runner = TurnRunner.__new__(TurnRunner)
        from studio.agent.tools import FindTextArgs

        body = runner._read(
            "find_text", FindTextArgs(query="kubernetes pipeline"), make_doc(), "c1"
        )["content"]

        # The ids it should have edited instead of searching again.
        assert BULLET_A in body
        assert EXP in body
        assert "search again" in body

    async def test_a_hit_stays_terse(self, repo: DocumentRepo) -> None:
        """A successful search must not drag the whole document along."""
        runner = TurnRunner.__new__(TurnRunner)
        from studio.agent.tools import FindTextArgs

        body = runner._read(
            "find_text", FindTextArgs(query="ledger"), make_doc(), "c1"
        )["content"]

        assert BULLET_A in body
        assert "SKILLS" not in body

    async def test_running_out_of_rounds_having_changed_nothing_says_so(
        self, repo: DocumentRepo
    ) -> None:
        """"Some changes could not be applied" is false when none were tried."""
        searching = turn(
            call_tool("find_text", {"query": "something absent"}), done("tool_calls")
        )
        backend = ScriptedBackend([searching for _ in range(8)])
        result, _, channel = await run_turn(
            repo, backend, "tailor this", budget=TurnBudget(max_iterations=3)
        )

        assert result.applied == 0
        budget = [w for w in emitted(channel, ev.Warning) if w.source == "budget"]
        assert len(budget) == 1
        assert "Nothing was changed" in budget[0].message


class TestTruncation:
    """A model cut off mid-answer is not a model that chose to do nothing.

    The report this comes from: a turn produced no reply, no tool call and no
    error, and the interface showed a numbered entry with blank space under it.
    The provider had said `finish_reason: "length"` -- the model spent its whole
    generation budget inside its reasoning block and stopped before writing
    anything -- and that signal was read into the assembler and dropped.
    """

    async def test_being_cut_off_is_reported(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend([turn(done("length"))])
        result, _, channel = await run_turn(repo, backend, "tailor this for me")

        warnings = [w for w in emitted(channel, ev.Warning) if w.source == "truncated"]
        assert len(warnings) == 1
        assert "cut off" in warnings[0].message
        # Nothing was applied, and the turn still closes cleanly rather than
        # erroring: being cut short is an ordinary outcome, not a failure.
        assert result.applied == 0

    async def test_the_turn_asks_for_the_configured_budget(
        self, repo: DocumentRepo
    ) -> None:
        """Not the library default, which was half this and too tight.

        Measured on qwen3:4b against a full-size prompt: the same instruction
        needed about 1,500 generated tokens to reach its first tool call and was
        still inside its reasoning block at 2,048.
        """
        from studio.config import settings

        backend = ScriptedBackend([turn(say("Nothing to do."), done("stop"))])
        await run_turn(repo, backend, "look it over")

        assert backend.received[0]["max_tokens"] == settings.llm_max_tokens
        assert settings.llm_max_tokens >= 4096

    async def test_an_ordinary_finish_says_nothing(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend([turn(say("Nothing needed changing."), done("stop"))])
        _, _, channel = await run_turn(repo, backend, "look it over")

        assert [w for w in emitted(channel, ev.Warning) if w.source == "truncated"] == []


class TestHappyPath:
    async def test_a_single_edit_applies(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    say("Tightening that bullet."),
                    call_tool(
                        "rewrite_text",
                        {"nid": BULLET_A, "value": "Cut settlement latency 96%."},
                    ),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        result, final, channel = await run_turn(repo, backend, "tighten my first bullet")

        assert result.status == "ok"
        assert result.applied == 1
        assert final.doc.experience[0].bullets[0].text == "Cut settlement latency 96%."

    async def test_events_arrive_in_a_usable_order(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    say("Working."),
                    call_tool("rewrite_text", {"nid": BULLET_A, "value": "New."}),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        _, _, channel = await run_turn(repo, backend, "edit it")
        kinds = [type(event).__name__ for event in channel.replay_from(0)]

        assert kinds[0] == "TurnStarted"
        assert kinds[-1] == "Done"
        assert "AssistantDelta" in kinds
        assert kinds.index("ToolStart") < kinds.index("PatchApplied")

    async def test_sequence_numbers_are_gapless(self, repo: DocumentRepo) -> None:
        """Resumption after a dropped connection depends on this."""
        backend = ScriptedBackend(
            [
                turn(
                    say("Hi"),
                    call_tool("rewrite_text", {"nid": BULLET_A, "value": "x"}),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        _, _, channel = await run_turn(repo, backend, "edit")
        sequences = [event.seq for event in channel.replay_from(0)]
        assert sequences == list(range(1, len(sequences) + 1))

    async def test_several_edits_land_separately(self, repo: DocumentRepo) -> None:
        """Each patch is its own event, so the UI can animate them one by one
        rather than showing a single jump."""
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "rewrite_text", {"nid": BULLET_A, "value": "First."}, index=0
                    ),
                    call_tool(
                        "rewrite_text", {"nid": BULLET_B, "value": "Second."}, index=1
                    ),
                    done("tool_calls"),
                ),
                turn(say("Both done."), done("stop")),
            ]
        )
        result, final, channel = await run_turn(repo, backend, "tighten both bullets")

        assert result.applied == 2
        assert len(emitted(channel, ev.PatchApplied)) == 2
        assert final.doc.experience[0].bullets[0].text == "First."
        assert final.doc.experience[0].bullets[1].text == "Second."

    async def test_read_tool_does_not_mutate(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend(
            [
                turn(call_tool("read_document", {"detail": "outline"}), done("tool_calls")),
                turn(say("It looks fine."), done("stop")),
            ]
        )
        result, final, _ = await run_turn(repo, backend, "what does my resume say")

        assert result.applied == 0
        assert final.version == 1
        # The outline, with ids, was handed back to the model.
        assert any(
            "blt_" in str(message.get("content", ""))
            for call in backend.received
            for message in call["messages"]
        )


class TestWeakModelRecovery:
    async def test_tool_call_written_as_prose_is_recovered(
        self, repo: DocumentRepo
    ) -> None:
        """The highest-frequency local-model failure: the call arrives in the
        message body instead of the structured field."""
        payload = (
            '<tool_call>{"name": "rewrite_text", "arguments": '
            f'{{"nid": "{BULLET_A}", "value": "Recovered."}}}}</tool_call>'
        )
        backend = ScriptedBackend(
            [turn(say(payload), done("stop")), turn(say("Done."), done("stop"))]
        )
        result, final, _ = await run_turn(repo, backend, "fix it")

        assert result.applied == 1
        assert final.doc.experience[0].bullets[0].text == "Recovered."

    async def test_legacy_path_is_translated(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "rewrite_text",
                        {
                            "path": "workExperience[0].description[0]",
                            "text": "Translated.",
                        },
                    ),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        result, final, _ = await run_turn(repo, backend, "fix it")

        assert result.applied == 1
        assert final.doc.experience[0].bullets[0].text == "Translated."

    async def test_misspelled_tool_name_is_corrected(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    call_tool("rewrite_texts", {"nid": BULLET_A, "value": "Fixed."}),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        result, final, _ = await run_turn(repo, backend, "fix it")

        assert result.applied == 1
        assert final.doc.experience[0].bullets[0].text == "Fixed."

    async def test_unknown_node_is_rejected_with_candidates(
        self, repo: DocumentRepo
    ) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    call_tool("rewrite_text", {"nid": "blt_ghost", "value": "x"}),
                    done("tool_calls"),
                ),
                turn(say("Sorry."), done("stop")),
            ]
        )
        result, final, channel = await run_turn(repo, backend, "fix it")

        rejections = emitted(channel, ev.PatchRejected)
        assert rejections and rejections[0].code == "unknown_node"
        assert result.applied == 0
        assert final.doc.experience[0].bullets[0].text == "Rebuilt the ledger."

    async def test_stale_expect_returns_the_actual_text(
        self, repo: DocumentRepo
    ) -> None:
        """Models fix this on retry roughly nine times in ten, but only if we
        hand back the real value."""
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "rewrite_text",
                        {"nid": BULLET_A, "value": "x", "expect": "something else"},
                    ),
                    done("tool_calls"),
                ),
                turn(say("Let me look again."), done("stop")),
            ]
        )
        _, _, channel = await run_turn(repo, backend, "fix it")

        rejection = emitted(channel, ev.PatchRejected)[0]
        assert rejection.code == "stale_expect"
        assert rejection.hint["actual"] == "Rebuilt the ledger."

    async def test_good_edits_survive_a_bad_one(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    call_tool("rewrite_text", {"nid": BULLET_A, "value": "Good."}, index=0),
                    call_tool("rewrite_text", {"nid": "blt_ghost", "value": "Bad."}, index=1),
                    done("tool_calls"),
                ),
                turn(say("Partly done."), done("stop")),
            ]
        )
        result, final, _ = await run_turn(repo, backend, "edit")

        assert result.status == "partial"
        assert result.applied == 1 and result.rejected == 1
        assert final.doc.experience[0].bullets[0].text == "Good."

    async def test_prose_with_no_tool_call_ends_the_turn(
        self, repo: DocumentRepo
    ) -> None:
        backend = ScriptedBackend([turn(say("Your resume looks good."), done("stop"))])
        result, final, _ = await run_turn(repo, backend, "how does it look")

        assert result.status == "ok"
        assert result.applied == 0
        assert final.version == 1


class TestSafety:
    async def test_identity_edit_is_made_and_reported(
        self, repo: DocumentRepo
    ) -> None:
        """A Tier C change happens, and the turn says so afterwards.

        This used to stop and ask. The dialog was the wrong shape for the
        request it interrupted -- somebody who asks for a retarget has already
        answered the question, and being asked it four more times mid-turn is
        the assistant refusing to believe them. What made asking look necessary
        was the fear of a change that could not be taken back, and the turn
        checkpoint had already made that untrue.
        """
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "set_personal_info",
                        {"field": "email", "value": "invented@example.com"},
                    ),
                    done("tool_calls"),
                ),
                turn(say("Shall I?"), done("stop")),
            ]
        )
        result, final, channel = await run_turn(
            repo, backend, "make my contact details more professional"
        )

        assert not emitted(channel, ev.ConfirmRequired)
        assert result.applied == 1
        assert final.doc.personal.email == "invented@example.com"

        # Reported once, over a finished document, with one undo behind it.
        notice = [
            warning
            for warning in emitted(channel, ev.Warning)
            if warning.source == "identity"
        ]
        assert len(notice) == 1
        # An address the user never typed is exactly what this is for.
        assert "without being named in your instructions" in notice[0].message

    async def test_identity_edit_the_user_typed_is_auto_approved(
        self, repo: DocumentRepo
    ) -> None:
        """If they typed the address, asking again is friction, not safety."""
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "set_personal_info",
                        {"field": "email", "value": "alex.morgan@newmail.com"},
                    ),
                    done("tool_calls"),
                ),
                turn(say("Updated."), done("stop")),
            ]
        )
        result, final, channel = await run_turn(
            repo, backend, "change my email to alex.morgan@newmail.com"
        )

        assert not emitted(channel, ev.ConfirmRequired)
        assert result.applied == 1
        assert final.doc.personal.email == "alex.morgan@newmail.com"

    async def test_ungrounded_skill_is_added_and_flagged(
        self, repo: DocumentRepo
    ) -> None:
        """The skill goes in; the fact that nothing supports it is recorded.

        Refusing was the engine overruling both the person, who asked in plain
        words, and the model, which can see the whole résumé and knows whether
        Kubernetes is fair for somebody who ran the cluster migration. What the
        engine does better than either is remember exactly which lines went in
        unsupported, and show them.
        """
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "add_skill",
                        {"skill": "Kubernetes", "evidence": "user_request"},
                    ),
                    done("tool_calls"),
                ),
                turn(say("I cannot add that."), done("stop")),
            ]
        )
        result, final, channel = await run_turn(repo, backend, "make my skills better")

        assert result.applied == 1
        assert "Kubernetes" in {item.text for item in final.doc.skills[0].items}

        notice = [
            warning
            for warning in emitted(channel, ev.Warning)
            if warning.source == "grounding"
        ]
        assert len(notice) == 1
        assert "Kubernetes" in notice[0].message
        assert "you will be asked about them" in notice[0].message

    async def test_skill_the_user_asked_for_is_added(
        self, repo: DocumentRepo
    ) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "add_skill", {"skill": "Rust", "evidence": "user_request"}
                    ),
                    done("tool_calls"),
                ),
                turn(say("Added."), done("stop")),
            ]
        )
        result, final, _ = await run_turn(repo, backend, "add Rust to my skills")

        assert result.applied == 1
        assert "Rust" in {item.text for item in final.doc.skills[0].items}

    async def test_removal_the_user_asked_for_sticks(
        self, repo: DocumentRepo
    ) -> None:
        """The old engine's net re-added anything removed, so this is the
        regression that scoping guards to intent was built to fix."""
        backend = ScriptedBackend(
            [
                turn(call_tool("remove_skill", {"skill": "Python"}), done("tool_calls")),
                turn(say("Removed."), done("stop")),
            ]
        )
        result, final, _ = await run_turn(repo, backend, "remove Python from my skills")

        assert result.applied == 1
        assert "Python" not in {item.text for item in final.doc.skills[0].items}

    async def test_a_turn_is_one_undo(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    call_tool("rewrite_text", {"nid": BULLET_A, "value": "One."}, index=0),
                    call_tool("rewrite_text", {"nid": BULLET_B, "value": "Two."}, index=1),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        state = await repo.create(make_doc())
        runner = TurnRunner(repo=repo, backend=backend)
        channel = TurnChannel(turn_id="t1", document_id=state.id)
        result = await runner.run(
            TurnRequest(document_id=state.id, message="tighten both"), channel
        )

        reverted, _ = await repo.revert(state.id, result.checkpoint_id)
        assert reverted.doc.experience[0].bullets[0].text == "Rebuilt the ledger."
        assert reverted.doc.experience[0].bullets[1].text == "Led the migration."


class TestBudgets:
    """What a limit is allowed to do.

    One rule governs all of these: a limit stops the turn only when there is
    nothing good left to do. The rest let the work finish and say what happened,
    because a half-rewritten resume is worse than either outcome it sits between
    -- and the turn already takes a checkpoint, so the whole thing is one undo.
    """

    async def test_a_turn_that_never_acts_is_stopped(self, repo: DocumentRepo) -> None:
        """The read-tool loop: call a search, ignore it, call it again."""
        reading = turn(call_tool("read_document", {}), done("tool_calls"))
        backend = ScriptedBackend([reading for _ in range(20)])

        result, _, channel = await run_turn(
            repo, backend, "look at it", budget=TurnBudget(max_stalled_rounds=2)
        )

        assert result.status == "partial"
        assert backend.calls <= 4
        assert any(
            "looking things up" in event.message
            for event in emitted(channel, ev.Warning)
        )

    async def test_a_turn_that_keeps_working_is_never_cut_off(
        self, repo: DocumentRepo
    ) -> None:
        """The regression this whole module was rewritten for.

        A model working steadily through a resume was stopped two thirds of the
        way through by a cap counting *rounds* rather than progress. Editing
        something resets the stall counter, so a turn that is getting somewhere
        runs until it is done however long that takes -- the wall clock is what
        catches a genuine hang.
        """
        edits = [
            turn(
                call_tool("rewrite_text", {"nid": BULLET_A, "value": f"round {index}"}),
                done("tool_calls"),
            )
            for index in range(8)
        ]
        result, final, _ = await run_turn(
            repo,
            backend := ScriptedBackend([*edits, turn(say("Done."), done("stop"))]),
            "work through it",
            budget=TurnBudget(max_stalled_rounds=2),
        )

        assert result.status == "ok"
        assert result.applied == 8
        assert backend.calls == 9
        assert final.doc.experience[0].bullets[0].text == "round 7"

    async def test_a_large_turn_finishes_and_says_so(self, repo: DocumentRepo) -> None:
        """Size is reported, never refused.

        The old cap stopped at its limit and left the document half-done under
        "Some changes could not be applied". Now every edit lands and the user
        judges a finished document with one undo behind it.
        """
        backend = ScriptedBackend(
            [
                turn(
                    *[
                        call_tool(
                            "rewrite_text",
                            {"nid": BULLET_A, "value": f"v{index}"},
                            index=index,
                        )
                        for index in range(6)
                    ],
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        result, _, channel = await run_turn(
            repo, backend, "rewrite", budget=TurnBudget(max_ops=2)
        )

        assert result.status == "ok"
        assert result.applied == 6
        assert any(
            "6 changes" in event.message and "Undo" in event.message
            for event in emitted(channel, ev.Warning)
        )

    async def test_a_wholesale_rewrite_is_reported_not_refused(
        self, repo: DocumentRepo
    ) -> None:
        backend = ScriptedBackend(
            [
                turn(
                    call_tool("rewrite_text", {"nid": BULLET_A, "value": "one"}),
                    call_tool("rewrite_text", {"nid": BULLET_B, "value": "two"}, index=1),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        result, final, channel = await run_turn(
            repo, backend, "rewrite it all", budget=TurnBudget(max_touch_ratio=0.01)
        )

        assert result.status == "ok"
        assert final.doc.experience[0].bullets[0].text == "one"
        assert final.doc.experience[0].bullets[1].text == "two"
        assert any(
            "rewrote" in event.message for event in emitted(channel, ev.Warning)
        )


class TestCancellation:
    async def test_cancel_stops_the_turn(self, repo: DocumentRepo) -> None:
        backend = ScriptedBackend(
            [turn(say("thinking about it at length"), done("stop"))]
        )
        state = await repo.create(make_doc())
        runner = TurnRunner(repo=repo, backend=backend)
        channel = TurnChannel(turn_id="t1", document_id=state.id)
        channel.cancel()

        result = await runner.run(
            TurnRequest(document_id=state.id, message="go"), channel
        )
        assert result.status == "cancelled"


class TestProviderFailure:
    async def test_a_provider_error_ends_cleanly(self, repo: DocumentRepo) -> None:
        from studio.llm.backend import BackendError

        backend = ScriptedBackend([], fail_with=BackendError("connection refused"))
        result, final, channel = await run_turn(repo, backend, "go")

        assert result.status == "failed"
        errors = emitted(channel, ev.ErrorEvent)
        assert errors and errors[0].code == "provider_error"
        # A failed turn must still terminate the stream, or the client hangs.
        assert emitted(channel, ev.Done)
        assert final.version == 1


class TestIdentityNoticeOnlyForSurprises:
    """The notice is for identity edits nobody asked for.

    Fired on every Tier C call it said the opposite of something useful. Asked
    to add two named jobs -- "add Geo TV, I worked there as a System Developer &
    Analyst" -- it reported back that the turn had "changed your identity or
    history", which is a warning about the instruction the person had just
    typed. Noise there teaches people to ignore it in the case it exists for.
    """

    def _request(self, message: str, history: list[dict[str, str]] | None = None):
        return TurnRequest(
            document_id="d1", message=message, history=history or []
        )

    def test_a_job_the_person_named_is_not_reported(self) -> None:
        """The exact case that made the notice noise."""
        spec = REGISTRY.get("add_experience")
        args = spec.Args(
            title="System Developer & Analyst",
            company="Geo TV",
            years="Sep 2025 - Present",
            bullets=["Built internal tools, including content management systems."],
        )

        assert _was_asked_for(
            args,
            self._request(
                "add Geo TV, I worked there as a System Developer & Analyst"
            ),
        )

    def test_the_bullets_it_wrote_are_not_held_against_it(self) -> None:
        """Wording is the assistant's job; the claim is the employer and title.

        A bullet is never verbatim -- "I built many internal tools" becomes
        "Built internal tools, including content management systems" -- so
        requiring every argument to appear would flag every addition.
        """
        spec = REGISTRY.get("add_experience")
        args = spec.Args(
            title="Business Planning Analyst",
            company="Loreal Paris",
            years="Apr 2026 - Jul 2026",
            bullets=["Unified data from 3+ sources."],
        )

        assert _was_asked_for(
            args,
            self._request(
                "add Loreal Paris, I was a Business Planning Analyst there"
            ),
        )

    def test_details_given_in_an_earlier_turn_still_count(self) -> None:
        """Facts arrive across turns: the employer in one, the dates in the next.

        Checking only the latest message would flag a job named two turns ago,
        which is the same failure as the assistant asking again for dates it had
        already been given.
        """
        spec = REGISTRY.get("set_entry_identity")
        args = spec.Args(nid=EXP, field="company", value="Loreal Paris")

        assert _was_asked_for(
            args,
            self._request(
                "yes the roles overlapped",
                history=[{"role": "user", "content": "also add Loreal Paris"}],
            ),
        )

    def test_a_value_the_person_never_typed_is_reported(self) -> None:
        spec = REGISTRY.get("set_personal_info")
        args = spec.Args(field="email", value="invented@example.com")

        assert not _was_asked_for(
            args, self._request("make my contact details more professional")
        )

    def test_a_removal_is_always_reported(self) -> None:
        """It names no values, and it is the most consequential thing here."""
        spec = REGISTRY.get("remove_entry")
        args = spec.Args(nid=EXP, reason="asked")

        assert not _was_asked_for(args, self._request("drop the Contoso job"))

    def test_matching_ignores_case(self) -> None:
        spec = REGISTRY.get("set_entry_identity")
        args = spec.Args(nid=EXP, field="company", value="Geo TV")

        assert _was_asked_for(args, self._request("add geo tv please"))


class TestDashesNeverReachTheDocument:
    """The prompt asks; the engine enforces.

    A style rule in a prompt is advisory, and a model under pressure ignores
    it. This is the half that does not.
    """

    async def test_a_rewrite_is_cleaned_on_the_way_in(
        self, repo: DocumentRepo
    ) -> None:
        state = await repo.create(make_doc(), title="mine")
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "rewrite_text",
                        {
                            "nid": BULLET_A,
                            "value": "Rebuilt the ledger — and cut latency 96%.",
                        },
                    ),
                    done("tool_calls"),
                ),
                turn(say("Done."), done("stop")),
            ]
        )
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        await runner.run(
            TurnRequest(document_id=state.id, message="tighten it"),
            TurnChannel(turn_id="t1", document_id=state.id),
        )
        final = await repo.get(state.id)

        text = final.doc.experience[0].bullets[0].text
        assert "—" not in text
        assert text == "Rebuilt the ledger, and cut latency 96%."

    async def test_a_whole_added_job_is_cleaned(self, repo: DocumentRepo) -> None:
        """Including the bullets nested inside it."""
        state = await repo.create(make_doc(), title="mine")
        backend = ScriptedBackend(
            [
                turn(
                    call_tool(
                        "add_experience",
                        {
                            "title": "Analyst — Systems",
                            "company": "Geo TV",
                            "years": "Sep 2025 — Present",
                            "bullets": ["Built the tools — and ran them."],
                        },
                    ),
                    done("tool_calls"),
                ),
                turn(say("Added."), done("stop")),
            ]
        )
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        await runner.run(
            TurnRequest(document_id=state.id, message="add Geo TV"),
            TurnChannel(turn_id="t2", document_id=state.id),
        )
        final = await repo.get(state.id)

        added = next(e for e in final.doc.experience if e.company == "Geo TV")
        assert added.title == "Analyst, Systems"
        # A range keeps a hyphen; a comma between two endpoints is nonsense.
        assert added.years == "Sep 2025 - Present"
        assert added.bullets[0].text == "Built the tools, and ran them."

    async def test_a_persons_own_typing_is_left_alone(
        self, repo: DocumentRepo
    ) -> None:
        """The same character from the keyboard is a choice.

        Rewriting it would be the application disagreeing with its user about
        their own writing.
        """
        state = await repo.create(make_doc(), title="mine")
        from studio.doc.apply import OpContext
        from studio.doc.ops import SetText

        await repo.apply(
            state.id,
            [SetText(nid=BULLET_A, value="I typed this — deliberately.")],
            ctx=OpContext(granted_tiers={"A", "B", "C"}, actor="user"),
        )
        final = await repo.get(state.id)

        assert final.doc.experience[0].bullets[0].text == "I typed this — deliberately."
