"""A turn that spends its whole budget looking at the resume.

Some models make exactly one tool call per turn. mistral-nemo:12b does, in
every turn measured -- and the loop assumes multi-round tool use, so a call
spent on a read is the entire budget and the edit never comes. Round two
arrives, the model has nothing left to spend, and it answers in prose.

Measured over twenty-four runs of the same edit, phrased four ways, the split
was exact: every turn that opened with a read applied nothing, every turn that
went straight to a tool edited. So a turn that has only looked is given one
more round with the document already in front of it.

The care here is all in *not* firing. A turn that answered a question, or that
stopped to ask for a fact the resume does not contain, is a turn working
correctly -- and this app's most important behaviour is refusing to invent, so
a nudge that read as "edit something" would be the worst possible regression.
"""

from __future__ import annotations

import pytest

from studio.agent.budget import TurnBudget
from studio.agent.loop import TurnRequest, TurnRunner
from studio.doc.schema import ExperienceNode, PersonalInfo, StudioDoc, TextNode
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, turn
from studio.persistence.repo import DocumentRepo
from studio.streaming import events as ev
from studio.streaming.channel import TurnChannel

EXP = "exp_11111"
BULLET = "blt_aaaaa"


def make_doc() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Senior Engineer",
                company="Northwind",
                years="2021 - Present",
                bullets=[TextNode(nid=BULLET, text="Worked on the ledger.")],
            )
        ],
    )


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


async def drive(repo: DocumentRepo, script, message: str = "tighten that bullet"):
    state = await repo.create(make_doc(), title="Alex Morgan")
    backend = ScriptedBackend(script)
    runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
    channel = TurnChannel(turn_id="turn-1", document_id=state.id)
    result = await runner.run(TurnRequest(document_id=state.id, message=message), channel)
    final = await repo.get(state.id)
    return result, final, channel, backend


def prompts(backend: ScriptedBackend) -> list[str]:
    """The user-role messages as the conversation finally stood.

    The last exchange, not every exchange. A message appended to the
    conversation is re-sent on each subsequent round, so counting across all of
    them counts one nudge as many.
    """
    if not backend.received:
        return []
    return [
        message["content"]
        for message in backend.received[-1]["messages"]
        if message.get("role") == "user"
    ]


def nudges(backend: ScriptedBackend) -> int:
    return sum(1 for text in prompts(backend) if "not changed it" in text)


def nudged(backend: ScriptedBackend) -> bool:
    return nudges(backend) > 0


READ = call_tool("read_document", {"section": "experience", "detail": "outline"})
EDIT = call_tool("rewrite_text", {"nid": BULLET, "value": "Rebuilt the ledger."})


class TestATurnThatOnlyLooked:
    async def test_it_is_given_one_more_round(self, repo: DocumentRepo) -> None:
        # Round 1 reads. Round 2 would have been prose and the end of the turn;
        # instead the model is asked to act, and does.
        result, final, _, backend = await drive(
            repo,
            [
                turn(READ, done("tool_calls")),
                turn(say("Here is the revised bullet."), done("stop")),
                turn(EDIT, done("tool_calls")),
                turn(say("Done."), done("stop")),
            ],
        )

        assert nudged(backend)
        assert result.applied == 1
        assert final.doc.experience[0].bullets[0].text == "Rebuilt the ledger."

    async def test_the_nudge_says_a_question_may_end_the_turn(
        self, repo: DocumentRepo
    ) -> None:
        # The whole risk of this feature. A model told to edit will edit, and
        # inventing an employer to satisfy a nudge is worse than any turn that
        # does nothing.
        _, _, _, backend = await drive(
            repo,
            [
                turn(READ, done("tool_calls")),
                turn(say("Which bullet did you mean?"), done("stop")),
                turn(say("Still asking."), done("stop")),
            ],
        )

        asked = next(text for text in prompts(backend) if "not changed it" in text)
        assert "asked a question" in asked
        assert "say" in asked and "stop" in asked

    async def test_a_turn_that_still_does_not_edit_simply_ends(
        self, repo: DocumentRepo
    ) -> None:
        result, final, _, _ = await drive(
            repo,
            [
                turn(READ, done("tool_calls")),
                turn(say("You will need to tell me which one."), done("stop")),
                turn(say("Still waiting."), done("stop")),
            ],
        )

        assert result.applied == 0
        assert final.doc.experience[0].bullets[0].text == "Worked on the ledger."

    async def test_it_is_offered_once(self, repo: DocumentRepo) -> None:
        _, _, _, backend = await drive(
            repo,
            [
                turn(READ, done("tool_calls")),
                turn(say("No."), done("stop")),
                turn(say("Still no."), done("stop")),
                turn(say("And again."), done("stop")),
            ],
        )

        assert nudges(backend) == 1


class TestWhenItMustNotFire:
    async def test_not_after_an_edit_has_landed(self, repo: DocumentRepo) -> None:
        # A model that did the work and stopped is finished. Asking again is
        # how a tailoring pass ends up making the same edit twice.
        _, _, _, backend = await drive(
            repo,
            [turn(EDIT, done("tool_calls")), turn(say("Done."), done("stop"))],
        )

        assert not nudged(backend)

    async def test_not_when_a_write_was_attempted_and_rejected(
        self, repo: DocumentRepo
    ) -> None:
        # It tried to edit and the engine refused. That is a turn with a
        # reason to stop, and the rejection already told it why -- this is for
        # a turn that never reached for a write at all.
        _, _, _, backend = await drive(
            repo,
            [
                turn(
                    call_tool("rewrite_text", {"nid": "blt_zzzzz", "value": "Nope."}),
                    done("tool_calls"),
                ),
                turn(say("That node is not there."), done("stop")),
            ],
        )

        assert not nudged(backend)

    async def test_not_when_the_model_called_nothing_at_all(
        self, repo: DocumentRepo
    ) -> None:
        # Answered from the outline it was given, without a tool. Nothing about
        # that says the model ran out of budget.
        _, _, _, backend = await drive(
            repo,
            [turn(say("Your summary is the weakest line."), done("stop"))],
            message="which line is weakest?",
        )

        assert not nudged(backend)
