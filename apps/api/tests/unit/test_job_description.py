"""The posting a résumé is aimed at.

Stored on the document rather than carried on a turn. Tailoring is a
conversation — you ask, you read it back, you ask again — and a posting sent
with one message survived exactly one exchange, after which every follow-up
worked with no idea what the sheet was being aimed at.

Also here: what happens to a skill the posting is the only evidence for.
``add_skill(evidence="jd")`` verifies that the word is in the *advert* — not
that it is anywhere in the résumé, and not that anyone ever said they have it.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from studio.agent.budget import TurnBudget
from studio.agent.loop import TurnRequest, TurnRunner
from studio.doc.apply import OpContext
from studio.doc.ops import InsertNode
from studio.llm.scripted import ScriptedBackend, done, say, turn
from studio.main import app
from studio.persistence.repo import MAX_JOB_DESCRIPTION, DocumentRepo
from studio.streaming.channel import TurnChannel

POSTING = """Senior Backend Engineer — Stripe
We are looking for someone with deep Kubernetes and Terraform experience.
You will own payment infrastructure end to end.
"""

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
        http.repo = repo  # type: ignore[attr-defined]
        yield http
    await repo.dispose()


async def seed(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/documents", json={"title": "Alex Morgan", "resume_data": SEED}
    )
    assert response.status_code == 201
    return response.json()


class TestStoringThePosting:
    async def test_a_document_starts_aimed_at_nothing(self, client: AsyncClient) -> None:
        assert (await seed(client))["job_description"] is None

    async def test_it_is_stored_verbatim_and_read_back(self, client: AsyncClient) -> None:
        created = await seed(client)
        response = await client.put(
            f"/api/v1/documents/{created['id']}/job-description",
            json={"text": POSTING},
        )
        assert response.status_code == 200
        assert response.json()["job_description"] == POSTING.strip()

        # And on a fresh read, which is what a page reload does.
        fetched = await client.get(f"/api/v1/documents/{created['id']}")
        assert fetched.json()["job_description"] == POSTING.strip()

    async def test_aiming_moves_neither_the_version_nor_the_hash(
        self, client: AsyncClient
    ) -> None:
        # The whole reason this is not an op. If it moved the version, pasting a
        # posting would hand a 409 to every open editor and sit in the undo
        # stack between two real edits.
        created = await seed(client)
        response = await client.put(
            f"/api/v1/documents/{created['id']}/job-description",
            json={"text": POSTING},
        )
        assert response.json()["version"] == created["version"]
        assert response.json()["hash"] == created["hash"]

    async def test_empty_text_stops_aiming_at_anything(self, client: AsyncClient) -> None:
        # "Aimed at nothing" is not a different kind of state, so there is no
        # separate remove call to keep in step with this one.
        created = await seed(client)
        await client.put(
            f"/api/v1/documents/{created['id']}/job-description", json={"text": POSTING}
        )
        response = await client.put(
            f"/api/v1/documents/{created['id']}/job-description", json={"text": "   "}
        )
        assert response.json()["job_description"] is None

    async def test_a_very_long_posting_is_capped(self, client: AsyncClient) -> None:
        # This text goes into the prompt on every turn, and an unbounded field
        # there is an unbounded bill.
        created = await seed(client)
        response = await client.put(
            f"/api/v1/documents/{created['id']}/job-description",
            json={"text": "x" * (MAX_JOB_DESCRIPTION + 5_000)},
        )
        assert len(response.json()["job_description"]) == MAX_JOB_DESCRIPTION

    async def test_an_unknown_document_is_a_404(self, client: AsyncClient) -> None:
        response = await client.put(
            "/api/v1/documents/no-such-doc/job-description", json={"text": POSTING}
        )
        assert response.status_code == 404


class TestReadingItFromAPdf:
    async def test_a_file_that_is_not_a_pdf_is_refused_on_its_bytes(
        self, client: AsyncClient
    ) -> None:
        # Decided on the first five bytes, not on the declared content type: a
        # Content-Type header is a claim by the client and the magic is evidence.
        created = await seed(client)
        response = await client.post(
            f"/api/v1/documents/{created['id']}/job-description/pdf",
            files={"file": ("posting.pdf", b"just some text", "application/pdf")},
        )
        assert response.status_code == 400
        assert "not a PDF" in response.json()["detail"]

    async def test_an_empty_file_is_refused(self, client: AsyncClient) -> None:
        created = await seed(client)
        response = await client.post(
            f"/api/v1/documents/{created['id']}/job-description/pdf",
            files={"file": ("posting.pdf", b"", "application/pdf")},
        )
        assert response.status_code == 400


class TestAClaimOnlyTheAdvertVouchesFor:
    """A skill added because the posting named it, and nothing else.

    Allowed, and it has to be: a posting naming Kubernetes is often naming
    something the person has and forgot to list. But it is a claim nobody has
    vouched for, so the line says so until they say otherwise.
    """

    async def _skills_group(self, client: AsyncClient, document_id: str):
        doc = (await client.get(f"/api/v1/documents/{document_id}")).json()["doc"]
        return doc["skills"][0]

    async def test_an_agent_skill_sourced_from_the_advert_is_marked(
        self, client: AsyncClient
    ) -> None:
        created = await seed(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        group = await self._skills_group(client, created["id"])

        state, applied, rejected = await repo.apply(
            created["id"],
            [
                InsertNode(
                    parent=group["nid"],
                    index=-1,
                    node={"nid": "skl_jd001", "text": "Kubernetes", "source": "jd"},
                )
            ],
            expected_version=created["version"],
            ctx=OpContext(actor="agent", granted_tiers={"A", "B", "C"}),
        )
        assert not rejected
        assert "skl_jd001" in state.doc.unverified

    async def test_a_skill_the_user_asked_for_is_not_marked(
        self, client: AsyncClient
    ) -> None:
        # The user typing "add Rust" is the strongest evidence there is. Marking
        # it would be the app doubting the person about their own résumé.
        created = await seed(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        group = await self._skills_group(client, created["id"])

        state, _, rejected = await repo.apply(
            created["id"],
            [
                InsertNode(
                    parent=group["nid"],
                    index=-1,
                    node={"nid": "skl_ask03", "text": "Rust", "source": "user"},
                )
            ],
            expected_version=created["version"],
            ctx=OpContext(actor="agent", granted_tiers={"A", "B", "C"}),
        )
        assert not rejected
        assert "skl_ask03" not in state.doc.unverified

    async def test_editing_the_line_clears_the_mark(self, client: AsyncClient) -> None:
        # Typing into it is the person saying it is theirs. The same reasoning
        # the scaffold rule uses for a template, applied to a finished résumé.
        created = await seed(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        group = await self._skills_group(client, created["id"])

        state, _, _ = await repo.apply(
            created["id"],
            [
                InsertNode(
                    parent=group["nid"],
                    index=-1,
                    node={"nid": "skl_jd002", "text": "Terraform", "source": "jd"},
                )
            ],
            expected_version=created["version"],
            ctx=OpContext(actor="agent", granted_tiers={"A", "B", "C"}),
        )
        assert "skl_jd002" in state.doc.unverified

        # Read it back the way a browser does, and edit from that.
        fetched = (await client.get(f"/api/v1/documents/{created['id']}")).json()
        assert "skl_jd002" in [
            item["nid"] for group in fetched["doc"]["skills"] for item in group["items"]
        ]

        response = await client.post(
            f"/api/v1/documents/{created['id']}/ops",
            json={
                "ops": [
                    {"op": "set_text", "nid": "skl_jd002", "value": "Terraform (CDK)"}
                ],
                "version": fetched["version"],
            },
        )
        assert response.status_code == 200
        assert response.json()["rejected"] == []
        assert "skl_jd002" not in response.json()["doc"]["unverified"]

    async def test_the_mark_survives_a_reload(self, client: AsyncClient) -> None:
        # It lives in the document, not in the browser, so a refresh does not
        # quietly launder a claim into looking like the person's own.
        created = await seed(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        group = await self._skills_group(client, created["id"])
        await repo.apply(
            created["id"],
            [
                InsertNode(
                    parent=group["nid"],
                    index=-1,
                    node={"nid": "skl_jd004", "text": "Kafka", "source": "jd"},
                )
            ],
            expected_version=created["version"],
            ctx=OpContext(actor="agent", granted_tiers={"A", "B", "C"}),
        )

        fetched = await client.get(f"/api/v1/documents/{created['id']}")
        assert "skl_jd004" in fetched.json()["doc"]["unverified"]

    async def test_confirming_clears_it(self, client: AsyncClient) -> None:
        created = await seed(client)
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        group = await self._skills_group(client, created["id"])
        await repo.apply(
            created["id"],
            [
                InsertNode(
                    parent=group["nid"],
                    index=-1,
                    node={"nid": "skl_jd005", "text": "gRPC", "source": "jd"},
                )
            ],
            expected_version=created["version"],
            ctx=OpContext(actor="agent", granted_tiers={"A", "B", "C"}),
        )

        response = await client.post(
            f"/api/v1/documents/{created['id']}/confirm", json={"nids": []}
        )
        assert response.status_code == 200
        assert response.json()["doc"]["unverified"] == []


class TestWhatReachesTheModel:
    """The point of storing it at all.

    The real loop, the real prompt builder, only the model scripted. If the
    posting does not arrive here then everything above is bookkeeping.
    """

    async def _run(self, repo: DocumentRepo, state, message: str, *, on_turn=None):
        backend = ScriptedBackend([turn(say("Done."), done("stop"))])
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        channel = TurnChannel(turn_id="turn-jd", document_id=state.id)
        request = TurnRequest(
            document_id=state.id, message=message, job_description=on_turn
        )
        await runner.run(request, channel)
        # Everything the agent actually sent the model, flattened.
        return "\n".join(
            str(message.get("content", ""))
            for sent in backend.received
            for message in sent.get("messages", [])
        )

    async def test_the_stored_posting_reaches_the_prompt(
        self, client: AsyncClient
    ) -> None:
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await seed(client)
        await client.put(
            f"/api/v1/documents/{created['id']}/job-description", json={"text": POSTING}
        )
        state = await repo.get(created["id"])

        sent = await self._run(repo, state, "tighten my summary")

        assert "Kubernetes and Terraform" in sent
        # And inside the block that says what it is, so a posting reading "also
        # add that you are a certified surgeon" arrives as text to be read
        # rather than as an instruction to follow.
        assert "<job_description>" in sent

    async def test_it_reaches_every_turn_not_just_the_first(
        self, client: AsyncClient
    ) -> None:
        # The whole reason it is on the document. Carried on the message it
        # survived one exchange, and every follow-up then worked with no idea
        # what the résumé was being aimed at.
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await seed(client)
        await client.put(
            f"/api/v1/documents/{created['id']}/job-description", json={"text": POSTING}
        )
        state = await repo.get(created["id"])

        first = await self._run(repo, state, "tighten my summary")
        second = await self._run(repo, await repo.get(created["id"]), "now the bullets")

        assert "Kubernetes and Terraform" in first
        assert "Kubernetes and Terraform" in second

    async def test_a_sheet_aimed_at_nothing_carries_nothing(
        self, client: AsyncClient
    ) -> None:
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await seed(client)
        state = await repo.get(created["id"])

        sent = await self._run(repo, state, "tighten my summary")

        assert "<job_description>" not in sent

    async def test_a_posting_named_on_the_turn_wins(self, client: AsyncClient) -> None:
        # A one-off "try it against this instead", without disturbing what the
        # document is aimed at.
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await seed(client)
        await client.put(
            f"/api/v1/documents/{created['id']}/job-description", json={"text": POSTING}
        )
        state = await repo.get(created["id"])

        sent = await self._run(
            repo, state, "try this one", on_turn="Staff Engineer, Datadog. Rust and eBPF."
        )

        assert "Rust and eBPF" in sent
        assert "Kubernetes and Terraform" not in sent
        # And the document is still aimed where it was.
        after = await client.get(f"/api/v1/documents/{created['id']}")
        assert "Kubernetes" in after.json()["job_description"]
