"""The node the assistant is writing to is closed while it writes.

A tool call's arguments arrive as a stream of JSON fragments, so the page shows
the new text landing in a node a second or more before the edit itself lands.
Both halves of that second are real: the words on screen are not in the
document yet, and the person can still type into the node they are appearing
in. Whichever write lands second wins, and neither of them knows it happened.

The event for saying so was declared and never sent. The browser had the whole
receiving half -- a lock set, the prop threaded to every editable node, the
styling, the test -- and the server never constructed a ``NodeLock``, so
nothing was ever closed. This is the wire that was missing, and the rule it has
to keep: whatever is locked while a call is in flight is open again once the
call settles, however it settles.
"""

from __future__ import annotations

import pytest

from studio.agent.budget import TurnBudget
from studio.agent.loop import TurnRequest, TurnRunner
from studio.doc.schema import (
    ExperienceNode,
    PersonalInfo,
    StudioDoc,
    TextNode,
)
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, turn
from studio.persistence.repo import DocumentRepo
from studio.streaming import events as ev
from studio.streaming.channel import TurnChannel

BULLET = "blt_aaaaa"
EXP = "exp_11111"


def make_doc() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", phone="+1-555-0142"),
        summary=TextNode(nid="sum_00001", text="Backend engineer."),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Senior Engineer",
                company="Northwind",
                bullets=[TextNode(nid=BULLET, text="Rebuilt the ledger.")],
            )
        ],
    )


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


async def run_turn(repo: DocumentRepo, backend: ScriptedBackend) -> TurnChannel:
    state = await repo.create(make_doc(), title="Alex Morgan")
    runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
    channel = TurnChannel(turn_id="turn-1", document_id=state.id)
    await runner.run(
        TurnRequest(document_id=state.id, message="tidy this up", consent_tokens=set()),
        channel,
    )
    return channel


def locks(channel: TurnChannel) -> list[tuple[bool, tuple[str, ...]]]:
    """Every lock and release, in order, as the browser receives them."""
    return [
        (event.locked, tuple(event.nids))
        for event in channel.replay_from(0)
        if isinstance(event, ev.NodeLock)
    ]


def still_held(channel: TurnChannel) -> set[str]:
    """What is closed for editing once the turn is over."""
    held: set[str] = set()
    for locked, nids in locks(channel):
        held.update(nids) if locked else held.difference_update(nids)
    return held


EDIT = turn(
    call_tool("rewrite_text", {"nid": BULLET, "value": "Cut settlement latency 96%."}),
    done("stop"),
)
FINISH = turn(say("Done."), done("stop"))


class TestWhileTheCallIsInFlight:
    async def test_the_node_being_written_to_is_closed(
        self, repo: DocumentRepo
    ) -> None:
        channel = await run_turn(repo, ScriptedBackend([EDIT, FINISH]))

        assert (True, (BULLET,)) in locks(channel)

    async def test_it_is_closed_before_the_edit_lands(
        self, repo: DocumentRepo
    ) -> None:
        # The whole point of the lock is the gap between the two. Closing on
        # the patch would be closing a node the moment it stops being written.
        stream = channel_order(await run_turn(repo, ScriptedBackend([EDIT, FINISH])))

        assert stream.index("lock") < stream.index("patch")

    async def test_a_field_is_closed_by_its_own_path(
        self, repo: DocumentRepo
    ) -> None:
        # `set_personal_info` writes `personal.phone`, and that is how the
        # browser keys the draft too -- so the lock has to name the same thing
        # or it would freeze an entry to protect one field of it.
        call = turn(
            call_tool(
                "set_personal_info", {"field": "phone", "value": "+1-555-0199"}
            ),
            done("stop"),
        )
        channel = await run_turn(repo, ScriptedBackend([call, FINISH]))

        assert any(
            locked and nids == ("personal.phone",) for locked, nids in locks(channel)
        )


class TestWhenItSettles:
    async def test_an_applied_edit_opens_the_node_again(
        self, repo: DocumentRepo
    ) -> None:
        channel = await run_turn(repo, ScriptedBackend([EDIT, FINISH]))

        assert still_held(channel) == set()

    async def test_a_rejected_edit_opens_it_too(self, repo: DocumentRepo) -> None:
        # The failure that matters most: an edit that does not land must not
        # leave the node it aimed at unusable. Nothing would ever unlock it.
        call = turn(
            call_tool("rewrite_text", {"nid": "blt_zzzzz", "value": "Nowhere."}),
            done("stop"),
        )
        channel = await run_turn(repo, ScriptedBackend([call, FINISH]))

        assert still_held(channel) == set()

    async def test_a_call_that_never_balances_opens_it_too(
        self, repo: DocumentRepo
    ) -> None:
        # Truncated mid-write: the draft named a node, the arguments never
        # closed, and the call is executed as a leftover.
        truncated = call_tool(
            "rewrite_text", {"nid": BULLET, "value": "Cut settlement latency"}
        )[:-1]
        channel = await run_turn(
            repo, ScriptedBackend([turn(truncated, done("stop")), FINISH])
        )

        assert still_held(channel) == set()

    async def test_nothing_is_held_after_the_turn_ends(
        self, repo: DocumentRepo
    ) -> None:
        two = turn(
            call_tool("rewrite_text", {"nid": BULLET, "value": "One."}, index=0),
            call_tool("rewrite_text", {"nid": "sum_00001", "value": "Two."}, index=1),
            done("stop"),
        )
        channel = await run_turn(repo, ScriptedBackend([two, FINISH]))

        assert still_held(channel) == set()


def channel_order(channel: TurnChannel) -> list[str]:
    """The events that matter here, in the order they went out."""
    names = []
    for event in channel.replay_from(0):
        if isinstance(event, ev.NodeLock) and event.locked:
            names.append("lock")
        elif isinstance(event, ev.PatchApplied):
            names.append("patch")
    return names
