"""Marks and their readings, rebuilt from the op log.

They used to live only in the browser, so a reload cleared the clouds and the
text under them. Once the conversation was persisted that became the wrong way
round: the dialogue came back and the record of what changed did not, when the
record is the half that describes the artifact.

Everything needed was already stored. Each op keeps its inverse, and the
inverse of a rewrite carries the outgoing text -- the only place it survives
once the new text is in.
"""

from __future__ import annotations

import pytest

from studio.doc.apply import OpContext
from studio.doc.ops import InsertNode, SetText
from studio.doc.schema import ExperienceNode, StudioDoc, TextNode
from studio.persistence.repo import DocumentRepo

EXP = "exp_11111"
BULLET_A = "blt_aaaaa"
BULLET_B = "blt_bbbbb"


def doc() -> StudioDoc:
    return StudioDoc(
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Engineer",
                company="Northwind",
                bullets=[
                    TextNode(nid=BULLET_A, text="Rebuilt the ledger."),
                    TextNode(nid=BULLET_B, text="Led the migration."),
                ],
            )
        ],
    )


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


async def turn(store: DocumentRepo, document_id: str, turn_id: str, ops: list) -> None:
    await store.apply(
        document_id,
        ops,
        ctx=OpContext(granted_tiers={"A", "B", "C"}, actor="agent"),
        turn_id=turn_id,
    )


class TestTheLatestIssue:
    async def test_marks_and_readings_come_back(self, repo) -> None:
        state = await repo.create(doc(), title="mine")
        await turn(
            repo,
            state.id,
            "t1",
            [
                SetText(nid=BULLET_A, value="Cut settlement latency 96%."),
                SetText(nid=BULLET_B, value="Moved 40 services to async Python."),
            ],
        )

        found = await repo.revisions(state.id)

        assert found["revision"] == 1
        assert found["turn_id"] == "t1"
        assert [(entry["nid"], entry["mark"]) for entry in found["marks"]] == [
            (BULLET_A, 1),
            (BULLET_B, 2),
        ]
        # The reading under the cloud: what the line said before.
        assert found["marks"][0]["before"] == "Rebuilt the ledger."

    async def test_only_the_latest_issue_is_drawn(self, repo) -> None:
        """Clouding every change a document ever had would cover the sheet.

        The number counts every issue; the marks show the current one, which is
        what a cloud means.
        """
        state = await repo.create(doc(), title="mine")
        await turn(repo, state.id, "t1", [SetText(nid=BULLET_A, value="One.")])
        await turn(repo, state.id, "t2", [SetText(nid=BULLET_B, value="Two.")])

        found = await repo.revisions(state.id)

        assert found["revision"] == 2
        assert [entry["nid"] for entry in found["marks"]] == [BULLET_B]
        assert found["marks"][0]["before"] == "Led the migration."

    async def test_a_node_touched_twice_carries_one_mark(self, repo) -> None:
        """A region carries one number however many times it was worked on."""
        state = await repo.create(doc(), title="mine")
        await turn(
            repo,
            state.id,
            "t1",
            [
                SetText(nid=BULLET_A, value="First pass."),
                SetText(nid=BULLET_A, value="Second pass."),
            ],
        )

        found = await repo.revisions(state.id)

        assert len(found["marks"]) == 1
        # And the reading is the *original* text, not the intermediate one.
        assert found["marks"][0]["before"] == "Rebuilt the ledger."

    async def test_an_inserted_node_has_a_mark_but_no_reading(self, repo) -> None:
        """Offering an empty reading invites opening a cloud with nothing under it."""
        state = await repo.create(doc(), title="mine")
        await turn(
            repo,
            state.id,
            "t1",
            [
                InsertNode(
                    parent=EXP,
                    index=-1,
                    node={"nid": "blt_ccccc", "text": "Something new."},
                )
            ],
        )

        found = await repo.revisions(state.id)

        assert found["marks"][0]["before"] is None


class TestNothingToDraw:
    async def test_a_document_nobody_has_edited(self, repo) -> None:
        state = await repo.create(doc(), title="mine")

        assert await repo.revisions(state.id) == {
            "revision": 0,
            "turn_id": None,
            "marks": [],
        }

    async def test_a_direct_edit_is_not_an_issue(self, repo) -> None:
        """Typing into the document yourself does not raise a revision.

        The clouds mark what the assistant did, so you can find its work; your
        own edits you just made and can see.
        """
        state = await repo.create(doc(), title="mine")
        await repo.apply(
            state.id,
            [SetText(nid=BULLET_A, value="I typed this myself.")],
            ctx=OpContext(granted_tiers={"A", "B", "C"}, actor="user"),
        )

        assert (await repo.revisions(state.id))["revision"] == 0


class TestFieldEditsAreReadable:
    """A mark can name a field rather than a node.

    `set_personal_info` writes `personal.phone`; `set_entry_identity` writes
    `exp_7f3a2.title`. Reading only `set_text` inverses left every one of those
    with a mark you could click and nothing under it -- and those are the
    changes most worth checking, because they are claims about your history.
    """

    async def test_a_contact_change_keeps_its_previous_reading(self, repo) -> None:
        from studio.doc.ops import SetField
        from studio.doc.schema import PersonalInfo

        start = doc()
        start.personal = PersonalInfo(name="Alex Morgan", phone="0300 0000000")
        state = await repo.create(start, title="mine")

        await repo.apply(
            state.id,
            [SetField(target="personal.phone", value="0313 290 9993")],
            ctx=OpContext(granted_tiers={"A", "B", "C"}, actor="agent"),
            turn_id="t1",
        )

        found = await repo.revisions(state.id)

        assert found["marks"][0]["nid"] == "personal.phone"
        assert found["marks"][0]["before"] == "0300 0000000"

    async def test_a_retitled_job_keeps_its_previous_title(self, repo) -> None:
        from studio.doc.ops import SetField

        state = await repo.create(doc(), title="mine")
        await repo.apply(
            state.id,
            [SetField(target=f"{EXP}.title", value="Senior AI Engineer")],
            ctx=OpContext(granted_tiers={"A", "B", "C"}, actor="agent"),
            turn_id="t1",
        )

        found = await repo.revisions(state.id)

        assert found["marks"][0]["nid"] == f"{EXP}.title"
        assert found["marks"][0]["before"] == "Engineer"


class TestAnAdditionPointsAtWhatWasAdded:
    """An insert is about the node being added, not the list it goes into.

    ``InsertNode`` carries no ``nid`` at the top level -- the new id is inside
    ``node`` -- so the mark was read from ``parent``: "experience", "blocks",
    the page. None of those is a node with text, so the revision row for a line
    that had just been created rendered "Removed".
    """

    async def test_an_added_bullet(self, repo) -> None:
        from studio.doc.ops import InsertNode

        state = await repo.create(doc(), title="mine")
        await repo.apply(
            state.id,
            [
                InsertNode(
                    parent=EXP,
                    index=-1,
                    node={"nid": "blt_ccccc", "text": "Shipped the thing."},
                )
            ],
            ctx=OpContext(granted_tiers={"A", "B", "C"}, actor="agent"),
            turn_id="t1",
        )

        found = await repo.revisions(state.id)

        assert [entry["nid"] for entry in found["marks"]] == ["blt_ccccc"]

    def test_an_added_text_box(self) -> None:
        """The case that surfaced it: a footer marked against "blocks".

        Checked against ``_touched`` rather than through the repository: a
        document whose pages carry no frames fails the coverage gate before any
        of this is reached, and that gate is not what is under test here.
        """
        from studio.agent.tools import REGISTRY
        from studio.doc.apply import _touched
        from studio.doc.schema import PageNode

        start = doc()
        start.pages = [PageNode(nid="pag_aaaaa")]
        tool = REGISTRY.get("add_text_box")
        ops = tool.compile(tool.Args(value="Made with ResumeWesume"), start)

        marked = [nid for op in ops for nid in _touched(op)]

        # The block and its frame, not "blocks" and the page.
        assert "blocks" not in marked
        assert "pag_aaaaa" not in marked
        assert any(nid.startswith("txb_") for nid in marked)
        assert any(nid.startswith("frm_") for nid in marked)

    async def test_a_removal_still_says_removed(self, repo) -> None:
        """That label is correct when the node really is gone."""
        from studio.doc.ops import RemoveNode

        state = await repo.create(doc(), title="mine")
        await repo.apply(
            state.id,
            [RemoveNode(nid=BULLET_A, reason="asked")],
            ctx=OpContext(granted_tiers={"A", "B", "C"}, actor="agent"),
            turn_id="t1",
        )

        found = await repo.revisions(state.id)

        assert [entry["nid"] for entry in found["marks"]] == [BULLET_A]
