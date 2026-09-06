"""Tailoring makes a copy and narrows that, rather than narrowing what you have.

A résumé cut down for one job is thin material for the next, and cutting it in
place means the general version is gone. So the assistant is asked to fork
first, and everything after the fork lands on the copy.

Three things have to move with it, and each is a real bug if it does not: the
board being edited, the checkpoint the turn can be undone to, and the baseline
the drift guards compare against. The last one is the quiet one — comparing a
fresh copy against the original's pre-turn document reads every line of the copy
as an edit the assistant just made.

These drive the real loop, the real gates and real persistence. Only the model
is scripted.
"""

from __future__ import annotations

import pytest

from studio.agent.budget import TurnBudget
from studio.agent.loop import TurnRequest, TurnRunner
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, turn
from studio.persistence.repo import DocumentRepo
from studio.streaming import events as ev
from studio.streaming.channel import TurnChannel
from studio.doc.schema import (
    ExperienceNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

BULLET = "blt_aaaaa"


def make_doc() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid="exp_11111",
                title="Senior Engineer",
                company="Northwind",
                years="2021 - Present",
                bullets=[TextNode(nid=BULLET, text="Rebuilt the ledger.")],
            )
        ],
        skills=[
            SkillGroup(
                nid="sgp_ggggg",
                key="technical",
                items=[SkillItem(nid="skl_ppppp", text="Python")],
            )
        ],
    )


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


async def run(repo: DocumentRepo, backend: ScriptedBackend, message: str):
    state = await repo.create(make_doc(), title="Alex Morgan")
    runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
    channel = TurnChannel(turn_id="turn-fork", document_id=state.id)
    result = await runner.run(
        TurnRequest(document_id=state.id, message=message), channel
    )
    canvas = await repo.get_canvas(state.canvas_id)
    return result, state, canvas, channel


def emitted(channel: TurnChannel, kind: type) -> list:
    return [event for event in channel.replay_from(0) if isinstance(event, kind)]


TAILOR = turn(
    call_tool("fork_board", {"name": "Stripe - Payments"}, index=0),
    done("tool_calls"),
)
EDIT = turn(
    call_tool(
        "rewrite_text",
        {"nid": BULLET, "text": "Owned the payments ledger end to end."},
        index=0,
    ),
    done("tool_calls"),
)
EDIT_AGAIN = turn(
    call_tool(
        "rewrite_text", {"nid": BULLET, "text": "Aimed at the Stripe role."}, index=0
    ),
    done("tool_calls"),
)
FINISH = turn(say("Tailored a copy for the Stripe role."), done("stop"))


class TestForkingBeforeTailoring:
    async def test_the_edit_lands_on_the_copy(self, repo: DocumentRepo) -> None:
        _, original, canvas, _ = await run(
            repo,
            ScriptedBackend([TAILOR, EDIT, FINISH]),
            "tailor this for a payments role at Stripe",
        )

        assert [board.title for board in canvas.boards] == [
            "Alex Morgan",
            "Stripe - Payments",
        ]
        edited = [b for b in canvas.boards if b.title == "Stripe - Payments"][0]
        assert edited.doc.experience[0].bullets[0].text == (
            "Owned the payments ledger end to end."
        )

    async def test_the_original_is_left_exactly_as_it_was(
        self, repo: DocumentRepo
    ) -> None:
        # The whole reason for forking. Tailoring in place is what destroys the
        # general version every other version is cut from.
        _, original, canvas, _ = await run(
            repo,
            ScriptedBackend([TAILOR, EDIT, FINISH]),
            "tailor this for Stripe",
        )

        kept = await repo.get(original.id)
        assert kept.doc.experience[0].bullets[0].text == "Rebuilt the ledger."
        assert kept.version == original.version

    async def test_the_client_is_told_to_follow(self, repo: DocumentRepo) -> None:
        # A page still showing the original would draw patches against a
        # document that never received them.
        _, original, canvas, channel = await run(
            repo, ScriptedBackend([TAILOR, EDIT, FINISH]), "tailor this"
        )

        forked = emitted(channel, ev.BoardForked)
        assert len(forked) == 1
        assert forked[0].title == "Stripe - Payments"
        assert forked[0].from_board == original.id
        assert forked[0].board_id != original.id

    async def test_undoing_the_turn_puts_the_copy_back_not_the_original(
        self, repo: DocumentRepo
    ) -> None:
        # A fresh copy has nothing to undo back to, and the original's snapshot
        # would put the user on the wrong document entirely.
        result, original, canvas, _ = await run(
            repo, ScriptedBackend([TAILOR, EDIT, FINISH]), "tailor this"
        )
        copy = [b for b in canvas.boards if b.title == "Stripe - Payments"][0]

        restored, _ = await repo.revert(copy.id, result.checkpoint_id)

        assert restored.doc.experience[0].bullets[0].text == "Rebuilt the ledger."
        # And the original never moved, so there was nothing there to restore.
        assert (await repo.get(original.id)).version == original.version

    async def test_the_posting_comes_with_it(self, repo: DocumentRepo) -> None:
        # A version aimed at a job is still aimed at it, and the next turn
        # reads that off the document.
        state = await repo.create(make_doc(), title="Alex Morgan")
        await repo.set_job_description(state.id, "Stripe. Payments. Kubernetes.")

        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend([TAILOR, EDIT, FINISH]),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=state.id, message="tailor this"),
            TurnChannel(turn_id="t", document_id=state.id),
        )

        canvas = await repo.get_canvas(state.canvas_id)
        copy = [b for b in canvas.boards if b.title == "Stripe - Payments"][0]
        assert copy.job_description == "Stripe. Payments. Kubernetes."


class TestTheGuardsFollowToo:
    """The drift guards compare the document before a turn against the document
    after it. A fork changes which document "after" refers to, so the baseline
    has to move with it or they are comparing one board against another."""

    async def test_a_plain_fork_and_edit_raises_nothing(
        self, repo: DocumentRepo
    ) -> None:
        _, _, _, channel = await run(
            repo, ScriptedBackend([TAILOR, EDIT, FINISH]), "tailor this"
        )

        warnings = [w.message for w in emitted(channel, ev.Warning)]
        assert not any("%" in message for message in warnings), warnings
        assert not emitted(channel, ev.DriftSuppressed)

    async def test_edits_made_before_the_fork_are_not_blamed_on_the_copy(
        self, repo: DocumentRepo
    ) -> None:
        """The case where the two baselines genuinely differ.

        Fork first is what the tool asks for, and when that happens the copy is
        identical to the original's pre-turn state and either baseline gives the
        same answer. It is the model editing *before* it forks that separates
        them: the copy then already carries those edits, and measuring it
        against the original's pre-turn document counts them twice — once
        against the board that received them, and again against the board that
        was born holding them.
        """
        state = await repo.create(make_doc(), title="Alex Morgan")
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend([EDIT, TAILOR, EDIT_AGAIN, FINISH]),
            budget=TurnBudget(),
        )
        channel = TurnChannel(turn_id="t", document_id=state.id)
        await runner.run(
            TurnRequest(document_id=state.id, message="tailor this"), channel
        )

        # The baseline the guards ran against is the copy as it was born, not
        # the original as it was before any of this.
        copied = [
            board
            for board in (await repo.get_canvas(state.canvas_id)).boards
            if board.title == "Stripe - Payments"
        ][0]
        assert runner._base_doc is not None  # noqa: SLF001
        assert (
            runner._base_doc.experience[0].bullets[0].text  # noqa: SLF001
            == "Owned the payments ledger end to end."
        )
        assert copied.doc.experience[0].bullets[0].text == "Aimed at the Stripe role."


class TestWhenThereIsNowhereToPutIt:
    async def test_a_board_with_no_canvas_refuses_rather_than_crashing(
        self, repo: DocumentRepo
    ) -> None:
        # Not reachable through the API — every document is created on a canvas
        # — but the loop should say so rather than raise.
        state = await repo.create(make_doc(), title="Alex Morgan")
        async with repo._engine.begin() as connection:  # noqa: SLF001
            from sqlalchemy import text

            await connection.execute(
                text("UPDATE documents SET canvas_id = NULL WHERE id = :id"),
                {"id": state.id},
            )

        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend([TAILOR, FINISH]),
            budget=TurnBudget(),
        )
        channel = TurnChannel(turn_id="t", document_id=state.id)
        result = await runner.run(
            TurnRequest(document_id=state.id, message="tailor this"), channel
        )

        # It answers rather than raising, and nothing was created. A turn whose
        # only call was refused is a failed turn, which is the loop's existing
        # and correct reading of that.
        assert result.rejected == 1
        assert result.applied == 0
        assert not emitted(channel, ev.BoardForked)
        assert len(await repo.list()) == 1


class TestSeveralVersionsAtOnce:
    """"Give me three versions" — three alternatives, each cut from the same
    résumé rather than each from the last."""

    async def test_each_copy_comes_from_the_original(
        self, repo: DocumentRepo
    ) -> None:
        # The bug this guards: forking from wherever the turn currently is means
        # the second copy is a copy of the first, already narrowed. Three
        # "alternatives" would then be one alternative narrowed three times,
        # each further from the résumé than the last.
        state = await repo.create(make_doc(), title="Alex Morgan")
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(call_tool("fork_board", {"name": "Punchy"}), done("tool_calls")),
                    EDIT,
                    turn(call_tool("fork_board", {"name": "Formal"}), done("tool_calls")),
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=state.id, message="give me two versions of this"),
            TurnChannel(turn_id="t", document_id=state.id),
        )

        canvas = await repo.get_canvas(state.canvas_id)
        by_name = {board.title: board for board in canvas.boards}
        assert set(by_name) == {"Alex Morgan", "Punchy", "Formal"}

        # The first copy carries the edit that was made to it.
        assert by_name["Punchy"].doc.experience[0].bullets[0].text == (
            "Owned the payments ledger end to end."
        )
        # The second is a fresh cut of the résumé, not of the first copy.
        assert by_name["Formal"].doc.experience[0].bullets[0].text == (
            "Rebuilt the ledger."
        )
        # And the résumé itself is untouched by any of it.
        assert by_name["Alex Morgan"].doc.experience[0].bullets[0].text == (
            "Rebuilt the ledger."
        )

    async def test_three_versions_do_not_trip_the_stall_limit(
        self, repo: DocumentRepo
    ) -> None:
        """A fork applies no ops, so counted as a stall it reads as the
        read-tool loop — and three in a row is exactly the limit. "Give me three
        versions" would then stop on the third before a word was written into
        it."""
        from studio.agent.budget import TurnBudget as Budget

        state = await repo.create(make_doc(), title="Alex Morgan")
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(call_tool("fork_board", {"name": "One"}), done("tool_calls")),
                    turn(call_tool("fork_board", {"name": "Two"}), done("tool_calls")),
                    turn(call_tool("fork_board", {"name": "Three"}), done("tool_calls")),
                    turn(call_tool("fork_board", {"name": "Four"}), done("tool_calls")),
                    FINISH,
                ]
            ),
            # The real limit, stated here so the test says what it is testing.
            budget=Budget(max_stalled_rounds=3),
        )
        result = await runner.run(
            TurnRequest(document_id=state.id, message="give me four versions"),
            TurnChannel(turn_id="t", document_id=state.id),
        )

        canvas = await repo.get_canvas(state.canvas_id)
        assert sorted(board.title for board in canvas.boards) == [
            "Alex Morgan",
            "Four",
            "One",
            "Three",
            "Two",
        ]
        assert result.status == "ok"
