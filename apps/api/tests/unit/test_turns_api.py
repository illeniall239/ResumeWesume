"""The streaming endpoint, through the real ASGI stack.

These exercise the wire format a browser actually consumes: chunked NDJSON, one
JSON object per line, terminated by a `done`. A protocol bug here is invisible
to the loop tests and fatal in the browser.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from studio.llm.backend import BackendError, ModelSpec
from studio.llm.factory import ProviderConfig, Resolution
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, turn
from studio.main import app
from studio.persistence.providers import ProviderStore
from studio.persistence.repo import DocumentRepo
from studio.streaming.channel import TurnRegistry

SEED = {
    "personalInfo": {"name": "Alex Morgan", "email": "alex@example.com"},
    "summary": "Backend engineer.",
    "workExperience": [
        {
            "title": "Senior Engineer",
            "company": "Northwind",
            "years": "2021 - Present",
            "description": ["Rebuilt the ledger."],
            "descriptionStyles": ["bullet"],
        }
    ],
    "additional": {"technicalSkills": ["Python"]},
}


class StubFactory:
    """Stands in for the backend factory so no network is reachable.

    Implements the whole surface the routers use -- ``from_settings``,
    ``resolve``, and now ``effective`` plus ``build`` -- because the turn router
    has to know *which provider* was chosen before it builds anything: a Claude
    subscription runs a different harness and never streams through a backend at
    all. A stub answering only the older calls let the real resolver run, which
    on a developer machine with Claude Code signed in routed every test down the
    subscription path.
    """

    def __init__(self, backend: ScriptedBackend) -> None:
        self._backend = backend

    def from_settings(self) -> ScriptedBackend:
        return self._backend

    async def resolve(self, store: object = None) -> ScriptedBackend:
        return self._backend

    async def effective(self, store: object = None) -> Resolution:
        return Resolution(ProviderConfig(provider="scripted", model="test"), "")

    def build(self, config: object = None) -> ScriptedBackend:
        return self._backend


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


def parse(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


class TestStreaming:
    async def test_turn_streams_ndjson(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        nid = created["doc"]["experience"][0]["bullets"][0]["nid"]

        use_backend(
            ScriptedBackend(
                [
                    turn(
                        say("Tightening that."),
                        call_tool("rewrite_text", {"nid": nid, "value": "Cut latency 96%."}),
                        done("tool_calls"),
                    ),
                    turn(say("Done."), done("stop")),
                ]
            )
        )

        response = await client.post(
            "/api/v1/turns",
            json={"document_id": created["id"], "message": "tighten my bullet"},
        )
        assert response.status_code == 200
        assert "ndjson" in response.headers["content-type"]
        assert response.headers["x-turn-id"]

        events = parse(response.text)
        kinds = [event["type"] for event in events]

        assert kinds[0] == "turn_started"
        assert kinds[-1] == "done"
        assert "patch_applied" in kinds
        assert events[-1]["status"] == "ok"
        assert events[-1]["applied"] == 1

    async def test_every_line_is_valid_json(self, harness) -> None:
        """A single malformed line breaks the client's reader for the rest of
        the stream, so this is worth asserting explicitly."""
        client, _ = harness
        created = await seed(client)
        use_backend(ScriptedBackend([turn(say("Looks good."), done("stop"))]))

        response = await client.post(
            "/api/v1/turns",
            json={"document_id": created["id"], "message": "how does it look"},
        )
        for line in response.text.splitlines():
            if line.strip():
                assert json.loads(line)["v"] == 1

    async def test_sequence_numbers_are_contiguous(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        nid = created["doc"]["experience"][0]["bullets"][0]["nid"]
        use_backend(
            ScriptedBackend(
                [
                    turn(
                        call_tool("rewrite_text", {"nid": nid, "value": "x"}),
                        done("tool_calls"),
                    ),
                    turn(say("Done."), done("stop")),
                ]
            )
        )

        response = await client.post(
            "/api/v1/turns", json={"document_id": created["id"], "message": "edit"}
        )
        sequences = [event["seq"] for event in parse(response.text)]
        assert sequences == list(range(1, len(sequences) + 1))

    async def test_document_is_actually_mutated(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        nid = created["doc"]["experience"][0]["bullets"][0]["nid"]
        use_backend(
            ScriptedBackend(
                [
                    turn(
                        call_tool(
                            "rewrite_text", {"nid": nid, "value": "Cut latency 96%."}
                        ),
                        done("tool_calls"),
                    ),
                    turn(say("Done."), done("stop")),
                ]
            )
        )

        await client.post(
            "/api/v1/turns", json={"document_id": created["id"], "message": "tighten"}
        )
        fetched = await client.get(f"/api/v1/documents/{created['id']}")
        body = fetched.json()
        assert body["version"] == 2
        assert body["doc"]["experience"][0]["bullets"][0]["text"] == "Cut latency 96%."

    async def test_rejection_reaches_the_client(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        use_backend(
            ScriptedBackend(
                [
                    turn(
                        call_tool("rewrite_text", {"nid": "blt_ghost", "value": "x"}),
                        done("tool_calls"),
                    ),
                    turn(say("Sorry."), done("stop")),
                ]
            )
        )

        response = await client.post(
            "/api/v1/turns", json={"document_id": created['id'], "message": "edit"}
        )
        events = parse(response.text)
        rejections = [e for e in events if e["type"] == "patch_rejected"]
        assert rejections and rejections[0]["code"] == "unknown_node"

    async def test_missing_document_is_404(self, harness) -> None:
        client, _ = harness
        use_backend(ScriptedBackend([turn(say("hi"), done("stop"))]))
        response = await client.post(
            "/api/v1/turns", json={"document_id": "nope", "message": "edit"}
        )
        assert response.status_code == 404


class TestTurnLifecycle:
    async def test_status_and_cancel(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        use_backend(ScriptedBackend([turn(say("thinking"), done("stop"))]))

        response = await client.post(
            "/api/v1/turns", json={"document_id": created["id"], "message": "go"}
        )
        turn_id = response.headers["x-turn-id"]

        status = await client.get(f"/api/v1/turns/{turn_id}/status")
        assert status.status_code == 200
        assert status.json()["finished"] is True

    async def test_cancelling_an_unknown_turn_is_404(self, harness) -> None:
        client, _ = harness
        assert (await client.delete("/api/v1/turns/nope")).status_code == 404

    async def test_resuming_an_expired_turn_is_404(self, harness) -> None:
        """The client must be told to reload rather than waiting forever for
        events that will never arrive."""
        client, _ = harness
        response = await client.get("/api/v1/turns/nope/stream")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "turn_not_found"

    async def test_replay_returns_the_whole_turn(self, harness) -> None:
        client, _ = harness
        created = await seed(client)
        use_backend(ScriptedBackend([turn(say("Looks fine."), done("stop"))]))

        first = await client.post(
            "/api/v1/turns", json={"document_id": created["id"], "message": "review"}
        )
        turn_id = first.headers["x-turn-id"]

        # A client that dropped at the start replays from zero and catches up.
        replay = await client.get(f"/api/v1/turns/{turn_id}/stream?from_seq=0")
        assert replay.status_code == 200
        assert [event["type"] for event in parse(replay.text)] == [
            event["type"] for event in parse(first.text)
        ]

    async def test_replay_from_a_midpoint_skips_what_was_seen(
        self, harness
    ) -> None:
        client, _ = harness
        created = await seed(client)
        use_backend(ScriptedBackend([turn(say("Looks fine."), done("stop"))]))

        first = await client.post(
            "/api/v1/turns", json={"document_id": created["id"], "message": "review"}
        )
        turn_id = first.headers["x-turn-id"]
        seen = parse(first.text)[2]["seq"]

        replay = parse(
            (await client.get(f"/api/v1/turns/{turn_id}/stream?from_seq={seen}")).text
        )
        assert all(event["seq"] > seen for event in replay)


class TestHealth:
    async def test_liveness_makes_no_model_call(self, harness) -> None:
        client, _ = harness
        # No backend configured at all: liveness must still answer, or an
        # orchestrator restarts a process whose only problem is a dead provider.
        app.state.backends = None
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    async def test_request_id_is_returned(self, harness) -> None:
        client, _ = harness
        response = await client.get(
            "/api/v1/health", headers={"X-Request-Id": "abc123"}
        )
        assert response.headers["x-request-id"] == "abc123"


async def turn_finished() -> None:
    """Wait for the turn task itself, not for a row to appear.

    The transcript is written in the turn task's ``finally``, which runs just
    after the response body ends -- the task outlives the request on purpose, so
    an exchange is saved even when the client navigated away.

    Awaiting the task rather than polling the database is not a style
    preference. Polling ``conversation()`` every 10ms opened a session each
    time, and against a shared in-memory SQLite connection that starved the
    writer it was waiting for: the rows never appeared and the test failed
    against working code.
    """
    tasks = [task for task in app.state.turns._tasks.values() if not task.done()]
    for task in tasks:
        await task


class TestConversationSurvivesAReload:
    """The transcript is stored on the server, not in the browser.

    Not only so the sidebar can be redrawn. Every turn sends the last few
    exchanges to the model, and a chat held in memory meant a page reload
    silently emptied that history -- the assistant would ask again for dates it
    had just been given, mid-task, with nothing on screen to say why.
    """

    async def test_a_turn_is_stored_and_read_back(self, harness) -> None:
        client, repo = harness
        created = await seed(client)
        use_backend(ScriptedBackend([turn(say("Tightened it."), done("stop"))]))

        await client.post(
            "/api/v1/turns",
            json={"document_id": created["id"], "message": "tighten my first bullet"},
        )
        await turn_finished()

        stored = await client.get(f"/api/v1/documents/{created['id']}/messages")
        messages = stored.json()["messages"]

        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[0]["text"] == "tighten my first bullet"
        assert messages[1]["text"] == "Tightened it."
        assert messages[1]["status"] == "ok"

    async def test_the_users_words_are_kept_even_when_the_turn_fails(
        self, harness
    ) -> None:
        """What they asked is part of the conversation whatever came back."""
        client, repo = harness
        created = await seed(client)
        use_backend(ScriptedBackend([], fail_with=BackendError("provider down")))

        await client.post(
            "/api/v1/turns",
            json={"document_id": created["id"], "message": "do the thing"},
        )

        await turn_finished()

        stored = await repo.conversation(created["id"])
        assert [message.text for message in stored] == ["do the thing"]

    async def test_clearing_forgets_the_chat_and_keeps_the_document(
        self, harness
    ) -> None:
        client, repo = harness
        created = await seed(client)
        await repo.add_messages(created["id"], [("user", "hello", None)])

        cleared = await client.delete(f"/api/v1/documents/{created['id']}/messages")

        assert cleared.status_code == 204
        assert await repo.conversation(created["id"]) == []
        # The resume itself is untouched: edits are undone through the op log,
        # not by deleting what was said about them.
        assert await repo.get(created["id"]) is not None

    async def test_an_unknown_document_has_no_conversation(self, harness) -> None:
        client, _ = harness
        missing = await client.get("/api/v1/documents/nope/messages")
        assert missing.status_code == 404
