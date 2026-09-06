"""Where the data lives, and getting a copy of it out.

Both halves of the same problem: this program is installed rather than signed
into, so the only copy of anybody's résumé is a file on their own machine. If
that file is at an address that moves, they lose it; if there is no way to copy
it, they lose it the first time the disk does.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from studio import config
from studio.doc.schema import PersonalInfo, StudioDoc, TextNode
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
    "additional": {"technicalSkills": ["Python"]},
}


async def restore(client: AsyncClient, bundle: dict):
    """Post a bundle the way the file picker does."""
    return await client.post(
        "/api/v1/restore",
        files={"file": ("backup.json", json.dumps(bundle).encode(), "application/json")},
    )


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


class TestWhereTheDatabaseLives:
    """It used to be `./data/studio.db`, relative to the working directory.

    The Makefile cds into `apps/api` first, so a person who started the server
    from the repository root, or a shortcut, or a service wrapper, got a
    brand-new empty database and every résumé appeared to have vanished --
    while the real file sat intact one directory away.
    """

    def test_the_default_does_not_depend_on_where_it_was_started(self) -> None:
        assert config.default_data_dir().is_absolute()

    def test_it_is_under_the_directory_this_machine_keeps_data_in(self) -> None:
        # Not the home directory itself, and not the install: somewhere the OS
        # already understands as application data, which is what makes it
        # survive moving or reinstalling the program.
        assert config.default_data_dir().name.lower().startswith("resumewesume")

    def test_an_existing_database_is_brought_along(self, tmp_path, monkeypatch) -> None:
        # The move would otherwise do the very thing it exists to prevent:
        # somebody upgrades and their résumés are gone as far as they can tell.
        monkeypatch.chdir(tmp_path)
        legacy = tmp_path / "data"
        legacy.mkdir()
        (legacy / "studio.db").write_bytes(b"the real database")

        settings = config.Settings(data_dir=tmp_path / "new")
        url = settings.resolved_database_url()

        moved = tmp_path / "new" / "studio.db"
        assert moved.read_bytes() == b"the real database"
        assert moved.as_posix() in url
        # Moved, not copied: two databases would diverge, and the adoption
        # would run again on a file that is no longer the live one.
        assert not (legacy / "studio.db").exists()

    def test_its_write_ahead_log_comes_too(self, tmp_path, monkeypatch) -> None:
        # Left behind, a WAL is applied to nothing -- and any committed work it
        # still holds is lost with it.
        monkeypatch.chdir(tmp_path)
        legacy = tmp_path / "data"
        legacy.mkdir()
        (legacy / "studio.db").write_bytes(b"db")
        (legacy / "studio.db-wal").write_bytes(b"wal")

        config.Settings(data_dir=tmp_path / "new").resolved_database_url()
        assert (tmp_path / "new" / "studio.db-wal").read_bytes() == b"wal"

    def test_a_database_already_there_is_never_overwritten(
        self, tmp_path, monkeypatch
    ) -> None:
        # The one way this could destroy data, so it is refused outright.
        monkeypatch.chdir(tmp_path)
        legacy = tmp_path / "data"
        legacy.mkdir()
        (legacy / "studio.db").write_bytes(b"old")
        new = tmp_path / "new"
        new.mkdir()
        (new / "studio.db").write_bytes(b"live")

        config.Settings(data_dir=new).resolved_database_url()
        assert (new / "studio.db").read_bytes() == b"live"
        assert (legacy / "studio.db").read_bytes() == b"old"

    def test_an_explicit_database_url_still_wins(self, tmp_path) -> None:
        settings = config.Settings(
            data_dir=tmp_path, database_url="postgresql+asyncpg://host/db"
        )
        assert settings.resolved_database_url() == "postgresql+asyncpg://host/db"


class TestTakingACopyOfEverything:
    async def test_it_is_offered_as_a_download(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/backup")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        # Dated, so keeping several is the obvious thing to do with them.
        assert "resumewesume-backup-" in response.headers["content-disposition"]

    async def test_it_is_not_shadowed_by_a_document_id(self, client: AsyncClient) -> None:
        # `/documents/backup` was matched by `GET /documents/{document_id}`,
        # registered first, which read "backup" as an id and answered
        # "Document not found" -- a 404 for a route that exists.
        assert (await client.get("/api/v1/documents/backup")).status_code == 404
        assert (await client.get("/api/v1/backup")).status_code == 200

    async def test_every_résumé_is_in_it(self, client: AsyncClient) -> None:
        first = await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        canvas_id = first.json()["canvas_id"]
        await client.post(
            "/api/v1/documents",
            json={"title": "Stripe", "resume_data": SEED, "canvas_id": canvas_id},
        )

        bundle = (await client.get("/api/v1/backup")).json()

        assert [entry["title"] for entry in bundle["documents"]] == ["Alex Morgan", "Stripe"]
        assert len(bundle["canvases"]) == 1
        # Every version of a résumé, keyed to the canvas that holds them, so a
        # restore rebuilds the register rather than a pile of loose documents.
        assert {entry["canvas_id"] for entry in bundle["documents"]} == {canvas_id}

    async def test_a_document_comes_back_in_the_shape_that_creates_one(
        self, client: AsyncClient
    ) -> None:
        # The point of exporting content rather than copying the database file:
        # a restore is replay through an endpoint that already exists, not a
        # migration of an opaque blob tied to the schema that wrote it.
        created = await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        bundle = (await client.get("/api/v1/backup")).json()
        entry = bundle["documents"][0]

        restored = await client.post(
            "/api/v1/documents", json={"title": entry["title"], "doc": entry["doc"]}
        )
        assert restored.status_code == 201
        assert restored.json()["doc"]["personal"]["name"] == "Alex Morgan"
        assert (
            restored.json()["doc"]["experience"][0]["company"]
            == created.json()["doc"]["experience"][0]["company"]
        )

    async def test_the_posting_a_résumé_is_aimed_at_survives(
        self, client: AsyncClient
    ) -> None:
        created = await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        await client.put(
            f"/api/v1/documents/{created.json()['id']}/job-description",
            json={"text": "Senior Backend Engineer, Stripe."},
        )

        bundle = (await client.get("/api/v1/backup")).json()
        assert "Stripe" in bundle["documents"][0]["job_description"]

    async def test_images_travel_with_it(self, client: AsyncClient) -> None:
        # A résumé whose photo is missing is not a résumé that was backed up.
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        await repo.store_asset(
            sha256="a" * 64,
            document_id=created.json()["id"],
            mime="image/png",
            data=b"\x89PNG pretend",
            width=100,
            height=100,
            filename="headshot.png",
        )

        bundle = (await client.get("/api/v1/backup")).json()
        assert len(bundle["assets"]) == 1
        assert base64.b64decode(bundle["assets"][0]["data"]) == b"\x89PNG pretend"

    async def test_it_says_what_it_is(self, client: AsyncClient) -> None:
        # A reader has to be able to refuse a bundle it does not understand
        # rather than half-restoring it.
        bundle = (await client.get("/api/v1/backup")).json()
        assert bundle["format"] == "resumewesume.backup"
        assert bundle["version"] == 1
        assert bundle["exported_at"]

    async def test_an_empty_machine_still_produces_a_file(
        self, client: AsyncClient
    ) -> None:
        # Somebody backing up before they have written anything should get a
        # valid empty bundle, not an error.
        bundle = json.loads((await client.get("/api/v1/backup")).text)
        assert bundle["documents"] == []
        assert bundle["canvases"] == []


class TestPuttingItBack:
    """An export you cannot import is half a backup.

    And the half that matters: nobody finds out whether their backup works
    until the day it has to.
    """

    async def test_a_wiped_register_comes_back(self, client: AsyncClient) -> None:
        first = await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        canvas_id = first.json()["canvas_id"]
        await client.post(
            "/api/v1/documents",
            json={"title": "Stripe", "resume_data": SEED, "canvas_id": canvas_id},
        )
        await client.put(
            f"/api/v1/documents/{first.json()['id']}/job-description",
            json={"text": "Senior Backend Engineer, Stripe."},
        )
        bundle = (await client.get("/api/v1/backup")).json()

        # The disaster.
        await client.delete(f"/api/v1/canvases/{canvas_id}")
        assert (await client.get("/api/v1/documents")).json() == []

        result = await restore(client, bundle)
        assert result.json()["documents"] == 2

        after = (await client.get("/api/v1/backup")).json()
        assert sorted(entry["title"] for entry in after["documents"]) == [
            "Alex Morgan",
            "Stripe",
        ]
        # The versions are back on the canvas that held them, not as a pile of
        # loose documents.
        assert [entry["id"] for entry in after["canvases"]] == [canvas_id]
        assert {entry["canvas_id"] for entry in after["documents"]} == {canvas_id}
        assert any("Stripe" in (e["job_description"] or "") for e in after["documents"])

    async def test_the_words_survive_the_round_trip(self, client: AsyncClient) -> None:
        created = await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        bundle = (await client.get("/api/v1/backup")).json()
        await client.delete(f"/api/v1/canvases/{created.json()['canvas_id']}")
        await restore(client, bundle)

        doc = (await client.get("/api/v1/documents")).json()[0]["doc"]
        assert doc["personal"]["name"] == "Alex Morgan"
        assert doc["experience"][0]["company"] == "Northwind"
        assert doc["experience"][0]["bullets"][0]["text"] == "Rebuilt the ledger."
        # The layout too: a résumé restored without its pages would open as one
        # long column, which is not the document that was backed up.
        assert doc["pages"]

    async def test_images_come_back_byte_for_byte(self, client: AsyncClient) -> None:
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        await repo.store_asset(
            sha256="c" * 64,
            document_id=created.json()["id"],
            mime="image/png",
            data=b"\x89PNG a photograph",
            width=10,
            height=10,
            filename="me.png",
        )
        bundle = (await client.get("/api/v1/backup")).json()

        fresh = DocumentRepo("sqlite+aiosqlite:///:memory:")
        await fresh.create_schema()
        try:
            await fresh.restore_everything(bundle)
            asset = await fresh.get_asset("c" * 64)
            assert asset is not None
            assert asset.data == b"\x89PNG a photograph"
        finally:
            await fresh.dispose()

    async def test_it_never_overwrites_what_is_already_here(
        self, client: AsyncClient
    ) -> None:
        # The rule the whole thing turns on. A restore is reached for when
        # something has already gone wrong, and the one outcome worse than not
        # recovering is destroying what survived.
        created = await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        bundle = (await client.get("/api/v1/backup")).json()

        document_id = created.json()["id"]
        await client.post(
            f"/api/v1/documents/{document_id}/ops",
            json={
                "ops": [
                    {
                        "op": "set_field",
                        "target": "personal.name",
                        "value": "Alex Morgan-Reeve",
                    }
                ]
            },
        )

        result = await restore(client, bundle)
        assert result.json()["documents"] == 0
        assert result.json()["skipped"] >= 1
        # The newer name stands: the backup's older one did not come back over it.
        fetched = await client.get(f"/api/v1/documents/{document_id}")
        assert fetched.json()["doc"]["personal"]["name"] == "Alex Morgan-Reeve"

    async def test_restoring_the_same_file_twice_changes_nothing(
        self, client: AsyncClient
    ) -> None:
        await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        bundle = (await client.get("/api/v1/backup")).json()

        again = await restore(client, bundle)
        assert again.json()["documents"] == 0
        assert len((await client.get("/api/v1/documents")).json()) == 1

    async def test_it_brings_back_only_what_is_missing(self, client: AsyncClient) -> None:
        # "I deleted one résumé by mistake": the bundle restores that one and
        # steps over everything else.
        keep = await client.post(
            "/api/v1/documents", json={"title": "Keep me", "resume_data": SEED}
        )
        lost = await client.post(
            "/api/v1/documents", json={"title": "Deleted by mistake", "resume_data": SEED}
        )
        bundle = (await client.get("/api/v1/backup")).json()
        await client.delete(f"/api/v1/canvases/{lost.json()['canvas_id']}")

        result = await restore(client, bundle)
        assert result.json()["documents"] == 1

        listed = (await client.get("/api/v1/documents")).json()
        assert sorted(entry["title"] for entry in listed) == [
            "Deleted by mistake",
            "Keep me",
        ]
        assert keep.json()["id"] in {entry["id"] for entry in listed}


class TestABackupItCannotRead:
    """Refused whole, rather than half-restored.

    A partial restore is the worst answer available: it leaves a register that
    looks recovered and is not, and nothing says which half is missing.
    """

    async def test_a_file_that_is_not_a_backup(self, client: AsyncClient) -> None:
        response = await restore(client, {"hello": "world"})
        assert response.status_code == 400
        assert "not a ResumeWesume backup" in response.json()["detail"]

    async def test_a_version_this_build_does_not_know(self, client: AsyncClient) -> None:
        response = await restore(
            client, {"format": "resumewesume.backup", "version": 99, "documents": []}
        )
        assert response.status_code == 400
        assert "cannot read" in response.json()["detail"]

    async def test_a_file_that_is_not_json(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/restore",
            files={"file": ("notes.txt", b"just some words", "text/plain")},
        )
        assert response.status_code == 400
        assert "JSON" in response.json()["detail"]

    async def test_an_empty_file(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/restore", files={"file": ("empty.json", b"", "application/json")}
        )
        assert response.status_code == 400

    async def test_a_document_that_will_not_load_stops_the_whole_restore(
        self, client: AsyncClient
    ) -> None:
        # Validated through the schema on the way in, so a hand-edited or
        # half-written bundle is refused here rather than stored and found to
        # be unreadable the next time somebody opens it.
        await client.post(
            "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
        )
        bundle = (await client.get("/api/v1/backup")).json()
        bundle["documents"][0]["id"] = "a-different-id"
        bundle["documents"][0]["doc"]["experience"] = "not a list at all"

        response = await restore(client, bundle)
        assert response.status_code == 400
        # Naming which résumé, because the raw validation error is a wall of
        # type complaints about a document the person cannot see.
        assert "Alex Morgan" in response.json()["detail"]
        assert "nothing was restored" in response.json()["detail"]
        # And nothing from it landed.
        assert len((await client.get("/api/v1/documents")).json()) == 1
