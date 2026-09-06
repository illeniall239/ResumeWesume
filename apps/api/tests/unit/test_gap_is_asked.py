"""A posting asking for something the résumé does not support.

Two things happen when a résumé is tailored to a posting. Either the posting's
requirements are already in there, in which case the work is reordering and
re-angling; or they are not, and the honest answer is to say which and ask.

It used to add the skill and mark the line for checking. That put a claim on
somebody's résumé that nobody had made, for them to catch afterwards from a
notice. Asking is simpler and puts the decision where it belongs — and the
answer arrives as ``user_request``, the strongest evidence there is, so what
they confirm goes on vouched for rather than flagged.
"""

from __future__ import annotations

import pytest

from studio.agent.budget import TurnBudget
from studio.agent.grounding import Grounder
from studio.agent.loop import TurnRequest, TurnRunner
from studio.agent.tools import REGISTRY
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, turn
from studio.persistence.repo import DocumentRepo
from studio.streaming.channel import TurnChannel
from studio.doc.schema import (
    ExperienceNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

GROUP = "sgp_ggggg"

POSTING = (
    "Senior Backend Engineer, Stripe. Deep Kubernetes experience required. "
    "You will own payment infrastructure end to end."
)


def make_doc() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid="exp_11111",
                title="Senior Engineer",
                company="Northwind",
                years="2021 - Present",
                bullets=[TextNode(nid="blt_aaaaa", text="Ran the cluster migration.")],
            )
        ],
        skills=[
            SkillGroup(
                nid=GROUP,
                key="technical",
                items=[SkillItem(nid="skl_ppppp", text="Python")],
            )
        ],
    )


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


def skills(doc: StudioDoc) -> list[str]:
    return [item.text for group in doc.skills for item in group.items]


class TestTheGapIsNotFilledQuietly:
    async def test_the_posting_alone_cannot_put_a_skill_on_the_page(
        self, repo: DocumentRepo
    ) -> None:
        # Kubernetes is in the advert and nowhere in the résumé. The model
        # asking for it on that basis is refused, and the résumé is unchanged.
        state = await repo.create(make_doc(), title="Alex Morgan")
        await repo.set_job_description(state.id, POSTING)

        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(
                        call_tool(
                            "add_skill",
                            {"skill": "Kubernetes", "group": "technical",
                             "evidence": "jd"},
                        ),
                        done("tool_calls"),
                    ),
                    turn(
                        say("Your résumé does not mention Kubernetes. Have you used it?"),
                        done("stop"),
                    ),
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=state.id, message="tailor this to the posting"),
            TurnChannel(turn_id="t", document_id=state.id),
        )

        final = await repo.get(state.id)
        assert skills(final.doc) == ["Python"]
        # And nothing was marked, because nothing was added.
        assert final.doc.unverified == []

    def test_the_refusal_says_what_to_do_instead(self) -> None:
        # A rejection that only says no gets retried unchanged.
        grounder = Grounder.build(
            make_doc(), user_message="tailor this", jd_keywords=["kubernetes"]
        )
        result = grounder.check("Kubernetes", "jd")
        assert not result.ok
        assert "ask" in result.detail.lower()

    def test_the_tool_does_not_offer_it_as_a_choice(self) -> None:
        # Refused before any of that is consulted: the argument model has no
        # such value, so a model reaching for it is corrected by the schema.
        schema = REGISTRY.get("add_skill").json_schema()
        assert schema["function"]["parameters"]["properties"]["evidence"]["enum"] == [
            "resume",
            "user_request",
        ]


class TestWhatTheUserConfirmsGoesOn:
    async def test_their_answer_is_the_evidence(self, repo: DocumentRepo) -> None:
        # The turn after the question. Their reply names the skill, so
        # `user_request` is satisfied and it goes on unmarked.
        state = await repo.create(make_doc(), title="Alex Morgan")
        await repo.set_job_description(state.id, POSTING)

        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(
                        call_tool(
                            "add_skill",
                            {"skill": "Kubernetes", "group": "technical",
                             "evidence": "user_request"},
                        ),
                        done("tool_calls"),
                    ),
                    turn(say("Added."), done("stop")),
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(
                document_id=state.id,
                message="yes, add Kubernetes -- the cluster migration was Kubernetes",
            ),
            TurnChannel(turn_id="t", document_id=state.id),
        )

        final = await repo.get(state.id)
        assert "Kubernetes" in skills(final.doc)
        # Vouched for, so no flag on the page.
        assert final.doc.unverified == []

    async def test_something_already_in_the_résumé_needs_no_asking(
        self, repo: DocumentRepo
    ) -> None:
        # The other of the two cases: the requirement is already supported, so
        # surfacing it into the skills list is not a new claim.
        state = await repo.create(make_doc(), title="Alex Morgan")
        runner = TurnRunner(
            repo=repo,
            backend=ScriptedBackend(
                [
                    turn(
                        call_tool(
                            "add_skill",
                            {"skill": "migration", "group": "technical",
                             "evidence": "resume"},
                        ),
                        done("tool_calls"),
                    ),
                    turn(say("Surfaced it."), done("stop")),
                ]
            ),
            budget=TurnBudget(),
        )
        await runner.run(
            TurnRequest(document_id=state.id, message="pull that out into skills"),
            TurnChannel(turn_id="t", document_id=state.id),
        )

        final = await repo.get(state.id)
        assert "migration" in skills(final.doc)
        assert final.doc.unverified == []


class TestTheModelIsToldToAsk:
    async def test_the_instruction_reaches_the_prompt(self, repo: DocumentRepo) -> None:
        # Refusing the call is the backstop. What actually produces the
        # behaviour is the model knowing to raise the gap in the first place.
        state = await repo.create(make_doc(), title="Alex Morgan")
        await repo.set_job_description(state.id, POSTING)

        backend = ScriptedBackend([turn(say("Done."), done("stop"))])
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        await runner.run(
            TurnRequest(document_id=state.id, message="tailor this"),
            TurnChannel(turn_id="t", document_id=state.id),
        )

        sent = "\n".join(
            str(message.get("content", ""))
            for request in backend.received
            for message in request.get("messages", [])
        )
        assert "TAILORING TO A JOB" in sent
        assert "ask how the user wants to proceed" in sent

    async def test_a_pasted_advert_is_not_an_instruction_to_start_editing(
        self, repo: DocumentRepo
    ) -> None:
        # The posting arrives by being pasted into the chat now, and a message
        # that is plainly an advert is not somebody asking for an edit. The
        # model is told to say which job it has and ask.
        state = await repo.create(make_doc(), title="Alex Morgan")

        backend = ScriptedBackend([turn(say("Got it."), done("stop"))])
        runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
        await runner.run(
            TurnRequest(document_id=state.id, message=POSTING),
            TurnChannel(turn_id="t", document_id=state.id),
        )

        sent = "\n".join(
            str(message.get("content", ""))
            for request in backend.received
            for message in request.get("messages", [])
        )
        assert "pastes the posting straight into the chat" in sent
        assert "ask whether to tailor the" in sent
