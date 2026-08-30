"""The turn loop, end to end, with no network.

Every test drives the real loop, the real gates, the real guards and real
persistence. Only the model is scripted, which is the whole point of the port
in ``llm/backend.py``: if the seam were any higher, these would be testing
mocks instead of behaviour.
"""

from __future__ import annotations

import pytest

from studio.agent.budget import TurnBudget
from studio.agent.loop import TurnRequest, TurnRunner
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
    async def test_identity_edit_without_consent_pauses(
        self, repo: DocumentRepo
    ) -> None:
        """A Tier C change the user did not spell out must ask first."""
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

        assert emitted(channel, ev.ConfirmRequired)
        assert result.applied == 0
        assert final.doc.personal.email == "alex@example.com"

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

    async def test_ungrounded_skill_is_refused(self, repo: DocumentRepo) -> None:
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

        rejection = emitted(channel, ev.PatchRejected)[0]
        assert rejection.code == "not_grounded"
        assert result.applied == 0
        assert "Kubernetes" not in {
            item.text for item in final.doc.skills[0].items
        }

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

        reverted = await repo.revert(state.id, result.checkpoint_id)
        assert reverted.doc.experience[0].bullets[0].text == "Rebuilt the ledger."
        assert reverted.doc.experience[0].bullets[1].text == "Led the migration."


class TestBudgets:
    async def test_iteration_budget_stops_a_loop(self, repo: DocumentRepo) -> None:
        """A model that keeps reading and never acts must not run forever."""
        reading = turn(call_tool("read_document", {}), done("tool_calls"))
        backend = ScriptedBackend([reading for _ in range(20)])

        result, _, channel = await run_turn(
            repo, backend, "look at it", budget=TurnBudget(max_iterations=3)
        )

        assert result.status == "partial"
        assert backend.calls <= 4
        assert any(
            "rounds" in event.message for event in emitted(channel, ev.Warning)
        )

    async def test_op_budget_caps_a_runaway(self, repo: DocumentRepo) -> None:
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
        result, _, _ = await run_turn(
            repo, backend, "rewrite", budget=TurnBudget(max_ops=2)
        )
        assert result.status == "partial"
        assert result.applied <= 3


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
