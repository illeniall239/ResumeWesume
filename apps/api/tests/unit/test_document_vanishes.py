"""A turn whose document goes away while it is working.

Reported as eight identical failures in one turn: "add project -- The edit
could not be saved.", three reads that came back with an empty resume, and an
assistant that concluded the editor had lost its connection and offered to
paste the work back once it returned.

One condition produced both halves. ``_execute`` read the document at the top
of every tool call and, when the row was not there, carried on with an empty
``StudioDoc()``: so the read tools answered with a blank resume, and every
write went to ``repo.apply`` on an id that is not there, raised ``KeyError``,
and came back under the one sentence the loop had for any exception at all.
Nothing anywhere said the document was gone, and the model -- which cannot
recover from an empty document by trying harder -- retried until the stall
counter ended the turn.

The turn start has always refused a missing document. These are the same
refusal for one that goes missing mid-turn.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from studio.agent.loop import TurnRequest, TurnRunner
from studio.doc.schema import ExperienceNode, PersonalInfo, StudioDoc, TextNode
from studio.llm.backend import ModelChunk
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, turn
from studio.persistence.repo import DocumentRepo
from studio.streaming import events as ev
from studio.streaming.channel import TurnChannel

EXP = "exp_11111"
BULLET = "blt_aaaaa"


def make_doc() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Senior Engineer",
                company="Northwind",
                years="2021 - Present",
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


class Vanishing(ScriptedBackend):
    """Deletes the document between the first round and the second.

    The row really goes, through the repo the loop is holding -- not a patched
    ``get`` returning ``None``, which would only prove that a stub was called.
    """

    def __init__(self, turns, *, repo: DocumentRepo, document_id: str) -> None:
        super().__init__(turns)
        self._repo = repo
        self._document_id = document_id

    async def stream(
        self, messages: list[dict[str, Any]], **kwargs: Any
    ) -> AsyncIterator[ModelChunk]:
        # Once the first tool result is in the conversation, which is the round
        # after the first edit landed. Counting rounds would be tying the test
        # to how many times the loop happens to call the model -- the scope
        # probe is one of those, and it is not a round.
        if any(message.get("role") == "tool" for message in messages):
            await self._repo.delete(self._document_id)
        async for chunk in super().stream(messages, **kwargs):
            yield chunk


def emitted(channel: TurnChannel, kind: type) -> list:
    return [event for event in channel.replay_from(0) if isinstance(event, kind)]


class TestTheDocumentIsDeletedMidTurn:
    SCRIPT = [
        turn(
            call_tool("rewrite_text", {"nid": BULLET, "text": "Rebuilt the ledger twice."}),
            done("tool_calls"),
        ),
        turn(
            call_tool("add_project", {"name": "ResumeWesume", "role": "Founder"}),
            done("tool_calls"),
        ),
        turn(say("Done."), done("stop")),
    ]

    async def run(self, repo: DocumentRepo):
        state = await repo.create(make_doc(), title="Alex Morgan")
        backend = Vanishing(self.SCRIPT, repo=repo, document_id=state.id)
        runner = TurnRunner(repo=repo, backend=backend)
        channel = TurnChannel(turn_id="turn-1", document_id=state.id)
        result = await runner.run(
            TurnRequest(document_id=state.id, message="add my project"), channel
        )
        return result, channel

    async def test_the_turn_stops_and_says_the_document_is_gone(
        self, repo: DocumentRepo
    ) -> None:
        result, channel = await self.run(repo)

        assert result.status == "failed"
        errors = emitted(channel, ev.ErrorEvent)
        assert [error.code for error in errors] == ["not_found"]
        # Enough for somebody to know what happened to their work.
        assert "no longer open" in errors[0].message
        assert "Nothing was changed" in errors[0].message

    async def test_it_never_reports_the_edit_as_unsaveable(
        self, repo: DocumentRepo
    ) -> None:
        # The symptom that sent everyone looking in the wrong place. "The edit
        # could not be saved" describes a write that failed against a document
        # that is there, and invites exactly one response: try the write again.
        _, channel = await self.run(repo)

        rejections = emitted(channel, ev.PatchRejected)
        assert [entry.code for entry in rejections] == []

    async def test_it_stops_at_the_first_call_rather_than_retrying(
        self, repo: DocumentRepo
    ) -> None:
        # There is nothing to edit, so every further round is a round spent
        # failing. Eight of them is what the report was.
        _, channel = await self.run(repo)

        starts = emitted(channel, ev.ToolStart)
        assert [start.name for start in starts] == ["rewrite_text"]

    async def test_the_first_round_still_landed(self, repo: DocumentRepo) -> None:
        # The edit made before the document went away is not un-made by this,
        # and the turn is not retroactively a no-op -- it stopped, part done.
        _, channel = await self.run(repo)

        assert [event.ops for event in emitted(channel, ev.PatchApplied)] != []


class TestAWriteThatFailsForSomeOtherReason:
    """When the document is there and the write still cannot land."""

    class Broken(DocumentRepo):
        async def apply(self, *args: Any, **kwargs: Any):
            raise RuntimeError("database is locked")

    async def test_the_message_names_the_cause(self, repo: DocumentRepo) -> None:
        # One sentence for a locked database, a schema violation and a
        # disconnected disk is three problems with three remedies and no way to
        # tell which one is in front of you. The model reads this too: a named
        # cause is the difference between adapting and retrying the identical
        # call.
        store = self.Broken("sqlite+aiosqlite:///:memory:")
        await store.create_schema()
        try:
            state = await store.create(make_doc(), title="Alex Morgan")
            backend = ScriptedBackend(
                [
                    turn(
                        call_tool(
                            "rewrite_text",
                            {"nid": BULLET, "text": "Rebuilt the ledger twice."},
                        ),
                        done("tool_calls"),
                    ),
                    turn(say("Sorry."), done("stop")),
                ]
            )
            runner = TurnRunner(repo=store, backend=backend)
            channel = TurnChannel(turn_id="turn-1", document_id=state.id)
            await runner.run(
                TurnRequest(document_id=state.id, message="tighten that bullet"),
                channel,
            )

            rejections = emitted(channel, ev.PatchRejected)
            assert [entry.code for entry in rejections] == ["apply_failed"]
            assert "database is locked" in rejections[0].message
            assert "RuntimeError" in rejections[0].message
        finally:
            await store.dispose()


class TestTheSubscriptionPathHasADocumentToEdit:
    """The Agent SDK drives ``_execute`` directly, never entering ``run``.

    Which is where every field ``_execute`` depends on used to be set. The
    wrapper copied three of them by hand and missed ``_target``, so on a Claude
    subscription every tool call read ``repo.get("")``, found nothing, and
    worked against a document that was not there: reads answered with a blank
    resume, writes raised ``KeyError`` on the way to the database. Not an edge
    case -- that path could not write at all, for any request, on any resume.
    """

    async def test_begin_leaves_the_runner_pointed_at_the_document(
        self, repo: DocumentRepo
    ) -> None:
        state = await repo.create(make_doc(), title="Alex Morgan")
        runner = TurnRunner(repo=repo, backend=ScriptedBackend([]))
        assert runner._target == ""

        runner.begin(
            document_id=state.id, base_doc=state.doc, checkpoint_id="chk_0001"
        )

        assert runner._target == state.id
        assert runner._origin == state.id
        assert runner._base_doc is state.doc
        assert runner._checkpoints == {state.id: "chk_0001"}

    async def test_an_edit_driven_the_way_the_sdk_drives_one_lands(
        self, repo: DocumentRepo
    ) -> None:
        # `_execute` called straight, with no `run` around it: that is the SDK
        # path in one line, and it is the thing that was broken.
        from studio.agent.grounding import Grounder
        from studio.agent.loop import AssembledCall
        from studio.guards.grants import IntentLedger

        state = await repo.create(make_doc(), title="Alex Morgan")
        runner = TurnRunner(repo=repo, backend=ScriptedBackend([]))
        runner.begin(
            document_id=state.id, base_doc=state.doc, checkpoint_id="chk_0001"
        )
        channel = TurnChannel(turn_id="turn-1", document_id=state.id)

        applied, rejected, _ = await runner._execute(
            AssembledCall(
                call_id="c1",
                name="rewrite_text",
                raw_arguments="",
                arguments={"nid": BULLET, "text": "Rebuilt the ledger twice."},
            ),
            TurnRequest(document_id=state.id, message="tighten that bullet"),
            channel,
            IntentLedger(turn_id="turn-1"),
            Grounder.build(state.doc, user_message="tighten that bullet", jd_keywords=[]),
            {},
        )

        assert (applied, rejected) == (1, 0)
        after = await repo.get(state.id)
        assert after.doc.experience[0].bullets[0].text == "Rebuilt the ledger twice."

    def test_the_sdk_path_sets_no_turn_state_by_hand(self) -> None:
        # The shape of the bug, not one instance of it. Three fields were
        # copied across and the fourth was forgotten; the guard is that none of
        # them are copied across at all.
        import inspect
        import re

        from studio.agent import claude_code

        source = inspect.getsource(claude_code)
        assert "self._inner.begin(" in source
        assert re.findall(r"self\._inner\._\w+\s*=[^=]", source) == []
