"""One conversation per résumé, not one per version of it.

A conversation is: you ask for a version aimed at one job, read it back, then
ask for another. Held per board it split into as many transcripts as there were
versions — so switching versions silently changed the subject, and the history
handed to the model lost everything said about the résumé as a whole.

Each message still records which board it acted on. The conversation is about
the résumé; an edit landed on exactly one version of it.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from studio.agent.context import roster
from studio.main import app
from studio.persistence.repo import DocumentRepo

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
}


@pytest.fixture
async def client():
    repo = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await repo.create_schema()
    app.state.repo = repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        http.repo = repo  # type: ignore[attr-defined]
        yield http
    await repo.dispose()


async def a_canvas(client: AsyncClient) -> tuple[str, str, str]:
    """A résumé with two versions. Returns (canvas, first board, second board)."""
    first = (
        await client.post(
            "/api/v1/documents", json={"title": "General", "resume_data": SEED}
        )
    ).json()
    second = (
        await client.post(
            "/api/v1/documents",
            json={
                "title": "Stripe",
                "resume_data": SEED,
                "canvas_id": first["canvas_id"],
            },
        )
    ).json()
    return first["canvas_id"], first["id"], second["id"]


class TestOneTranscriptPerRésumé:
    async def test_turns_against_different_versions_are_one_conversation(
        self, client: AsyncClient
    ) -> None:
        canvas, first, second = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]

        await repo.add_messages(
            first, [("user", "tighten the summary", None), ("assistant", "Done.", "ok")],
            turn_id="t1",
        )
        await repo.add_messages(
            second, [("user", "now aim it at Stripe", None), ("assistant", "Done.", "ok")],
            turn_id="t2",
        )

        body = (await client.get(f"/api/v1/canvases/{canvas}/messages")).json()
        assert [m["text"] for m in body["messages"]] == [
            "tighten the summary",
            "Done.",
            "now aim it at Stripe",
            "Done.",
        ]

    async def test_each_message_says_which_version_it_acted_on(
        self, client: AsyncClient
    ) -> None:
        canvas, first, second = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(first, [("assistant", "One.", "ok")], turn_id="t1")
        await repo.add_messages(second, [("assistant", "Two.", "ok")], turn_id="t2")

        body = (await client.get(f"/api/v1/canvases/{canvas}/messages")).json()
        assert [(m["text"], m["board"]) for m in body["messages"]] == [
            ("One.", "General"),
            ("Two.", "Stripe"),
        ]

    async def test_another_canvas_hears_nothing_of_it(
        self, client: AsyncClient
    ) -> None:
        canvas, first, _ = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(first, [("user", "private", None)], turn_id="t1")

        other = (
            await client.post(
                "/api/v1/documents", json={"title": "Someone else", "resume_data": SEED}
            )
        ).json()
        body = (
            await client.get(f"/api/v1/canvases/{other['canvas_id']}/messages")
        ).json()
        assert body["messages"] == []

    async def test_clearing_it_clears_the_whole_conversation(
        self, client: AsyncClient
    ) -> None:
        canvas, first, second = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(first, [("user", "a", None)], turn_id="t1")
        await repo.add_messages(second, [("user", "b", None)], turn_id="t2")

        assert (
            await client.delete(f"/api/v1/canvases/{canvas}/messages")
        ).status_code == 204
        body = (await client.get(f"/api/v1/canvases/{canvas}/messages")).json()
        assert body["messages"] == []

    async def test_an_unknown_canvas_is_a_404(self, client: AsyncClient) -> None:
        assert (
            await client.get("/api/v1/canvases/nope/messages")
        ).status_code == 404


class TestAConversationHeldBeforeTheMove:
    async def test_it_is_filed_under_its_canvas_at_startup(
        self, client: AsyncClient
    ) -> None:
        # A résumé full of history would otherwise open on an empty sidebar,
        # and the history handed to the model would be empty too.
        canvas, first, _ = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(first, [("user", "said before the move", None)])

        async with repo._engine.begin() as connection:  # noqa: SLF001
            await connection.execute(text("UPDATE chat_messages SET canvas_id = NULL"))
        assert (await client.get(f"/api/v1/canvases/{canvas}/messages")).json()[
            "messages"
        ] == []

        await repo.create_schema()

        body = (await client.get(f"/api/v1/canvases/{canvas}/messages")).json()
        assert [m["text"] for m in body["messages"]] == ["said before the move"]


class TestUndoingATurnStillWorks:
    async def test_the_offer_survives_on_the_canvas_transcript(
        self, client: AsyncClient
    ) -> None:
        # Phase 0's control reads the transcript, and the transcript moved.
        canvas, first, _ = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        checkpoint_id = await repo.checkpoint(first, label="before turn", turn_id="t1")

        board = (await client.get(f"/api/v1/documents/{first}")).json()
        nid = board["doc"]["experience"][0]["bullets"][0]["nid"]
        await client.post(
            f"/api/v1/documents/{first}/ops",
            json={
                "ops": [{"op": "set_text", "nid": nid, "value": "Owned the ledger."}],
                "version": board["version"],
            },
        )
        await repo.add_messages(first, [("assistant", "Done.", "ok")], turn_id="t1")

        body = (await client.get(f"/api/v1/canvases/{canvas}/messages")).json()
        assert body["messages"][0]["checkpoint"] == checkpoint_id

    async def test_a_turn_that_changed_nothing_offers_nothing(
        self, client: AsyncClient
    ) -> None:
        canvas, first, _ = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.checkpoint(first, label="before turn", turn_id="quiet")
        await repo.add_messages(first, [("assistant", "Nothing to do.", "ok")],
                                turn_id="quiet")

        body = (await client.get(f"/api/v1/canvases/{canvas}/messages")).json()
        assert body["messages"][0]["checkpoint"] is None

    async def test_a_snapshot_of_another_version_is_judged_against_that_version(
        self, client: AsyncClient
    ) -> None:
        # The canvas transcript holds turns against several boards, so whether
        # a snapshot is worth offering depends on how far *that* board moved.
        canvas, first, second = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.checkpoint(second, label="before turn", turn_id="t2")
        await repo.add_messages(second, [("assistant", "Done.", "ok")], turn_id="t2")

        # The *first* board moves. The offer on the second board's turn must
        # not appear because of it.
        board = (await client.get(f"/api/v1/documents/{first}")).json()
        nid = board["doc"]["experience"][0]["bullets"][0]["nid"]
        await client.post(
            f"/api/v1/documents/{first}/ops",
            json={
                "ops": [{"op": "set_text", "nid": nid, "value": "Moved."}],
                "version": board["version"],
            },
        )

        body = (await client.get(f"/api/v1/canvases/{canvas}/messages")).json()
        assert body["messages"][0]["checkpoint"] is None


class TestTheRoster:
    """What the assistant is told about the other versions."""

    def test_a_résumé_with_one_version_says_nothing(self) -> None:
        # Which is almost every résumé, and it should pay nothing for this.
        class Board:
            def __init__(self, id: str, title: str) -> None:
                self.id, self.title = id, title

        assert roster([Board("a", "General")], "a") == ""
        assert roster([], "a") == ""

    def test_it_names_the_versions_and_marks_the_one_being_edited(self) -> None:
        class Board:
            def __init__(self, id: str, title: str) -> None:
                self.id, self.title = id, title

        text_ = roster(
            [
                Board("doc_zzz01", "General"),
                Board("doc_zzz02", "Stripe"),
                Board("doc_zzz03", "Datadog"),
            ],
            "doc_zzz02",
        )
        assert "General" in text_ and "Stripe" in text_ and "Datadog" in text_
        # Names, not ids: a version is referred to the way a person refers to
        # it, and an id in the prompt is one more thing for a model to confuse
        # with a node id.
        assert "doc_zzz" not in text_
        stripe_line = [line for line in text_.split("\n") if "Stripe" in line][0]
        assert "you are editing this one" in stripe_line

    async def test_it_reaches_the_prompt(self, client: AsyncClient) -> None:
        from studio.agent.budget import TurnBudget
        from studio.agent.loop import TurnRequest, TurnRunner
        from studio.llm.scripted import ScriptedBackend, done, say, turn
        from studio.streaming.channel import TurnChannel

        canvas, first, _ = await a_canvas(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        backend = ScriptedBackend([turn(say("Done."), done("stop"))])
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        await runner.run(
            TurnRequest(document_id=first, message="tighten the summary"),
            TurnChannel(turn_id="turn-roster", document_id=first),
        )

        sent = "\n".join(
            str(message.get("content", ""))
            for request in backend.received
            for message in request.get("messages", [])
        )
        assert "VERSIONS:" in sent
        assert "Stripe" in sent
