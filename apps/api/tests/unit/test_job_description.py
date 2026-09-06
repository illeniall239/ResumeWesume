"""The posting a résumé is aimed at.

Stored on the document rather than carried on a turn. Tailoring is a
conversation — you ask, you read it back, you ask again — and a posting sent
with one message survived exactly one exchange, after which every follow-up
worked with no idea what the sheet was being aimed at.

Also here: what happens when the posting asks for something the résumé does not
support. Nothing is added on the advert's word alone — the gap is raised as a
question, and the answer is what lets the skill on.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from studio.agent.budget import TurnBudget
from studio.agent.loop import TurnRequest, TurnRunner
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


class TestASkillThePostingAsksForAndTheResumeDoesNotSupport:
    """A posting asking for a skill is not evidence the person has it.

    This used to be an evidence class of its own: the assistant could add the
    skill and the line was marked for checking. That put a claim on somebody's
    résumé that nobody had made, for them to catch afterwards from a notice.
    A gap is a question, so it is asked -- and the answer arrives as
    ``user_request``, which is the strongest evidence there is.
    """

    async def test_the_posting_is_no_longer_evidence_at_all(
        self, client: AsyncClient
    ) -> None:
        from studio.agent.grounding import Grounder
        from studio.doc.schema import StudioDoc

        grounder = Grounder.build(
            StudioDoc(), user_message="tailor this", jd_keywords=["kubernetes"]
        )
        result = grounder.check("Kubernetes", "jd")
        assert not result.ok
        # And says what to do instead, because a rejection that only says no
        # gets retried unchanged.
        assert "ask" in result.detail.lower()

    async def test_the_tool_will_not_accept_it(self, client: AsyncClient) -> None:
        # Not reachable at all: the argument model no longer has the value, so
        # a model asking for it is refused before any of this is consulted.
        from studio.agent.tools import REGISTRY

        schema = REGISTRY.get("add_skill").json_schema()
        allowed = schema["function"]["parameters"]["properties"]["evidence"]["enum"]
        assert allowed == ["resume", "user_request"]

    async def test_what_the_user_confirms_goes_on_unmarked(
        self, client: AsyncClient
    ) -> None:
        # The point of asking. Their answer is the evidence, and a skill they
        # have vouched for needs no flag on the page.
        from studio.agent.grounding import Grounder
        from studio.doc.schema import StudioDoc

        grounder = Grounder.build(
            StudioDoc(), user_message="yes, add Kubernetes -- I ran the migration"
        )
        assert grounder.check("Kubernetes", "user_request").ok


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

        # The closing tag, not the opening one: the system prompt names
        # `<job_description>` when it explains what to do with a posting, so
        # only a real block has both ends.
        assert "</job_description>" not in sent

    async def test_the_turn_it_is_pasted_in_does_not_carry_it_twice(
        self, client: AsyncClient
    ) -> None:
        # The posting arrives by being pasted into the chat, so on that one
        # turn the message *is* the advert. Repeating it under
        # <job_description> doubles the longest thing in the prompt, and on a
        # 4,096-token context what falls off the front is the system prompt and
        # the tool schemas.
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await seed(client)
        state = await repo.get(created["id"])

        sent = await self._run(repo, state, POSTING, on_turn=POSTING)

        assert "</job_description>" not in sent
        # It is there once, as the message the person actually sent.
        assert "Kubernetes and Terraform" in sent

    async def test_every_turn_after_it_does(self, client: AsyncClient) -> None:
        # Which is the whole reason it is stored: the next instruction is
        # "tailor it", and that message carries nothing.
        repo: DocumentRepo = client.repo  # type: ignore[attr-defined]
        created = await seed(client)
        await client.put(
            f"/api/v1/documents/{created['id']}/job-description", json={"text": POSTING}
        )
        state = await repo.get(created["id"])

        sent = await self._run(repo, state, "now tailor it")

        assert "</job_description>" in sent

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
