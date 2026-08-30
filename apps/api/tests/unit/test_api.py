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

    async def test_list(self, client: AsyncClient) -> None:
        await seed(client)
        response = await client.get("/api/v1/documents")
        assert response.status_code == 200
        assert len(response.json()) == 1


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
