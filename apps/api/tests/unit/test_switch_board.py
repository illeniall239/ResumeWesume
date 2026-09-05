"""Working on a version other than the one that is open, and renaming one.

The other half of the roster. Naming the versions is what lets somebody say
"add Rust to the Stripe one"; ``switch_board`` is what makes it reach. Without
it the assistant can see the other versions and can only answer that it cannot
get to them.

What it forces: a turn can now touch more than one version, so undoing that
turn has to put back every board it reached rather than the last it happened to
be on.
"""

from __future__ import annotations

import pytest

from studio.agent.budget import TurnBudget
from studio.agent.loop import TurnRequest, TurnRunner, _board_named
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


def make_doc(bullet: str = "Rebuilt the ledger.") -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid="exp_11111",
                title="Senior Engineer",
                company="Northwind",
                years="2021 - Present",
                bullets=[TextNode(nid=BULLET, text=bullet)],
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


async def a_canvas(repo: DocumentRepo):
    """A résumé kept in two versions. Returns (general, stripe)."""
    general = await repo.create(make_doc(), title="Alex Morgan")
    stripe = await repo.create(
        make_doc(), title="Stripe - Payments", canvas_id=general.canvas_id
    )
    return general, stripe


def emitted(channel: TurnChannel, kind: type) -> list:
    return [event for event in channel.replay_from(0) if isinstance(event, kind)]


EDIT = turn(
    call_tool("rewrite_text", {"nid": BULLET, "text": "Owned the ledger."}, index=0),
    done("tool_calls"),
)
FINISH = turn(say("Done."), done("stop"))


class TestReachingAnotherVersion:
    async def test_the_edit_lands_on_the_version_that_was_named(
        self, repo: DocumentRepo
    ) -> None:
        general, stripe = await a_canvas(repo)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(
                        call_tool("switch_board", {"name": "Stripe - Payments"}),
                        done("tool_calls"),
                    ),
                    EDIT,
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=general.id, message="add that to the Stripe one"),
            TurnChannel(turn_id="t", document_id=general.id),
        )

        assert (await repo.get(stripe.id)).doc.experience[0].bullets[0].text == (
            "Owned the ledger."
        )
        # And the version the turn started on is untouched.
        assert (await repo.get(general.id)).doc.experience[0].bullets[0].text == (
            "Rebuilt the ledger."
        )

    async def test_the_client_is_told_to_follow(self, repo: DocumentRepo) -> None:
        general, stripe = await a_canvas(repo)
        channel = TurnChannel(turn_id="t", document_id=general.id)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(call_tool("switch_board", {"name": "Stripe"}), done("tool_calls")),
                    EDIT,
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=general.id, message="the Stripe one"), channel
        )

        switched = emitted(channel, ev.BoardSwitched)
        assert len(switched) == 1
        assert switched[0].board_id == stripe.id
        assert switched[0].title == "Stripe - Payments"

    async def test_a_name_that_matches_nothing_says_what_there_is(
        self, repo: DocumentRepo
    ) -> None:
        # A rejection that only says no gets retried unchanged; naming the
        # versions is what lets the model pick a real one.
        general, _ = await a_canvas(repo)
        channel = TurnChannel(turn_id="t", document_id=general.id)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(call_tool("switch_board", {"name": "Datadog"}), done("tool_calls")),
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(TurnRequest(document_id=general.id, message="x"), channel)

        assert not emitted(channel, ev.BoardSwitched)

    async def test_it_hands_back_the_ids_of_the_version_it_moved_to(
        self, repo: DocumentRepo
    ) -> None:
        """The ids the model is holding belong to the version it just left.

        Two résumés written separately share no node ids, so without this every
        edit after a switch is rejected against a node that is not there. It
        was: the first end-to-end run of this came back `applied: 1,
        rejected: 1`, and the rejected one was the whole point of the turn.
        """
        general = await repo.create(make_doc(), title="Alex Morgan")
        # Its own ids, which is what a résumé written separately rather than
        # forked from this one actually has -- an import, or a second template.
        theirs = "blt_zzzzz"
        elsewhere = make_doc("Ran the rota.")
        elsewhere.experience[0].bullets[0].nid = theirs
        other = await repo.create(
            elsewhere, title="Stripe - Payments", canvas_id=general.canvas_id
        )
        assert theirs != BULLET

        backend = ScriptedBackend(
            [
                turn(call_tool("switch_board", {"name": "Stripe"}), done("tool_calls")),
                FINISH,
            ]
        )
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        await runner.run(
            TurnRequest(document_id=general.id, message="the Stripe one"),
            TurnChannel(turn_id="t", document_id=general.id),
        )

        # The tool's own answer carries that version's outline.
        answered = "\n".join(
            str(message.get("content", ""))
            for request in backend.received
            for message in request.get("messages", [])
            if message.get("role") == "tool"
        )
        assert theirs in answered
        assert "Ran the rota." in answered

    async def test_switching_to_the_one_already_open_is_answered_not_refused(
        self, repo: DocumentRepo
    ) -> None:
        # The model asked for something true. Refusing sends it looking for
        # another way to get somewhere it already is.
        general, _ = await a_canvas(repo)
        channel = TurnChannel(turn_id="t", document_id=general.id)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(
                        call_tool("switch_board", {"name": "Alex Morgan"}),
                        done("tool_calls"),
                    ),
                    EDIT,
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        result = await runner.run(
            TurnRequest(document_id=general.id, message="x"), channel
        )

        assert result.rejected == 0
        assert (await repo.get(general.id)).doc.experience[0].bullets[0].text == (
            "Owned the ledger."
        )


class TestUndoingATurnThatMoved:
    async def test_every_version_it_touched_can_be_put_back(
        self, repo: DocumentRepo
    ) -> None:
        """The reason the checkpoints are a list.

        A turn that edits two versions changed two documents. Keeping one
        snapshot would put back the one that happened to be last and leave the
        other quietly edited.
        """
        general, stripe = await a_canvas(repo)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    EDIT,
                    turn(call_tool("switch_board", {"name": "Stripe"}), done("tool_calls")),
                    EDIT,
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=general.id, message="both of them"),
            TurnChannel(turn_id="turn-both", document_id=general.id),
        )

        assert (await repo.get(general.id)).doc.experience[0].bullets[0].text == (
            "Owned the ledger."
        )
        assert (await repo.get(stripe.id)).doc.experience[0].bullets[0].text == (
            "Owned the ledger."
        )

        # Both snapshots are recorded against the one turn.
        snapshots = await repo.turn_checkpoints_for_canvas(general.canvas_id)
        assert len(snapshots["turn-both"]) == 2
        assert {document_id for _, _, document_id in snapshots["turn-both"]} == {
            general.id,
            stripe.id,
        }

        for checkpoint_id, _, document_id in snapshots["turn-both"]:
            await repo.revert(document_id, checkpoint_id)

        assert (await repo.get(general.id)).doc.experience[0].bullets[0].text == (
            "Rebuilt the ledger."
        )
        assert (await repo.get(stripe.id)).doc.experience[0].bullets[0].text == (
            "Rebuilt the ledger."
        )


class TestRenaming:
    async def test_the_version_takes_the_new_name(self, repo: DocumentRepo) -> None:
        general, _ = await a_canvas(repo)
        channel = TurnChannel(turn_id="t", document_id=general.id)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(
                        call_tool("rename_board", {"name": "Backend, senior"}),
                        done("tool_calls"),
                    ),
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=general.id, message="call it something better"),
            channel,
        )

        assert (await repo.get(general.id)).title == "Backend, senior"
        renamed = emitted(channel, ev.BoardRenamed)
        assert len(renamed) == 1 and renamed[0].title == "Backend, senior"

    async def test_it_renames_the_version_being_edited_not_the_one_it_started_on(
        self, repo: DocumentRepo
    ) -> None:
        general, stripe = await a_canvas(repo)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(call_tool("switch_board", {"name": "Stripe"}), done("tool_calls")),
                    turn(
                        call_tool("rename_board", {"name": "Stripe - Ledger"}),
                        done("tool_calls"),
                    ),
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=general.id, message="rename the Stripe one"),
            TurnChannel(turn_id="t", document_id=general.id),
        )

        assert (await repo.get(stripe.id)).title == "Stripe - Ledger"
        assert (await repo.get(general.id)).title == "Alex Morgan"

    async def test_a_blank_name_is_refused(self, repo: DocumentRepo) -> None:
        general, _ = await a_canvas(repo)
        channel = TurnChannel(turn_id="t", document_id=general.id)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(call_tool("rename_board", {"name": "   "}), done("tool_calls")),
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(TurnRequest(document_id=general.id, message="x"), channel)

        assert (await repo.get(general.id)).title == "Alex Morgan"
        assert not emitted(channel, ev.BoardRenamed)


class TestMatchingAName:
    """The name comes back through a model that read it off a roster, and is as
    likely to say "Stripe" as "Stripe - Payments"."""

    class Board:
        def __init__(self, id: str, title: str) -> None:
            self.id, self.title = id, title

    def test_an_exact_name_wins(self) -> None:
        boards = [self.Board("a", "Stripe"), self.Board("b", "Stripe - Payments")]
        assert _board_named(boards, "Stripe").id == "a"

    def test_a_partial_name_is_enough_when_only_one_answers(self) -> None:
        boards = [self.Board("a", "Alex Morgan"), self.Board("b", "Stripe - Payments")]
        assert _board_named(boards, "stripe").id == "b"
        assert _board_named(boards, "Stripe - Payments Engineer").id == "b"

    def test_an_ambiguous_name_matches_nothing(self) -> None:
        # Editing the wrong résumé is worse than asking again.
        boards = [
            self.Board("a", "Stripe - Payments"),
            self.Board("b", "Stripe - Platform"),
        ]
        assert _board_named(boards, "Stripe") is None

    def test_nothing_matches_nothing(self) -> None:
        assert _board_named([self.Board("a", "Alex")], "") is None
        assert _board_named([], "Stripe") is None


class TestTheGuardBaselineMovesToo:
    """The drift guards compare the document before a turn against the document
    after it. Switching changes which document "after" refers to, so the
    baseline has to move with it or they compare one version against another --
    and every difference between the two reads as something the assistant just
    wrote."""

    async def test_a_longer_version_is_not_read_as_a_turn_of_padding(
        self, repo: DocumentRepo
    ) -> None:
        # The versions differ in length, which is the case that separates the
        # two baselines. Measured against the short one, editing the long one
        # looks like a turn that tripled the résumé.
        short = await repo.create(make_doc("Ran the rota."), title="Short")
        long_text = (
            "Owned the payments ledger end to end, the service that records "
            "every movement of money through the platform and the one nobody "
            "is allowed to get wrong, across three regions and two currencies."
        )
        long = await repo.create(
            make_doc(long_text), title="Long", canvas_id=short.canvas_id
        )

        channel = TurnChannel(turn_id="t", document_id=short.id)
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(call_tool("switch_board", {"name": "Long"}), done("tool_calls")),
                    turn(
                        call_tool(
                            "rewrite_text",
                            {"nid": BULLET, "text": long_text.replace("three", "four")},
                        ),
                        done("tool_calls"),
                    ),
                    FINISH,
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=short.id, message="edit the long one"), channel
        )

        assert (await repo.get(long.id)).doc.experience[0].bullets[0].text.endswith(
            "four regions and two currencies."
        )
        grew = [
            warning.message
            for warning in emitted(channel, ev.Warning)
            if "grew" in warning.message
        ]
        assert not grew, grew
