"""The turn loop against a document that has a layout.

``test_loop.py`` drives every scripted turn against a v1 flowing document, so
none of it exercises the coverage gate, the frame cascade, or the drift guards'
new obligation to restore layout with content. This file runs the same kind of
turns against a **v2** document and asserts the property the whole canvas
design rests on: *the content tools were not supposed to notice.*

Everything here goes through the real loop, the real gates, the real guards and
real persistence. Only the model is scripted -- the same seam ``test_loop.py``
uses, for the same reason.
"""

from __future__ import annotations

import pytest

from studio.agent.budget import TurnBudget
from studio.agent.loop import TurnRequest, TurnRunner
from studio.doc.apply import _first_orphan
from studio.doc.autolayout import layout
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    Rect,
    ShapeElement,
    EducationNode,
    ExperienceNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)
from studio.llm.scripted import ScriptedBackend, call_tool, done, say, turn
from studio.persistence.repo import DocumentRepo
from studio.streaming import events as ev
from studio.streaming.channel import TurnChannel

EXP = "exp_11111"
EXP_B = "exp_22222"
BULLET_A = "blt_aaaaa"
BULLET_B = "blt_bbbbb"
GROUP = "sgp_ggggg"
SKILL_PY = "skl_ppppp"


def paged_doc() -> StudioDoc:
    """The same shape ``test_loop.py`` uses, laid out onto pages."""
    doc = StudioDoc(
        personal=PersonalInfo(
            name="Alex Morgan", email="alex@example.com", phone="+1-555-0142"
        ),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Senior Engineer",
                company="Northwind",
                years="2021 - Present",
                bullets=[
                    TextNode(nid=BULLET_A, text="Rebuilt the ledger."),
                    TextNode(nid=BULLET_B, text="Led the migration."),
                ],
            ),
            ExperienceNode(
                nid=EXP_B,
                title="Engineer",
                company="Contoso",
                years="2019 - 2021",
                bullets=[TextNode(nid="blt_ccccc", text="Shipped the API.")],
            ),
        ],
        education=[
            EducationNode(nid="edu_11111", institution="UT", degree="BS Computer Science")
        ],
        skills=[
            SkillGroup(
                nid=GROUP,
                key="technical",
                items=[
                    SkillItem(nid=SKILL_PY, text="Python"),
                    SkillItem(nid="skl_ggggo", text="Go"),
                ],
            )
        ],
        sections=list(DEFAULT_SECTIONS),
    )
    doc.pages = layout(doc)
    return doc


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


async def run_turn(
    repo: DocumentRepo,
    backend: ScriptedBackend,
    message: str,
    *,
    doc: StudioDoc | None = None,
    consent: set[str] | None = None,
):
    state = await repo.create(doc or paged_doc(), title="Alex Morgan")
    runner = TurnRunner(repo=repo, backend=backend, budget=TurnBudget())
    channel = TurnChannel(turn_id="turn-1", document_id=state.id)
    request = TurnRequest(
        document_id=state.id, message=message, consent_tokens=consent or set()
    )
    result = await runner.run(request, channel)
    final = await repo.get(state.id)
    return result, final, channel


def emitted(channel: TurnChannel, kind: type) -> list:
    return [event for event in channel.replay_from(0) if isinstance(event, kind)]


def script(*calls):
    return ScriptedBackend(
        [
            turn(say("Working on it."), *calls, done("tool_calls")),
            turn(say("Done."), done("stop")),
        ]
    )


class TestContentToolsDoNotNoticeTheLayout:
    """The claim the whole design rests on.

    Content and layout are separate subtrees precisely so the sixteen content
    tools keep addressing the same nids in the same lists. If any of these
    needed to change, the separation failed.
    """

    async def test_rewriting_a_bullet(self, repo: DocumentRepo) -> None:
        result, final, _ = await run_turn(
            repo,
            script(call_tool("rewrite_text", {"nid": BULLET_A, "value": "Cut latency 96%."})),
            "tighten my first bullet",
        )
        assert result.status == "ok"
        assert final.doc.experience[0].bullets[0].text == "Cut latency 96%."
        assert _first_orphan(final.doc) is None

    async def test_adding_a_bullet_needs_no_new_frame(self, repo: DocumentRepo) -> None:
        """Coverage is over containers, so a new leaf is covered by its
        ancestor the moment it exists. If it were over leaves, every insert
        would have to mint a frame and this would fail."""
        result, final, _ = await run_turn(
            repo,
            script(call_tool("add_bullet", {"parent": EXP, "value": "Mentored two juniors."})),
            "add a bullet about mentoring",
        )
        assert result.status == "ok"
        assert len(final.doc.experience[0].bullets) == 3
        assert _first_orphan(final.doc) is None

    async def test_adding_a_job(self, repo: DocumentRepo) -> None:
        result, final, _ = await run_turn(
            repo,
            script(
                call_tool(
                    "add_experience",
                    {"title": "Staff Engineer", "company": "Globex", "years": "2018"},
                )
            ),
            "add a job at Globex",
        )
        assert result.status == "ok"
        assert len(final.doc.experience) == 3
        assert _first_orphan(final.doc) is None

    async def test_reordering_bullets(self, repo: DocumentRepo) -> None:
        result, final, _ = await run_turn(
            repo,
            script(call_tool("reorder_bullets", {"parent": EXP, "order": [BULLET_B, BULLET_A]})),
            "put the migration bullet first",
        )
        assert result.status == "ok"
        assert final.doc.experience[0].bullets[0].nid == BULLET_B
        assert _first_orphan(final.doc) is None

    async def test_editing_personal_info(self, repo: DocumentRepo) -> None:
        result, final, _ = await run_turn(
            repo,
            script(call_tool("set_personal_info", {"field": "email", "value": "a@new.com"})),
            "change my email",
            consent={"set_personal_info:email"},
        )
        assert result.status == "ok"
        assert final.doc.personal.email == "a@new.com"

    async def test_adding_a_skill(self, repo: DocumentRepo) -> None:
        result, final, _ = await run_turn(
            repo,
            script(
                call_tool(
                    "add_skill",
                    {"skill": "Rust", "group": "technical", "evidence": "user_request"},
                )
            ),
            "add Rust to my skills",
        )
        assert result.status == "ok"
        assert any(item.text == "Rust" for item in final.doc.skills[0].items)
        assert _first_orphan(final.doc) is None


class TestRemovalTakesItsLayoutWithIt:
    async def test_removing_a_job_removes_the_frame_bound_to_it(
        self, repo: DocumentRepo
    ) -> None:
        """Otherwise the frame is left pointing at nothing and the coverage
        gate refuses the batch -- the tool would stop working the moment a
        document had a layout."""
        result, final, channel = await run_turn(
            repo,
            script(call_tool("remove_entry", {"nid": EXP_B, "reason": "asked for"})),
            "delete the Contoso job",
            consent={f"remove_entry:{EXP_B}"},
        )

        assert result.status == "ok", emitted(channel, ev.PatchRejected)
        assert [entry.nid for entry in final.doc.experience] == [EXP]
        refs = [
            getattr(element, "ref", None)
            for page in final.doc.pages
            for element in page.elements
        ]
        assert EXP_B not in refs
        assert _first_orphan(final.doc) is None

    async def test_a_removal_is_still_one_undo_unit(self, repo: DocumentRepo) -> None:
        # The entry and its frames go out in one batch, so undo brings both
        # back together. Cascading inside `_do_remove` instead would give one
        # op two effects and make the inverse impossible to express.
        _, final, _ = await run_turn(
            repo,
            script(call_tool("remove_entry", {"nid": EXP_B, "reason": "asked for"})),
            "delete the Contoso job",
            consent={f"remove_entry:{EXP_B}"},
        )
        restored, _ = await repo.reverse(final.id, direction="undo")
        assert [entry.nid for entry in restored.doc.experience] == [EXP, EXP_B]
        assert _first_orphan(restored.doc) is None


class TestGuardsPreserveLayout:
    async def test_an_ungranted_removal_is_reverted_with_its_frame(
        self, repo: DocumentRepo
    ) -> None:
        """A guard correction is written through ``replace``, which never meets
        the coverage gate -- so restoring content without its frame would
        persist a document the engine would have refused, and the user's next
        edit would be rejected for something they never did."""
        result, final, channel = await run_turn(
            repo,
            script(call_tool("remove_entry", {"nid": EXP_B, "reason": "unasked"})),
            "tighten my bullets",  # no consent for a removal
        )

        assert [entry.nid for entry in final.doc.experience] == [EXP, EXP_B]
        assert _first_orphan(final.doc) is None

    async def test_pages_are_not_replaced_with_pre_turn_geometry(
        self, repo: DocumentRepo
    ) -> None:
        """The risk the plan flagged: a correction that carries stale `pages`
        would silently undo a drag that landed during the turn."""
        doc = paged_doc()
        doc.pages[0].elements[0].rect.x = 123.0

        _, final, _ = await run_turn(
            repo,
            script(call_tool("remove_entry", {"nid": EXP_B, "reason": "unasked"})),
            "tighten my bullets",
            doc=doc,
        )

        assert final.doc.pages[0].elements[0].rect.x == 123.0


class TestCanvasTools:
    async def test_adding_a_page(self, repo: DocumentRepo) -> None:
        result, final, _ = await run_turn(
            repo, script(call_tool("add_page", {})), "add a blank page"
        )
        assert result.status == "ok"
        assert len(final.doc.pages) == 2

    async def test_removing_a_page_that_holds_the_resume_is_refused(
        self, repo: DocumentRepo
    ) -> None:
        doc = paged_doc()
        # A second, blank page -- otherwise the "a resume needs one page" rule
        # fires first and this would not be testing the coverage refusal.
        doc.pages.append(doc.pages[0].model_copy(deep=True, update={"nid": "pag_zzzzz", "elements": []}))

        result, final, channel = await run_turn(
            repo,
            script(call_tool("remove_page", {"page": 1, "reason": "asked"})),
            "delete page 1",
            consent={"remove_page:1"},
            doc=doc,
        )

        assert len(final.doc.pages) == 2
        assert final.doc.experience  # nothing was lost
        rejects = emitted(channel, ev.PatchRejected)
        assert rejects, "the model should be told why, not silently ignored"
        assert "only place" in rejects[0].message

    async def test_removing_a_box_returns_content_to_the_flow(
        self, repo: DocumentRepo
    ) -> None:
        doc = paged_doc()
        frame = next(
            element
            for element in doc.pages[0].elements
            if getattr(element, "ref", None) == EXP
        )

        result, final, channel = await run_turn(
            repo,
            script(call_tool("remove_element", {"nid": frame.nid, "reason": "asked"})),
            "remove that box",
            doc=doc,
        )

        assert result.status == "ok", emitted(channel, ev.PatchRejected)
        # The words survive; the section frame draws them again.
        assert final.doc.experience[0].nid == EXP
        assert _first_orphan(final.doc) is None

    async def test_arranging_two_shapes(self, repo: DocumentRepo) -> None:
        """The whole point of the tool, through the real loop and real guards.

        The turn is ungated: `arrange` is Tier A because nothing it can produce
        loses a word or moves anything off the page, which is what a free
        geometry tool could not promise.
        """
        doc = paged_doc()
        doc.pages[0].elements.extend(
            [
                ShapeElement(nid="shp_aaaaa", shape="rect", rect=Rect(x=40, y=40, w=100, h=50)),
                ShapeElement(nid="shp_bbbbb", shape="rect", rect=Rect(x=300, y=200, w=60, h=90)),
            ]
        )

        result, final, channel = await run_turn(
            repo,
            script(
                call_tool(
                    "arrange",
                    {"preset": "align_left", "nids": ["shp_aaaaa", "shp_bbbbb"]},
                )
            ),
            "line those two boxes up on the left",
            doc=doc,
        )

        assert result.status == "ok", emitted(channel, ev.PatchRejected)
        placed = {e.nid: e.rect for page in final.doc.pages for e in page.elements}
        assert placed["shp_aaaaa"].x == placed["shp_bbbbb"].x == 40
        assert _first_orphan(final.doc) is None

    async def test_arranging_never_loses_a_word(self, repo: DocumentRepo) -> None:
        """The drift guards read text, so a layout turn must leave text alone
        -- otherwise a correction fires and reverts an innocent arrangement."""
        doc = paged_doc()
        frames = [
            e.nid
            for e in doc.pages[0].elements
            if getattr(e, "ref", None) in ("summary", "experience")
        ]

        _, final, _ = await run_turn(
            repo,
            script(call_tool("arrange", {"preset": "align_top", "nids": frames})),
            "line up those two boxes",
            doc=doc,
        )

        assert final.doc.summary is not None
        assert final.doc.summary.text == "Backend engineer."
        assert [b.text for b in final.doc.experience[0].bullets] == [
            "Rebuilt the ledger.",
            "Led the migration.",
        ]

    async def test_a_bad_arrangement_is_reported_not_applied(
        self, repo: DocumentRepo
    ) -> None:
        # The model gets a sentence it can retry from rather than a silent
        # nothing, which it would report to the user as success.
        result, final, channel = await run_turn(
            repo,
            script(
                call_tool("arrange", {"preset": "align_left", "nids": ["shp_nope0"]})
            ),
            "line those up",
        )

        before = paged_doc()
        assert [e.rect.y for page in final.doc.pages for e in page.elements] == [
            e.rect.y for page in before.pages for e in page.elements
        ]

    async def test_reaching_for_a_content_node_is_refused_with_a_hint(
        self, repo: DocumentRepo
    ) -> None:
        result, final, channel = await run_turn(
            repo,
            script(call_tool("remove_element", {"nid": BULLET_A, "reason": "asked"})),
            "remove that",
        )

        assert len(final.doc.experience[0].bullets) == 2, "the bullet must survive"
        rejects = emitted(channel, ev.PatchRejected)
        assert rejects and "remove_entry" in rejects[0].message
