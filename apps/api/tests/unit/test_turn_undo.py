"""Putting a whole agent turn back.

Ordinary undo reverses one committed batch, and the loop commits one per tool
call -- so a turn that made fourteen edits is fourteen presses of it, which is
long enough that people stop halfway and are left with a document nobody asked
for. The checkpoint taken before a turn's first mutation is the only thing that
knows where the turn began: afterwards the op log holds fourteen versions that
look exactly like fourteen hand edits.

These drive the real ASGI app against a temp database, so the join that makes
the offer survive a page reload is exercised rather than assumed.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

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
            "description": ["Rebuilt the ledger.", "Cut latency.", "Wrote the runbook."],
            "descriptionStyles": ["bullet", "bullet", "bullet"],
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


async def seed(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
    )
    assert response.status_code == 201
    return response.json()


def bullets(doc: dict) -> list[str]:
    return [
        bullet["text"]
        for entry in doc.get("experience", [])
        for bullet in entry.get("bullets", [])
    ]


async def a_turn(client: AsyncClient, document_id: str, *, turn_id: str = "t1") -> str:
    """One agent turn: a snapshot, then an edit per tool call, as the loop does it.

    Deliberately several separate `POST /ops` calls rather than one batch --
    that is the shape of the problem, and a test that batched them would pass
    while the thing it describes stayed broken.
    """
    repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
    checkpoint_id = await repo.checkpoint(
        document_id, label="before turn", turn_id=turn_id
    )

    state = (await client.get(f"/api/v1/documents/{document_id}")).json()
    version = state["version"]
    nids = [
        bullet["nid"]
        for entry in state["doc"]["experience"]
        for bullet in entry["bullets"]
    ]
    for index, nid in enumerate(nids):
        response = await client.post(
            f"/api/v1/documents/{document_id}/ops",
            json={
                "ops": [{"op": "set_text", "nid": nid, "value": f"Tailored line {index}."}],
                "version": version,
            },
        )
        assert response.status_code == 200
        version = response.json()["version"]

    return checkpoint_id


class TestRevertingATurn:
    async def test_one_call_puts_back_every_edit_the_turn_made(
        self, client: AsyncClient
    ) -> None:
        created = await seed(client)
        before = bullets(created["doc"])
        assert len(before) == 3

        checkpoint_id = await a_turn(client, created["id"])

        during = (await client.get(f"/api/v1/documents/{created['id']}")).json()
        assert bullets(during["doc"]) != before
        # Three tool calls, three versions -- three presses of ordinary undo.
        assert during["version"] == created["version"] + 3

        response = await client.post(
            f"/api/v1/documents/{created['id']}/revert",
            json={"checkpoint_id": checkpoint_id},
        )
        assert response.status_code == 200
        assert bullets(response.json()["doc"]) == before

    async def test_history_moves_forward_rather_than_rewinding(
        self, client: AsyncClient
    ) -> None:
        # A client holding an old ETag must still get a conflict rather than
        # silently appearing to be up to date.
        created = await seed(client)
        checkpoint_id = await a_turn(client, created["id"])
        during = (await client.get(f"/api/v1/documents/{created['id']}")).json()

        response = await client.post(
            f"/api/v1/documents/{created['id']}/revert",
            json={"checkpoint_id": checkpoint_id},
        )
        assert response.json()["version"] > during["version"]

    async def test_an_unknown_checkpoint_is_a_404_not_a_crash(
        self, client: AsyncClient
    ) -> None:
        created = await seed(client)
        response = await client.post(
            f"/api/v1/documents/{created['id']}/revert",
            json={"checkpoint_id": "no-such-checkpoint"},
        )
        assert response.status_code == 404

    async def test_a_checkpoint_from_another_document_is_refused(
        self, client: AsyncClient
    ) -> None:
        mine = await seed(client)
        theirs = await seed(client)
        checkpoint_id = await a_turn(client, theirs["id"])

        response = await client.post(
            f"/api/v1/documents/{mine['id']}/revert",
            json={"checkpoint_id": checkpoint_id},
        )
        assert response.status_code == 404


class TestTheOfferSurvivesAReload:
    """The conversation is re-read on every page load, and a reload is exactly
    what somebody does when they are unsure whether an edit landed."""

    async def test_a_stored_turn_carries_its_checkpoint(
        self, client: AsyncClient
    ) -> None:
        created = await seed(client)
        checkpoint_id = await a_turn(client, created["id"], turn_id="turn-a")
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(
            created["id"],
            [("user", "tailor this", None), ("assistant", "Done.", "ok")],
            turn_id="turn-a",
        )

        body = (await client.get(f"/api/v1/documents/{created['id']}/messages")).json()
        assistant = [m for m in body["messages"] if m["role"] == "assistant"]
        assert assistant[0]["checkpoint"] == checkpoint_id

    async def test_a_user_message_never_carries_one(self, client: AsyncClient) -> None:
        created = await seed(client)
        await a_turn(client, created["id"], turn_id="turn-a")
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(
            created["id"],
            [("user", "tailor this", None), ("assistant", "Done.", "ok")],
            turn_id="turn-a",
        )

        body = (await client.get(f"/api/v1/documents/{created['id']}/messages")).json()
        assert [m for m in body["messages"] if m["role"] == "user"][0]["checkpoint"] is None

    async def test_a_turn_that_changed_nothing_offers_nothing(
        self, client: AsyncClient
    ) -> None:
        # Every turn gets a checkpoint, answers included. Restoring one that
        # matches the current document writes a new version and changes nothing
        # on screen, which is the shape of a control that appears broken.
        created = await seed(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.checkpoint(created["id"], label="before turn", turn_id="turn-quiet")
        await repo.add_messages(
            created["id"],
            [("user", "what is on page two?", None), ("assistant", "Nothing.", "ok")],
            turn_id="turn-quiet",
        )

        body = (await client.get(f"/api/v1/documents/{created['id']}/messages")).json()
        assistant = [m for m in body["messages"] if m["role"] == "assistant"]
        assert assistant[0]["checkpoint"] is None
