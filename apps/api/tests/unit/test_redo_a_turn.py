"""Putting a turn back after undoing it.

Undo on its own is a trapdoor: taking the offer is the only way to find out
what a turn actually did, and a fourteen-edit turn undone by mistake is
fourteen edits to type back by hand -- the exact cost "undo this turn" exists
to remove, pointed the other way.

It needs no second mechanism. A revert already writes the state it replaced as
a snapshot of its own, recorded as the inverse of its own op, so redo is the
same restore at the other id. These pin the two halves of that: the way back
handed straight to the caller who is about to offer it, and the same fact
recovered from the log for somebody who reloaded the page in between.
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


async def a_turn(client: AsyncClient) -> tuple[dict, str]:
    """A résumé, and a snapshot of it taken before an edit landed.

    The shape a turn leaves behind: a checkpoint tagged with the turn id, then
    a version that moved past it.
    """
    created = (
        await client.post(
            "/api/v1/documents", json={"title": "Alex", "resume_data": SEED}
        )
    ).json()
    repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
    checkpoint = await repo.checkpoint(
        created["id"], label="before turn", turn_id="turn-1"
    )
    await client.post(
        f"/api/v1/documents/{created['id']}/ops",
        json={
            "ops": [
                {"op": "set_field", "target": "personal.name", "value": "Alex Reeve"}
            ]
        },
    )
    return created, checkpoint


def name_of(document: dict) -> str:
    return document["doc"]["personal"]["name"]


class TestTheWayBack:
    async def test_a_revert_hands_back_the_snapshot_that_undoes_it(
        self, client: AsyncClient
    ) -> None:
        # At the moment it is free: the server wrote it a line earlier, as the
        # inverse of the restore. Looked up afterwards it is a second search
        # through the log for something already in hand.
        created, checkpoint = await a_turn(client)

        response = await client.post(
            f"/api/v1/documents/{created['id']}/revert",
            json={"checkpoint_id": checkpoint},
        )

        assert response.status_code == 200
        assert response.json()["redo_checkpoint"]
        assert name_of(response.json()) == "Alex Morgan"

    async def test_restoring_it_puts_the_turn_back(self, client: AsyncClient) -> None:
        created, checkpoint = await a_turn(client)
        undone = (
            await client.post(
                f"/api/v1/documents/{created['id']}/revert",
                json={"checkpoint_id": checkpoint},
            )
        ).json()

        redone = (
            await client.post(
                f"/api/v1/documents/{created['id']}/revert",
                json={"checkpoint_id": undone["redo_checkpoint"]},
            )
        ).json()

        assert name_of(redone) == "Alex Reeve"
        # Forward, never back: the whole history stays append-only, so a client
        # holding an old ETag still gets a conflict rather than silently
        # appearing to be up to date.
        assert redone["version"] > undone["version"] > created["version"]

    async def test_it_can_be_pressed_back_and_forth(self, client: AsyncClient) -> None:
        # Each restore records its own way out, so the pair is a switch rather
        # than one press in each direction.
        created, checkpoint = await a_turn(client)
        document = created["id"]

        state = (
            await client.post(
                f"/api/v1/documents/{document}/revert",
                json={"checkpoint_id": checkpoint},
            )
        ).json()
        seen = [name_of(state)]
        for _ in range(3):
            state = (
                await client.post(
                    f"/api/v1/documents/{document}/revert",
                    json={"checkpoint_id": state["redo_checkpoint"]},
                )
            ).json()
            seen.append(name_of(state))

        assert seen == ["Alex Morgan", "Alex Reeve", "Alex Morgan", "Alex Reeve"]


class TestAfterAReload:
    """The offer has to survive a refresh, which is when it is most needed.

    Somebody who undoes a turn and is unsure what happened refreshes the page.
    Without this the conversation offered to undo the turn a second time, and
    taking that offer restored the résumé to where it already was: a control
    that appears broken, on the one screen where being unsure is the reason
    you are looking.
    """

    async def test_an_undone_turn_reads_as_undone(self, client: AsyncClient) -> None:
        created, checkpoint = await a_turn(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(
            created["id"], [("assistant", "Renamed you.", "ok")], turn_id="turn-1"
        )
        await client.post(
            f"/api/v1/documents/{created['id']}/revert",
            json={"checkpoint_id": checkpoint},
        )

        body = (
            await client.get(f"/api/v1/canvases/{created['canvas_id']}/messages")
        ).json()
        message = body["messages"][0]

        assert message["reverted"] is True
        assert [point["board_id"] for point in message["redo"]] == [created["id"]]

    async def test_the_offer_it_carries_actually_puts_the_turn_back(
        self, client: AsyncClient
    ) -> None:
        created, checkpoint = await a_turn(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(
            created["id"], [("assistant", "Renamed you.", "ok")], turn_id="turn-1"
        )
        await client.post(
            f"/api/v1/documents/{created['id']}/revert",
            json={"checkpoint_id": checkpoint},
        )

        body = (
            await client.get(f"/api/v1/canvases/{created['canvas_id']}/messages")
        ).json()
        point = body["messages"][0]["redo"][0]
        redone = (
            await client.post(
                f"/api/v1/documents/{point['board_id']}/revert",
                json={"checkpoint_id": point["checkpoint_id"]},
            )
        ).json()

        assert name_of(redone) == "Alex Reeve"

    async def test_a_turn_put_back_reads_as_done_again(
        self, client: AsyncClient
    ) -> None:
        # Only the *latest* restore describes where the document stands. Redo
        # the turn and the next restore names a different snapshot, so the turn
        # stops reading as undone and undo is what is offered.
        created, checkpoint = await a_turn(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(
            created["id"], [("assistant", "Renamed you.", "ok")], turn_id="turn-1"
        )
        undone = (
            await client.post(
                f"/api/v1/documents/{created['id']}/revert",
                json={"checkpoint_id": checkpoint},
            )
        ).json()
        await client.post(
            f"/api/v1/documents/{created['id']}/revert",
            json={"checkpoint_id": undone["redo_checkpoint"]},
        )

        body = (
            await client.get(f"/api/v1/canvases/{created['canvas_id']}/messages")
        ).json()
        message = body["messages"][0]

        assert message["reverted"] is False
        assert message["redo"] == []
        assert message["checkpoints"]

    async def test_a_turn_nobody_undid_offers_nothing_to_redo(
        self, client: AsyncClient
    ) -> None:
        created, _ = await a_turn(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(
            created["id"], [("assistant", "Renamed you.", "ok")], turn_id="turn-1"
        )

        body = (
            await client.get(f"/api/v1/canvases/{created['canvas_id']}/messages")
        ).json()
        message = body["messages"][0]

        assert message["reverted"] is False
        assert message["redo"] == []

    async def test_it_survives_a_round_trip_and_still_reads_as_undone(
        self, client: AsyncClient
    ) -> None:
        # Undo, redo, undo again. The board is back at the turn's starting
        # content, but it got there through a snapshot the reverts made along
        # the way -- the turn's own checkpoint is nowhere in the last restore.
        # Matching on which id was restored said "not undone" here, and the
        # reload offered to undo a turn the sheet had visibly already undone.
        created, checkpoint = await a_turn(client)
        document = created["id"]
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(
            document, [("assistant", "Renamed you.", "ok")], turn_id="turn-1"
        )

        state = (
            await client.post(
                f"/api/v1/documents/{document}/revert",
                json={"checkpoint_id": checkpoint},
            )
        ).json()
        for _ in range(2):
            state = (
                await client.post(
                    f"/api/v1/documents/{document}/revert",
                    json={"checkpoint_id": state["redo_checkpoint"]},
                )
            ).json()
        assert name_of(state) == "Alex Morgan"

        body = (
            await client.get(f"/api/v1/canvases/{created['canvas_id']}/messages")
        ).json()
        assert body["messages"][0]["reverted"] is True

    async def test_a_hand_edit_after_undoing_takes_the_offer_away(
        self, client: AsyncClient
    ) -> None:
        # Putting the turn back would silently discard what was just typed.
        # The board is no longer where the turn began, and that is the whole
        # test the content comparison is doing.
        created, checkpoint = await a_turn(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        await repo.add_messages(
            created["id"], [("assistant", "Renamed you.", "ok")], turn_id="turn-1"
        )
        await client.post(
            f"/api/v1/documents/{created['id']}/revert",
            json={"checkpoint_id": checkpoint},
        )
        await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={
                "ops": [
                    {"op": "set_field", "target": "personal.name", "value": "Alex M."}
                ]
            },
        )

        body = (
            await client.get(f"/api/v1/canvases/{created['canvas_id']}/messages")
        ).json()
        assert body["messages"][0]["reverted"] is False
