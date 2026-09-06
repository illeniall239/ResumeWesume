"""The import endpoint, through the real ASGI stack.

No mocked router and no mocked channel: these drive the actual FastAPI app with
a scripted model behind it, so wiring mistakes -- an unregistered router, a
header the browser cannot read, a detached task reading a closed upload file --
surface here instead of in the browser.

The upload validation tests are security tests as much as correctness ones. The
declared content type is a claim made by the caller; only the bytes are
evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from studio.llm.backend import ModelSpec, StreamEnd, TextDelta
from studio.llm.scripted import ScriptedBackend
from studio.main import app
from studio.persistence.providers import ProviderStore
from studio.persistence.repo import DocumentRepo
from studio.streaming.channel import TurnRegistry

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SPEC = ModelSpec(provider="scripted", model="scripted")

EXPERIENCE = json.dumps(
    {
        "entries": [
            {
                "title": "Senior Software Engineer",
                "company": "Northwind Systems",
                "years": "Mar 2021 - Present",
                "bullets": ["Rebuilt the payments ledger"],
            }
        ]
    }
)
EDUCATION = json.dumps(
    {"entries": [{"institution": "University of Texas at Austin", "degree": "B.S."}]}
)
SUMMARY = json.dumps({"summary": "Backend engineer."})


class StubFactory:
    """Stands in for the backend factory so no network is reachable.

    Implements ``resolve`` as well as ``from_settings`` because the routers ask
    for the *selected* model now, and a stub that only answers the older call
    would let a real provider lookup run in a test.
    """

    def __init__(self, backend: ScriptedBackend) -> None:
        self._backend = backend

    def from_settings(self) -> ScriptedBackend:
        return self._backend

    async def resolve(self, store: object = None) -> ScriptedBackend:
        return self._backend


def scripted(*replies: str) -> ScriptedBackend:
    backend = ScriptedBackend(
        [[TextDelta(text=reply), StreamEnd(finish_reason="stop")] for reply in replies],
        spec=SPEC,
    )
    app.state.backends = StubFactory(backend)
    return backend


@pytest.fixture
async def client():
    repo = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await repo.create_schema()
    app.state.repo = repo
    app.state.providers = ProviderStore(repo.session_factory)
    app.state.imports = TurnRegistry()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await app.state.imports.shutdown()
    await repo.dispose()


def pdf_bytes(name: str = "resume_single_column.pdf") -> bytes:
    return (FIXTURES / name).read_bytes()


async def upload(client: AsyncClient, data: bytes, *, filename: str = "cv.pdf"):
    return await client.post(
        "/api/v1/imports",
        files={"file": (filename, data, "application/pdf")},
    )


def events(body: str) -> list[dict]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


class TestUploadValidation:
    async def test_a_non_pdf_is_rejected_on_its_bytes(self, client) -> None:
        """Declared as a PDF, and is not one. The header is a claim; the magic
        number is evidence."""
        response = await upload(client, b"MZ\x90\x00 this is an executable" * 40)
        assert response.status_code == 415

    async def test_a_pdf_named_something_else_is_still_accepted(self, client) -> None:
        """The converse: the bytes decide, so a resume saved without an
        extension is not turned away."""
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        response = await client.post(
            "/api/v1/imports",
            files={"file": ("resume", pdf_bytes(), "application/octet-stream")},
        )
        assert response.status_code == 200

    async def test_an_oversized_upload_is_refused(self, client) -> None:
        from studio.routers.ingest import MAX_UPLOAD_BYTES

        oversized = b"%PDF-" + b"0" * MAX_UPLOAD_BYTES
        response = await upload(client, oversized)
        assert response.status_code == 413

    async def test_an_empty_upload_is_refused(self, client) -> None:
        response = await upload(client, b"")
        assert response.status_code == 400

    async def test_a_missing_file_field_is_a_422(self, client) -> None:
        response = await client.post("/api/v1/imports")
        assert response.status_code == 422


class TestStream:
    async def test_the_import_id_header_is_readable(self, client) -> None:
        """Without exposing it in CORS the browser cannot reattach to a stream
        it lost, which is the whole point of having an id."""
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        response = await upload(client, pdf_bytes())
        assert response.headers["x-import-id"]
        assert response.headers["content-type"].startswith("application/x-ndjson")

    async def test_the_stream_is_valid_ndjson_in_sequence(self, client) -> None:
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        response = await upload(client, pdf_bytes())
        stream = events(response.text)

        assert stream, "the stream was empty"
        assert [event["seq"] for event in stream] == list(range(1, len(stream) + 1))
        assert {event["turn_id"] for event in stream} == {
            response.headers["x-import-id"]
        }

    async def test_a_full_parse_reaches_import_ready(self, client) -> None:
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        response = await upload(client, pdf_bytes())
        stream = events(response.text)

        assert stream[0]["type"] == "import_started"
        assert stream[0]["filename"] == "cv.pdf"
        assert stream[0]["columns"] == 1

        ready = stream[-1]
        assert ready["type"] == "import_ready"
        assert ready["failed"] == 0
        assert ready["title"] == "Alex Morgan"

    async def test_the_preview_arrives_on_a_page(self, client) -> None:
        # The review screen draws sheets, and a document with no pages has
        # nothing to draw them from -- it fell back to the flowing renderer and
        # showed one continuous column, while the studio showed the same résumé
        # across two. A review that does not show what you are about to get is
        # not a review, so the importer lays out a page exactly as
        # POST /documents does for every document it creates.
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        ready = events((await upload(client, pdf_bytes())).text)[-1]

        pages = ready["doc"]["pages"]
        assert pages, "the preview arrived with nowhere to be drawn"
        # Every frame binds to something the renderer can find: the header, a
        # section key, or an entry nid. A frame pointing at nothing draws
        # nothing, and the section it stood for is silently missing.
        refs = [
            element["ref"] for element in pages[0]["elements"] if "ref" in element
        ]
        assert "personal" in refs
        assert "experience" in refs
        entries = {entry["nid"] for entry in ready["doc"]["experience"]}
        assert entries <= set(refs)

    async def test_the_checklist_is_announced_before_any_parsing(self, client) -> None:
        """So the user sees what was found in their resume immediately, rather
        than an empty box for the length of a model call."""
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        stream = events((await upload(client, pdf_bytes())).text)

        types = [event["type"] for event in stream]
        last_found = max(index for index, name in enumerate(types) if name == "section_found")
        first_parsed = min(
            index for index, name in enumerate(types) if name in {"section_parsed", "section_started"}
        )
        assert last_found < first_parsed

    async def test_deterministic_sections_never_call_the_model(self, client) -> None:
        """Contact and skills are regex and split. Three model calls, for the
        three model-parsed sections this fixture has."""
        backend = scripted(SUMMARY, EXPERIENCE, EDUCATION)
        stream = events((await upload(client, pdf_bytes())).text)

        assert backend.calls == 3
        parsed = {event["key"] for event in stream if event["type"] == "section_parsed"}
        assert {"contact", "skills"} <= parsed
        started = {event["key"] for event in stream if event["type"] == "section_started"}
        assert "contact" not in started and "skills" not in started

    async def test_the_parsed_document_is_previewable_and_re_creatable(
        self, client
    ) -> None:
        """``doc`` renders the preview; ``resume_data`` is what gets posted
        back. Both must be present and must agree."""
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        ready = events((await upload(client, pdf_bytes())).text)[-1]

        assert ready["doc"]["personal"]["name"] == "Alex Morgan"
        assert ready["doc"]["experience"][0]["company"] == "Northwind Systems"
        assert ready["doc"]["experience"][0]["nid"].startswith("exp_")
        assert ready["resume_data"]["personalInfo"]["name"] == "Alex Morgan"
        # The payload posted back carries no ids: they are minted server-side.
        assert "nid" not in json.dumps(ready["resume_data"])

    async def test_the_confirm_path_creates_a_real_document(self, client) -> None:
        """End to end: upload, then post the parse back the way the review
        screen will."""
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        ready = events((await upload(client, pdf_bytes())).text)[-1]

        created = await client.post(
            "/api/v1/documents",
            json={
                "title": ready["title"],
                "resume_data": ready["resume_data"],
                "source_markdown": ready["source_text"],
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["version"] == 1
        assert body["doc"]["personal"]["email"] == "alex.morgan@example.com"
        assert body["doc"]["experience"][0]["company"] == "Northwind Systems"
        assert body["doc"]["skills"][0]["items"][0]["text"] == "Python"


class TestContainment:
    async def test_one_failed_section_does_not_lose_the_others(self, client) -> None:
        """The promise the whole per-section design exists to keep."""
        scripted(SUMMARY, "I'm sorry, I can't do that", EDUCATION)
        stream = events((await upload(client, pdf_bytes())).text)

        failed = [event for event in stream if event["type"] == "section_failed"]
        assert [event["key"] for event in failed] == ["experience"]
        assert failed[0]["code"] == "no_json"
        # The section that failed still carries its source text, so the user
        # can see what we could not read rather than just that we could not.
        assert "Northwind Systems" in failed[0]["source_text"]

        ready = stream[-1]
        assert ready["type"] == "import_ready"
        assert ready["failed"] == 1
        assert ready["doc"]["experience"] == []
        assert ready["doc"]["summary"]["text"] == "Backend engineer."
        assert ready["doc"]["education"][0]["institution"].startswith("University")

    async def test_an_unreadable_pdf_ends_the_stream_with_a_usable_reason(
        self, client
    ) -> None:
        scripted()
        data = b"%PDF-1.7\nthis is not really a pdf at all\n" * 30
        stream = events((await upload(client, data)).text)

        assert stream[-1]["type"] == "error"
        assert stream[-1]["fatal"] is True
        assert stream[-1]["code"] in {"malformed", "no_text_layer", "empty"}

    async def test_a_dead_provider_still_produces_a_usable_import(
        self, client
    ) -> None:
        """Ollama not running must not mean "upload failed": contact, skills
        and section order are all still there."""
        from studio.llm.backend import BackendError

        app.state.backends = StubFactory(
            ScriptedBackend([], spec=SPEC, fail_with=BackendError("connection refused"))
        )
        stream = events((await upload(client, pdf_bytes())).text)

        ready = stream[-1]
        assert ready["type"] == "import_ready"
        assert ready["failed"] == 3
        assert ready["doc"]["personal"]["name"] == "Alex Morgan"
        assert ready["doc"]["skills"][0]["items"][0]["text"] == "Python"
        assert all(
            event["code"] == "provider_error"
            for event in stream
            if event["type"] == "section_failed"
        )


class TestReattachAndCancel:
    async def test_replay_from_a_sequence_number(self, client) -> None:
        """Losing a three-minute parse to a flaky connection means uploading
        and waiting all over again."""
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        first = await upload(client, pdf_bytes())
        import_id = first.headers["x-import-id"]
        original = events(first.text)

        resumed = await client.get(
            f"/api/v1/imports/{import_id}/stream", params={"from_seq": 2}
        )
        assert resumed.status_code == 200
        replayed = events(resumed.text)
        assert [event["seq"] for event in replayed] == [
            event["seq"] for event in original if event["seq"] > 2
        ]

    async def test_reattaching_to_an_unknown_import_is_a_404(self, client) -> None:
        response = await client.get("/api/v1/imports/nope/stream")
        assert response.status_code == 404

    async def test_cancelling_an_unknown_import_is_a_404(self, client) -> None:
        response = await client.delete("/api/v1/imports/nope")
        assert response.status_code == 404

    async def test_cancelling_a_finished_import_is_accepted(self, client) -> None:
        scripted(SUMMARY, EXPERIENCE, EDUCATION)
        first = await upload(client, pdf_bytes())
        response = await client.delete(f"/api/v1/imports/{first.headers['x-import-id']}")
        assert response.status_code == 202
        assert response.json()["status"] == "cancelling"


class TestTwoColumn:
    async def test_a_two_column_resume_imports(self, client) -> None:
        scripted(EXPERIENCE, EDUCATION, json.dumps({"entries": []}))
        stream = events(
            (await upload(client, pdf_bytes("resume_two_column.pdf"), filename="priya.pdf")).text
        )

        assert stream[0]["columns"] == 2
        ready = stream[-1]
        assert ready["title"] == "Priya Raman"
        assert ready["doc"]["personal"]["email"] == "priya.raman@example.com"
        # The sidebar's languages are their own group, not folded into skills.
        groups = {group["key"]: group for group in ready["doc"]["skills"]}
        assert "languages" in groups
        assert {item["text"] for item in groups["languages"]["items"]} == {
            "English",
            "Tamil",
            "German",
        }


class TestUnknownSections:
    """A résumé is not made only of the six sections we happen to model.

    Every heading in this fixture appears on real résumés and none of them is
    in the alias table. Before this they were recognised as sections, reported
    as skipped, and left out of the document -- so importing somebody's résumé
    quietly returned less than they uploaded.
    """

    async def test_sections_we_have_no_schema_for_are_still_imported(
        self, client
    ) -> None:
        scripted(EXPERIENCE, EDUCATION)
        stream = events(
            (
                await upload(
                    client,
                    pdf_bytes("resume_novel_sections.pdf"),
                    filename="anna.pdf",
                )
            ).text
        )
        ready = stream[-1]
        assert ready["type"] == "import_ready"

        custom = ready["resume_data"]["customSections"]
        # Named by the résumé, not by us -- which is what covers a heading
        # nobody has thought of, including one in another language.
        assert "PUBLICATIONS" in custom
        assert "LEADERSHIP" in custom
        assert "INTERESTS" in custom
        assert "FORMATION CONTINUE" in custom

    async def test_volunteer_experience_is_not_filed_as_paid_work(
        self, client
    ) -> None:
        """The one case that stated something false about the person.

        "Volunteer Experience" matched the word "experience" inside it and was
        merged into the work history. Unpaid work in somebody's paid history is
        worse than a section we did not import: it is a section we got wrong.
        """
        scripted(EXPERIENCE, EDUCATION)
        stream = events(
            (
                await upload(
                    client,
                    pdf_bytes("resume_novel_sections.pdf"),
                    filename="anna.pdf",
                )
            ).text
        )
        ready = stream[-1]

        assert "VOLUNTEER EXPERIENCE" in ready["resume_data"]["customSections"]
        # And the paid history is only what the Experience heading covered.
        companies = {
            entry["company"] for entry in ready["resume_data"]["workExperience"]
        }
        assert "Pilsen community centre" not in " ".join(companies)

    async def test_nothing_from_the_source_goes_missing(self, client) -> None:
        """The promise, stated as one assertion.

        Every non-heading line of the fixture has to appear somewhere in the
        document that comes back -- in a section we model, or in one named by
        the résumé's own heading.
        """
        scripted(EXPERIENCE, EDUCATION)
        stream = events(
            (
                await upload(
                    client,
                    pdf_bytes("resume_novel_sections.pdf"),
                    filename="anna.pdf",
                )
            ).text
        )
        imported = json.dumps(stream[-1]["resume_data"])

        for phrase in (
            "Scaling laws for sparse models",
            "On gradient noise under heavy tails",
            "Pilsen community centre",
            "Graduate Student Association",
            "annual symposium",
            "history of cartography",
            "apprentissage automatique",
        ):
            assert phrase in imported, f"lost from the résumé: {phrase}"

    async def test_a_custom_section_keeps_its_place_in_the_order(
        self, client
    ) -> None:
        """Publications sits between Experience and Education in the source.

        Appending custom sections after the modelled ones would reshuffle
        somebody's résumé on the way in, which reads as the importer having
        rewritten their document.
        """
        scripted(EXPERIENCE, EDUCATION)
        stream = events(
            (
                await upload(
                    client,
                    pdf_bytes("resume_novel_sections.pdf"),
                    filename="anna.pdf",
                )
            ).text
        )
        order = [
            meta["key"] for meta in stream[-1]["resume_data"]["sectionMeta"]
        ]

        assert order.index("experience") < order.index("PUBLICATIONS")
        assert order.index("PUBLICATIONS") < order.index("education")
