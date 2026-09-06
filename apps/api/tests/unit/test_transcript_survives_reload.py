"""What a finished turn leaves behind for somebody who comes back to it.

The conversation stored only role, text and status, on the argument that
reducing the event stream a second time on the server would be one copy of that
logic drifting out of step with the other. True, and it cost more than it
saved: a reload left every past turn as a bare paragraph. The reasoning was
gone and so was every tool call, so the record of *how* the resume came to say
what it says -- which is most of what the sidebar is for -- lasted exactly as
long as the tab did.

What is stored is the settled shape of a finished turn: each call with the
status it ended in, and the thinking as one block. The states a turn passes
*through* -- running, drafting, awaiting a confirmation -- have no meaning once
it is over, and those are the parts the client's reducer owns.
"""

from __future__ import annotations


import pytest
from httpx import ASGITransport, AsyncClient

from studio.llm.backend import ModelSpec
from studio.llm.factory import ProviderConfig, Resolution
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, think, turn
from studio.main import app
from studio.persistence.providers import ProviderStore
from studio.persistence.repo import DocumentRepo
from studio.streaming.channel import TurnRegistry
from tests.unit.test_turns_api import SEED, StubFactory


@pytest.fixture
async def harness():
    repo = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await repo.create_schema()
    app.state.repo = repo
    app.state.providers = ProviderStore(repo.session_factory)
    app.state.turns = TurnRegistry()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, repo
    await app.state.turns.shutdown()
    await repo.dispose()


def use_backend(backend: ScriptedBackend) -> None:
    backend.spec = ModelSpec(provider="scripted", model="scripted")
    app.state.backends = StubFactory(backend)


async def seed(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/documents", json={"title": "Alex", "resume_data": SEED}
    )
    return response.json()


async def settle(response) -> None:
    """Wait for the turn to finish writing its side of the conversation.

    The transcript is written by the background task that drives the turn,
    after the stream has closed -- deliberately, so a turn saves what it did
    even when the client navigated away halfway through. So the response
    returning is not the same moment as the row existing, and waiting on the
    task is the only version of this that is not a race: polling for a second
    stored the message about half the time.
    """
    task = app.state.turns._tasks[response.headers["X-Turn-Id"]]
    await task


async def run_a_turn(client: AsyncClient, created: dict, nid: str) -> None:
    use_backend(
        ScriptedBackend(
            [
                turn(
                    think("The second bullet is the vague one."),
                    call_tool("rewrite_text", {"nid": nid, "value": "Cut latency 96%."}),
                    done("tool_calls"),
                ),
                turn(say("Tightened it."), done("stop")),
            ]
        )
    )
    await settle(
        await client.post(
            "/api/v1/turns",
            json={"document_id": created["id"], "message": "tighten"},
        )
    )


async def stored(client: AsyncClient, created: dict) -> dict:
    """The assistant's row. Call `settle` first."""
    body = (await client.get(f"/api/v1/documents/{created['id']}/messages")).json()
    answered = [m for m in body["messages"] if m["role"] == "assistant"]
    assert answered, "the turn stored no side of the conversation"
    return answered[0]


class TestTheTurnIsStillThereAfterAReload:
    async def test_the_reasoning_comes_back(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        await run_a_turn(
            client, created, created["doc"]["experience"][0]["bullets"][0]["nid"]
        )

        assert (await stored(client, created))["thinking"] == (
            "The second bullet is the vague one."
        )

    async def test_the_tool_calls_come_back_with_what_they_did(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        nid = created["doc"]["experience"][0]["bullets"][0]["nid"]
        await run_a_turn(client, created, nid)

        activity = (await stored(client, created))["activity"]
        assert activity[0]["name"] == "rewrite_text"
        # Its final status, not the one it started in: a call left "running"
        # in the transcript reads as a turn that never finished.
        assert activity[0]["status"] == "applied"
        assert activity[0]["touched"] == [nid]

    async def test_an_advisory_note_keeps_its_place_among_the_calls(
        self, harness
    ) -> None:
        # This turn writes "96%", a figure the original did not have, so the
        # quality guard reports it. Those notes exist because the engine chose
        # to report rather than refuse -- the edit landed -- so they are stored
        # as notes and not as failures, and they stay in the order they were
        # raised: a note is about the edits either side of it, and floated to
        # the end it reads as being about the last one.
        client, _ = harness
        created = await seed(client)
        await run_a_turn(
            client, created, created["doc"]["experience"][0]["bullets"][0]["nid"]
        )

        activity = (await stored(client, created))["activity"]
        note = next(item for item in activity if item["status"] == "note")
        assert activity.index(note) > 0
        assert "96%" in note["detail"]

    async def test_a_rejected_call_says_why(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        use_backend(
            ScriptedBackend(
                [
                    turn(
                        call_tool(
                            "rewrite_text", {"nid": "blt_zzzzz", "value": "Nowhere."}
                        ),
                        done("tool_calls"),
                    ),
                    turn(say("That node is not there."), done("stop")),
                ]
            )
        )
        await settle(
            await client.post(
                "/api/v1/turns",
                json={"document_id": created["id"], "message": "tighten the third one"},
            )
        )

        activity = (await stored(client, created))["activity"]
        assert activity[0]["status"] == "rejected"
        # The reason, because the alternative is a cross with nothing behind
        # it -- and the reason is usually the answer to "why did nothing
        # happen when I asked for that?".
        assert activity[0]["code"]
        assert activity[0]["detail"]

    async def test_the_user_message_carries_none_of_it(self, harness) -> None:
        # The turn did the work; the message that asked for it did not.
        client, _ = harness
        created = await seed(client)
        await run_a_turn(
            client, created, created["doc"]["experience"][0]["bullets"][0]["nid"]
        )

        body = (await client.get(f"/api/v1/documents/{created['id']}/messages")).json()
        asked = next(m for m in body["messages"] if m["role"] == "user")
        assert asked["thinking"] is None
        assert asked["activity"] is None

    async def test_the_canvas_conversation_carries_it_too(self, harness) -> None:
        # The sidebar reads this one: the conversation is canvas-wide, because
        # asking for a version aimed at one job and then another is one
        # conversation about one resume.
        client, _ = harness
        created = await seed(client)
        await run_a_turn(
            client, created, created["doc"]["experience"][0]["bullets"][0]["nid"]
        )

        body = (
            await client.get(f"/api/v1/canvases/{created['canvas_id']}/messages")
        ).json()
        answered = next(m for m in body["messages"] if m["role"] == "assistant")
        assert answered["thinking"]
        assert "rewrite_text" in [item["name"] for item in answered["activity"]]


class TestATranscriptWrittenBeforeThisExisted:
    async def test_says_nothing_rather_than_claiming_there_was_nothing(
        self, harness
    ) -> None:
        # Null, not empty. An old row genuinely does not know what its tools
        # did, and an empty list is a claim that it did none.
        _, repo = harness
        client, _ = harness
        created = await seed(client)
        await repo.add_messages(
            created["id"], [("assistant", "Done.", "ok")], turn_id="t1"
        )

        answered = await stored(client, created)
        assert answered["activity"] is None
        assert answered["thinking"] is None
