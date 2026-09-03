"""Endpoint behaviour through the real ASGI app.

No mocked routers: these drive the actual FastAPI stack against a temp SQLite
database, so wiring mistakes surface here rather than in the browser.
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
    "additional": {"technicalSkills": ["Python", "Go"]},
}


@pytest.fixture
async def client():
    repo = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await repo.create_schema()
    app.state.repo = repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await repo.dispose()


async def seed(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
    )
    assert response.status_code == 201
    return response.json()


class TestHealth:
    async def test_health(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


class TestCreate:
    async def test_create_from_legacy_shape(self, client: AsyncClient) -> None:
        body = await seed(client)
        assert body["version"] == 1
        assert body["doc"]["personal"]["name"] == "Alex Morgan"
        assert body["doc"]["experience"][0]["company"] == "Northwind"
        # Ids are minted on import; nothing else can address a node without them.
        assert body["doc"]["experience"][0]["nid"].startswith("exp_")

    async def test_create_blank(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/documents", json={"title": "Empty"})
        assert response.status_code == 201
        assert response.json()["doc"]["experience"] == []

    async def test_a_template_document_is_scaffolding(
        self, client: AsyncClient
    ) -> None:
        """A résumé nobody has written yet holds no facts to protect.

        The engine's promise is never to invent a claim about this person. On a
        document seeded from a gallery card that promise is backwards: "Alex
        Morgan" is not a person, and replacing his words wholesale is the only
        thing being asked for. The two cases are indistinguishable without this
        flag, so the guards fired on placeholder text and the turn did nothing.
        """
        response = await client.post(
            "/api/v1/documents",
            json={"title": "From a card", "template": "book", "scaffold": True},
        )
        assert response.status_code == 201
        assert response.json()["doc"]["scaffold"] is True

    async def test_an_imported_document_is_never_scaffolding(
        self, client: AsyncClient
    ) -> None:
        """Someone's real résumé is theirs from the first moment."""
        body = await seed(client)
        assert body["doc"]["scaffold"] is False

    async def test_confirming_ends_the_scaffolding_and_changes_no_words(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/documents",
            json={"title": "Starter", "starter": True, "scaffold": True},
        )
        created = response.json()
        before = created["doc"]

        confirmed = await client.post(
            f"/api/v1/documents/{created['id']}/confirm", json={"nids": []}
        )
        assert confirmed.status_code == 200
        after = confirmed.json()["doc"]

        assert after["scaffold"] is False
        assert after["unverified"] == []
        # Confirming is a statement about truth, not about wording.
        assert after["experience"] == before["experience"]
        assert after["summary"] == before["summary"]
        # And it is not a content change, so the hash a client holds stays good.
        assert confirmed.json()["hash"] == created["hash"]

    async def test_create_records_the_template(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/documents", json={"title": "Set", "template": "banner"}
        )
        assert response.status_code == 201
        assert response.json()["doc"]["template"] == "banner"

    async def test_new_documents_default_to_plain(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/documents", json={"title": "Default"})
        assert response.json()["doc"]["template"] == "plain"

    async def test_starter_is_something_to_fill_in(self, client: AsyncClient) -> None:
        """A new document must never open as a blank sheet.

        The defect this pins: creating with no content produced a document with
        no summary, no experience, no education and no skills, which laid out
        as a single page carrying exactly one frame -- the header. Every
        section renders only when it has something in it, so the page was
        empty, nothing could be typed into, and choosing a template landed
        somewhere that made the template look broken.
        """
        response = await client.post(
            "/api/v1/documents",
            json={"title": "Starter", "template": "book", "starter": True},
        )
        assert response.status_code == 201
        doc = response.json()["doc"]

        assert doc["template"] == "book"
        assert doc["summary"] is not None
        assert len(doc["experience"]) == 1
        assert len(doc["experience"][0]["bullets"]) == 2
        assert len(doc["education"]) == 1
        assert len(doc["skills"]) == 1

        # Empty, not pre-filled: a skeleton is a shape to follow, not a form to
        # clear out before it is usable.
        assert doc["summary"]["text"] == ""
        assert doc["experience"][0]["title"] == ""
        assert doc["experience"][0]["bullets"][0]["text"] == ""

        # Real, addressable nodes -- the editor and the assistant both need an
        # id to write to, and an empty document has nothing to address.
        assert doc["summary"]["nid"].startswith("sum_")
        assert doc["experience"][0]["nid"].startswith("exp_")
        assert doc["education"][0]["nid"].startswith("edu_")

        # And it lays out as a page with structure on it, not one lone header.
        frames = [element for page in doc["pages"] for element in page["elements"]]
        assert len(frames) > 1
        refs = {frame.get("ref") for frame in frames}
        assert {"personal", "summary", "experience", "education", "skills"} <= refs

    async def test_list(self, client: AsyncClient) -> None:
        await seed(client)
        response = await client.get("/api/v1/documents")
        assert response.status_code == 200
        assert len(response.json()) == 1

    async def test_create_stores_source_text(self, client: AsyncClient) -> None:
        """An imported document keeps the text it was parsed from.

        Nothing reads it back during editing, so this is the only place the
        wiring is observable.
        """
        from sqlalchemy import select

        from studio.main import app
        from studio.persistence.models import Document

        source = "ALEX MORGAN\n\nEXPERIENCE\nNorthwind, 2021 - Present"
        response = await client.post(
            "/api/v1/documents",
            json={"title": "Alex", "resume_data": SEED, "source_markdown": source},
        )
        assert response.status_code == 201

        repo = app.state.repo
        async with repo._session() as session:
            row = (
                await session.execute(
                    select(Document).where(Document.id == response.json()["id"])
                )
            ).scalar_one()
        assert row.source_markdown == source

    async def test_source_text_is_not_echoed_in_the_response(
        self, client: AsyncClient
    ) -> None:
        """It can be tens of kilobytes and no client needs it back."""
        response = await client.post(
            "/api/v1/documents",
            json={"title": "Alex", "resume_data": SEED, "source_markdown": "x" * 100},
        )
        assert "source_markdown" not in response.json()


class TestFetch:
    async def test_get_sets_etag(self, client: AsyncClient) -> None:
        created = await seed(client)
        response = await client.get(f"/api/v1/documents/{created['id']}")
        assert response.status_code == 200
        assert response.headers["etag"].startswith('W/"1-')

    async def test_missing_is_404(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/documents/nope")).status_code == 404

    async def test_legacy_projection_realigns_arrays(
        self, client: AsyncClient
    ) -> None:
        created = await seed(client)
        response = await client.get(f"/api/v1/documents/{created['id']}/legacy")
        entry = response.json()["workExperience"][0]
        assert len(entry["description"]) == len(entry["descriptionStyles"])


class TestOps:
    async def test_edit_applies_and_bumps_version(self, client: AsyncClient) -> None:
        created = await seed(client)
        nid = created["doc"]["experience"][0]["bullets"][0]["nid"]
        response = await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={"ops": [{"op": "set_text", "nid": nid, "value": "Cut latency 96%."}]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == 2
        assert len(body["applied"]) == 1
        assert body["doc"]["experience"][0]["bullets"][0]["text"] == "Cut latency 96%."

    async def test_rejection_is_reported_not_raised(self, client: AsyncClient) -> None:
        created = await seed(client)
        response = await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={"ops": [{"op": "set_text", "nid": "blt_ghost", "value": "x"}]},
        )
        # A bad op is data, not a server error.
        assert response.status_code == 200
        body = response.json()
        assert body["applied"] == []
        assert body["rejected"][0]["code"] == "unknown_node"
        assert body["version"] == 1

    async def test_stale_if_match_conflicts_with_rebase_data(
        self, client: AsyncClient
    ) -> None:
        created = await seed(client)
        nid = created["doc"]["experience"][0]["bullets"][0]["nid"]
        first = await client.get(f"/api/v1/documents/{created['id']}")
        stale_etag = first.headers["etag"]

        await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={"ops": [{"op": "set_text", "nid": nid, "value": "agent wrote this"}]},
            headers={"If-Match": stale_etag},
        )
        conflict = await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={"ops": [{"op": "set_text", "nid": nid, "value": "user wrote this"}]},
            headers={"If-Match": stale_etag},
        )
        assert conflict.status_code == 409
        detail = conflict.json()["detail"]
        assert detail["code"] == "version_conflict"
        assert detail["current_version"] == 2
        # The client needs what changed to rebase without losing its typing.
        assert len(detail["ops_since"]) == 1

    async def test_user_may_edit_personal_info(self, client: AsyncClient) -> None:
        """The tier system constrains the model, not the author. A person
        editing their own resume can change their own email."""
        created = await seed(client)
        response = await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={
                "ops": [
                    {
                        "op": "set_field",
                        "target": "personal.email",
                        "value": "new@example.com",
                    }
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()["doc"]["personal"]["email"] == "new@example.com"

    async def test_ops_on_missing_document_are_404(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/documents/nope/ops",
            json={"ops": [{"op": "set_text", "nid": "blt_aaaaa", "value": "x"}]},
        )
        assert response.status_code == 404

    async def test_malformed_op_is_422(self, client: AsyncClient) -> None:
        created = await seed(client)
        response = await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={"ops": [{"op": "no_such_op", "nid": "blt_aaaaa"}]},
        )
        assert response.status_code == 422


class TestDelete:
    async def test_delete(self, client: AsyncClient) -> None:
        created = await seed(client)
        assert (
            await client.delete(f"/api/v1/documents/{created['id']}")
        ).status_code == 204
        assert (
            await client.get(f"/api/v1/documents/{created['id']}")
        ).status_code == 404


class TestUndoRedo:
    """Undo through the real stack.

    The point of routing reversal through ``apply_ops`` rather than restoring a
    snapshot is that it works identically whoever made the edit -- a direct
    edit, a drag, or an agent turn -- and that each undo is itself an ordinary
    version, so it can be redone.
    """

    async def undo(self, client: AsyncClient, doc_id: str):
        return await client.post(f"/api/v1/documents/{doc_id}/undo")

    async def redo(self, client: AsyncClient, doc_id: str):
        return await client.post(f"/api/v1/documents/{doc_id}/redo")

    async def edit(self, client: AsyncClient, doc: dict, ops: list) -> dict:
        response = await client.post(
            f"/api/v1/documents/{doc['id']}/ops",
            headers={"If-Match": f'W/"{doc["version"]}-"'},
            json={"ops": ops},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def first_bullet(self, body: dict) -> str:
        return body["doc"]["experience"][0]["bullets"][0]["text"]

    async def test_a_text_edit_round_trips(self, client: AsyncClient) -> None:
        doc = await seed(client)
        nid = doc["doc"]["experience"][0]["bullets"][0]["nid"]
        original = doc["doc"]["experience"][0]["bullets"][0]["text"]

        edited = await self.edit(
            client, doc, [{"op": "set_text", "nid": nid, "value": "Changed it."}]
        )
        assert self.first_bullet(edited) == "Changed it."

        undone = await self.undo(client, doc["id"])
        assert undone.status_code == 200
        assert self.first_bullet(undone.json()) == original
        assert undone.json()["reversed_version"] == edited["version"]

        redone = await self.redo(client, doc["id"])
        assert redone.status_code == 200
        assert self.first_bullet(redone.json()) == "Changed it."

    async def test_a_delete_comes_back_in_its_original_place(
        self, client: AsyncClient
    ) -> None:
        """The case the old partial `invert` got wrong: it restored to the end."""
        doc = await seed(client)
        exp = doc["doc"]["experience"][0]
        before = [b["nid"] for b in exp["bullets"]]
        assert len(before) >= 1

        await self.edit(
            client, doc, [{"op": "insert_node", "parent": exp["nid"], "index": -1,
                           "node": {"nid": "blt_zzzz1", "text": "Second."}}]
        )
        current = (await client.get(f"/api/v1/documents/{doc['id']}")).json()
        await self.edit(
            client, current, [{"op": "remove_node", "nid": before[0]}]
        )

        undone = await self.undo(client, doc["id"])
        assert undone.status_code == 200
        restored = [b["nid"] for b in undone.json()["doc"]["experience"][0]["bullets"]]
        assert restored[0] == before[0], "restored to the wrong position"

    async def test_a_move_round_trips(self, client: AsyncClient) -> None:
        doc = await seed(client)
        exp = doc["doc"]["experience"][0]
        await self.edit(
            client, doc, [{"op": "insert_node", "parent": exp["nid"], "index": -1,
                           "node": {"nid": "blt_zzzz2", "text": "Second."}}]
        )
        current = (await client.get(f"/api/v1/documents/{doc['id']}")).json()
        order = [b["nid"] for b in current["doc"]["experience"][0]["bullets"]]

        await self.edit(
            client, current,
            [{"op": "move_node", "nid": order[0], "parent": exp["nid"], "index": 1}],
        )
        moved = (await client.get(f"/api/v1/documents/{doc['id']}")).json()
        assert [b["nid"] for b in moved["doc"]["experience"][0]["bullets"]] == order[::-1]

        undone = await self.undo(client, doc["id"])
        assert [b["nid"] for b in undone.json()["doc"]["experience"][0]["bullets"]] == order

    async def test_undo_walks_back_through_several_edits(
        self, client: AsyncClient
    ) -> None:
        doc = await seed(client)
        nid = doc["doc"]["experience"][0]["bullets"][0]["nid"]
        original = doc["doc"]["experience"][0]["bullets"][0]["text"]

        current = doc
        for value in ("one", "two", "three"):
            current = await self.edit(
                client, current, [{"op": "set_text", "nid": nid, "value": value}]
            )

        for expected in ("two", "one", original):
            body = (await self.undo(client, doc["id"])).json()
            assert self.first_bullet(body) == expected

    async def test_nothing_to_undo_is_a_409_not_an_error(
        self, client: AsyncClient
    ) -> None:
        doc = await seed(client)
        response = await self.undo(client, doc["id"])
        assert response.status_code == 409
        assert "undo" in response.json()["detail"].lower()

    async def test_nothing_to_redo_before_an_undo(self, client: AsyncClient) -> None:
        doc = await seed(client)
        nid = doc["doc"]["experience"][0]["bullets"][0]["nid"]
        await self.edit(client, doc, [{"op": "set_text", "nid": nid, "value": "x"}])
        assert (await self.redo(client, doc["id"])).status_code == 409

    async def test_a_new_edit_clears_the_redo_stack(self, client: AsyncClient) -> None:
        """Otherwise redo would resurrect work from before the branch and
        overwrite what the user just did."""
        doc = await seed(client)
        nid = doc["doc"]["experience"][0]["bullets"][0]["nid"]

        edited = await self.edit(
            client, doc, [{"op": "set_text", "nid": nid, "value": "first"}]
        )
        await self.undo(client, doc["id"])

        current = (await client.get(f"/api/v1/documents/{doc['id']}")).json()
        await self.edit(
            client, current, [{"op": "set_text", "nid": nid, "value": "second"}]
        )

        assert (await self.redo(client, doc["id"])).status_code == 409
        final = (await client.get(f"/api/v1/documents/{doc['id']}")).json()
        assert self.first_bullet(final) == "second"

    async def test_undo_on_an_unknown_document_is_a_404(
        self, client: AsyncClient
    ) -> None:
        assert (await self.undo(client, "nope")).status_code == 404


class TestTheRegisterKnowsWhenNotJustHowMuch:
    """A document says when it last changed.

    The register listed documents by write count, which is a fact about the
    engine rather than about the person: "173 writes" does not tell you which
    résumé you were working on and "3 hours ago" does.
    """

    async def test_a_listed_document_carries_a_timestamp(self, client) -> None:
        await client.post("/api/v1/documents", json={"title": "Mine"})

        listed = (await client.get("/api/v1/documents")).json()

        assert listed[0]["updated_at"]

    async def test_it_is_utc(self, client) -> None:
        """The client reads a bare timestamp as local time otherwise, which put
        a document saved a minute ago hours into the past or the future."""
        from datetime import datetime, timezone

        created = (await client.post("/api/v1/documents", json={"title": "Mine"})).json()
        fetched = (await client.get(f"/api/v1/documents/{created['id']}")).json()

        stamp = datetime.fromisoformat(fetched["updated_at"])
        naive = stamp.replace(tzinfo=timezone.utc)
        # Within a minute of now, which it cannot be if it were local time in
        # any zone this is likely to run in.
        assert abs((datetime.now(timezone.utc) - naive).total_seconds()) < 60

    async def test_it_moves_when_the_document_does(self, client) -> None:
        created = (await client.post("/api/v1/documents", json={"title": "Mine"})).json()
        first = (await client.get(f"/api/v1/documents/{created['id']}")).json()[
            "updated_at"
        ]

        await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={"ops": []},
            headers={"If-Match": f'W/"{created["version"]}-"'},
        )
        second = (await client.get(f"/api/v1/documents/{created['id']}")).json()[
            "updated_at"
        ]

        assert second >= first
