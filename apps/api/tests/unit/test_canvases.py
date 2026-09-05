"""Canvases, and the boards on them.

A canvas is what the register lists and what a URL names. Its boards are
ordinary documents carrying a ``canvas_id`` — which is the point of the shape:
ops, undo, export and the agent loop keep working on a board exactly as they
worked on a document, because a board *is* a document.

The migration matters as much as the model here. Every résumé written before
canvases existed has none, and a register that lists canvases would open empty
on a database full of work.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

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


async def a_document(client: AsyncClient, title: str = "Alex Morgan", **extra) -> dict:
    response = await client.post(
        "/api/v1/documents", json={"title": title, "resume_data": SEED, **extra}
    )
    assert response.status_code == 201
    return response.json()


class TestABoardIsADocument:
    async def test_a_new_document_gets_a_canvas_of_its_own(
        self, client: AsyncClient
    ) -> None:
        # So no caller has to know canvases exist in order to make a résumé,
        # and no document can be created orphaned.
        created = await a_document(client)
        assert created["canvas_id"]

        canvases = (await client.get("/api/v1/canvases")).json()
        assert [canvas["id"] for canvas in canvases] == [created["canvas_id"]]
        assert [board["id"] for board in canvases[0]["boards"]] == [created["id"]]

    async def test_a_second_board_joins_an_existing_canvas(
        self, client: AsyncClient
    ) -> None:
        # What tailoring does: a second version of one résumé, aimed elsewhere.
        first = await a_document(client)
        second = await a_document(client, title="Stripe", canvas_id=first["canvas_id"])

        assert second["canvas_id"] == first["canvas_id"]
        canvas = (await client.get(f"/api/v1/canvases/{first['canvas_id']}")).json()
        assert [board["title"] for board in canvas["boards"]] == ["Alex Morgan", "Stripe"]

    async def test_a_board_carries_its_whole_document(
        self, client: AsyncClient
    ) -> None:
        # In full, because the register renders boards for real through the
        # same DocumentFlow the studio and the PDF use — so a card cannot go
        # stale against the thing it opens.
        await a_document(client)
        canvas = (await client.get("/api/v1/canvases")).json()[0]
        board = canvas["boards"][0]
        assert board["doc"]["experience"][0]["company"] == "Northwind"
        assert board["version"] == 1

    async def test_editing_a_board_still_works_exactly_as_before(
        self, client: AsyncClient
    ) -> None:
        # The whole reason a board is a document rather than a new kind of
        # thing: nothing downstream had to learn about canvases.
        created = await a_document(client)
        nid = created["doc"]["experience"][0]["bullets"][0]["nid"]
        response = await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={
                "ops": [{"op": "set_text", "nid": nid, "value": "Owned the ledger."}],
                "version": created["version"],
            },
        )
        assert response.status_code == 200
        assert response.json()["doc"]["experience"][0]["bullets"][0]["text"] == (
            "Owned the ledger."
        )


class TestNamingACanvas:
    async def test_it_is_named_after_the_résumé_that_started_it(
        self, client: AsyncClient
    ) -> None:
        await a_document(client, title="Rao Muhammad Hamza")
        canvas = (await client.get("/api/v1/canvases")).json()[0]
        assert canvas["title"] == "Rao Muhammad Hamza"

    async def test_it_can_be_renamed(self, client: AsyncClient) -> None:
        created = await a_document(client)
        response = await client.patch(
            f"/api/v1/canvases/{created['canvas_id']}", json={"title": "Job hunt 2026"}
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Job hunt 2026"

    async def test_a_blank_name_is_refused(self, client: AsyncClient) -> None:
        # A canvas with no name leaves the register with an unclickable-looking
        # row — the same rule a document's own title follows.
        created = await a_document(client)
        response = await client.patch(
            f"/api/v1/canvases/{created['canvas_id']}", json={"title": "   "}
        )
        assert response.status_code == 422

    async def test_renaming_a_canvas_leaves_its_boards_alone(
        self, client: AsyncClient
    ) -> None:
        created = await a_document(client, title="Alex Morgan")
        await client.patch(
            f"/api/v1/canvases/{created['canvas_id']}", json={"title": "Job hunt"}
        )
        board = (await client.get(f"/api/v1/documents/{created['id']}")).json()
        assert board["title"] == "Alex Morgan"


class TestDeleting:
    async def test_deleting_a_canvas_takes_its_boards(
        self, client: AsyncClient
    ) -> None:
        # They are versions of one résumé. Keeping them would leave a set of
        # sheets with nothing in common and no way back to each other.
        first = await a_document(client)
        second = await a_document(client, title="Stripe", canvas_id=first["canvas_id"])

        assert (
            await client.delete(f"/api/v1/canvases/{first['canvas_id']}")
        ).status_code == 204

        assert (await client.get(f"/api/v1/documents/{first['id']}")).status_code == 404
        assert (await client.get(f"/api/v1/documents/{second['id']}")).status_code == 404
        assert (await client.get("/api/v1/canvases")).json() == []

    async def test_deleting_one_board_leaves_the_canvas_and_the_others(
        self, client: AsyncClient
    ) -> None:
        first = await a_document(client)
        second = await a_document(client, title="Stripe", canvas_id=first["canvas_id"])

        assert (
            await client.delete(f"/api/v1/documents/{second['id']}")
        ).status_code == 204

        canvas = (await client.get(f"/api/v1/canvases/{first['canvas_id']}")).json()
        assert [board["id"] for board in canvas["boards"]] == [first["id"]]

    async def test_deleting_an_unknown_canvas_is_a_404(
        self, client: AsyncClient
    ) -> None:
        assert (await client.delete("/api/v1/canvases/nope")).status_code == 404


class TestAdoptingWhatCameBefore:
    """Every résumé written before canvases existed has none."""

    async def test_an_orphaned_document_is_given_a_canvas_at_startup(
        self, client: AsyncClient
    ) -> None:
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await a_document(client, title="Written before canvases")

        # Put it back the way it was: a document with no canvas at all.
        async with repo._engine.begin() as connection:  # noqa: SLF001
            await connection.execute(
                text("UPDATE documents SET canvas_id = NULL WHERE id = :id"),
                {"id": created["id"]},
            )
            await connection.execute(text("DELETE FROM canvases"))
        assert (await client.get("/api/v1/canvases")).json() == []

        await repo.create_schema()

        canvases = (await client.get("/api/v1/canvases")).json()
        assert len(canvases) == 1
        assert canvases[0]["title"] == "Written before canvases"
        assert [board["id"] for board in canvases[0]["boards"]] == [created["id"]]

    async def test_adopting_twice_changes_nothing(self, client: AsyncClient) -> None:
        # It runs on every boot, so it has to be idempotent or a restart would
        # multiply everybody's canvases.
        await a_document(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]

        await repo.create_schema()
        await repo.create_schema()

        assert len((await client.get("/api/v1/canvases")).json()) == 1
