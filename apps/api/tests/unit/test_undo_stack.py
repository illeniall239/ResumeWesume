"""Undo and redo over the real op log.

``test_history`` covers the chooser as arithmetic. This covers the thing the
person presses: a repository, a document, and the versions that actually land
between one edit and the next.

Every case here is a bug that shipped, and each has the same shape -- a version
somebody never made, sitting between them and the one they meant. Undo is
chosen by walking the log, so anything written into that log is a step on the
way back, whether or not it is anything a person would recognise.
"""

from __future__ import annotations

import pytest

from studio.doc.apply import OpContext
from studio.doc.ops import SetText
from studio.doc.schema import PersonalInfo, StudioDoc, TextNode
from studio.persistence.repo import DocumentRepo

NID = "sum_00001"


def make_doc(text: str = "one") -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", email="alex@example.com"),
        summary=TextNode(nid=NID, text=text, style="plain"),
    )


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


async def write(repo: DocumentRepo, doc_id: str, text: str, *, actor: str = "user"):
    await repo.apply(doc_id, [SetText(nid=NID, value=text)], ctx=OpContext(actor=actor))


async def summary(repo: DocumentRepo, doc_id: str) -> str:
    return (await repo.get(doc_id)).doc.summary.text


class TestTheMeasurePassIsNotAnEdit:
    """The client corrects frame geometry from what the browser rendered.

    It lands after almost every edit that changes how much room a line takes.
    Filed as a person's work it cost a press per edit -- the first Ctrl+Z moved
    geometry nobody could see and read as a dead key -- and it killed redo on
    the press after every undo.
    """

    async def test_undo_reaches_the_edit_under_the_correction(
        self, repo: DocumentRepo
    ) -> None:
        state = await repo.create(make_doc(), title="R")
        await write(repo, state.id, "two")
        # The measure pass, arriving a round trip after the edit it describes.
        await write(repo, state.id, "two", actor="layout")

        assert (await repo.reverse(state.id, direction="undo")) is not None
        assert await summary(repo, state.id) == "one"

    async def test_a_correction_after_an_undo_does_not_kill_redo(
        self, repo: DocumentRepo
    ) -> None:
        # The sequence a person actually produces: edit, undo, and the pass
        # fires again because the text changed back and the frames no longer
        # fit it. Counted as a fresh edit, that cleared the redo stack -- so
        # redo was dead on the press immediately after every undo.
        state = await repo.create(make_doc(), title="R")
        await write(repo, state.id, "two")
        await repo.reverse(state.id, direction="undo")
        await write(repo, state.id, "one", actor="layout")

        assert (await repo.reverse(state.id, direction="redo")) is not None
        assert await summary(repo, state.id) == "two"

    async def test_a_real_edit_still_clears_the_redo_stack(
        self, repo: DocumentRepo
    ) -> None:
        # The behaviour the exemption must not cost: branching abandons the
        # redo stack, or redo would silently overwrite what was just typed.
        state = await repo.create(make_doc(), title="R")
        await write(repo, state.id, "two")
        await repo.reverse(state.id, direction="undo")
        await write(repo, state.id, "branch")

        assert (await repo.reverse(state.id, direction="redo")) is None


class TestAGuardCorrectionDoesNotEndUndo:
    """A drift guard rewrites the document wholesale after a turn.

    That write cannot be inverted op by op, so an undo aimed at it refused --
    and the chooser is deterministic, so it refused every press after that too.
    One guard firing left the document permanently unable to undo anything.
    """

    async def test_undo_still_reaches_the_edits_below_it(
        self, repo: DocumentRepo
    ) -> None:
        state = await repo.create(make_doc(), title="R")
        await write(repo, state.id, "two")
        await write(repo, state.id, "three")

        corrected = (await repo.get(state.id)).doc.model_copy(
            update={"summary": TextNode(nid=NID, text="three, corrected", style="plain")}
        )
        await repo.replace(state.id, corrected, reason="drift_guard")

        assert (await repo.reverse(state.id, direction="undo")) is not None
        assert await summary(repo, state.id) == "two"
        assert (await repo.reverse(state.id, direction="undo")) is not None
        assert await summary(repo, state.id) == "one"


class TestUndoingTheUndoOfATurn:
    """Undo this turn, from the transcript, is a snapshot restore.

    It moved the document and wrote nothing into the log, so the next Ctrl+Z
    could not see it: the press aimed at the turn *below* and replayed inverses
    against a document they had already been applied to. Nothing appeared to
    happen, and every press after that was one step out of step with the page.
    """

    async def test_the_press_after_it_puts_the_turn_back(
        self, repo: DocumentRepo
    ) -> None:
        state = await repo.create(make_doc(), title="R")
        await write(repo, state.id, "two")

        before = await repo.checkpoint(state.id, label="before turn", turn_id="t1")
        await write(repo, state.id, "the agent wrote this", actor="agent")
        await repo.revert(state.id, before)
        assert await summary(repo, state.id) == "two"

        assert (await repo.reverse(state.id, direction="undo")) is not None
        assert await summary(repo, state.id) == "the agent wrote this"

    async def test_and_redo_undoes_it_again(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc(), title="R")
        await write(repo, state.id, "two")
        before = await repo.checkpoint(state.id, label="before turn", turn_id="t1")
        await write(repo, state.id, "the agent wrote this", actor="agent")
        await repo.revert(state.id, before)
        await repo.reverse(state.id, direction="undo")

        assert (await repo.reverse(state.id, direction="redo")) is not None
        assert await summary(repo, state.id) == "two"

    async def test_undo_keeps_walking_back_past_it(self, repo: DocumentRepo) -> None:
        # Two presses, two states, no repeats: the revert, then the edit under
        # the turn it reverted.
        state = await repo.create(make_doc(), title="R")
        await write(repo, state.id, "two")
        before = await repo.checkpoint(state.id, label="before turn", turn_id="t1")
        await write(repo, state.id, "the agent wrote this", actor="agent")
        await repo.revert(state.id, before)

        seen = []
        for _ in range(4):
            if await repo.reverse(state.id, direction="undo") is None:
                break
            seen.append(await summary(repo, state.id))

        assert seen == ["the agent wrote this", "two", "one"]


class TestOrdinaryUse:
    """The path everything above is protecting."""

    async def test_three_edits_undo_and_redo_in_order(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc(), title="R")
        for text in ("two", "three", "four"):
            await write(repo, state.id, text)

        undone = []
        for _ in range(3):
            await repo.reverse(state.id, direction="undo")
            undone.append(await summary(repo, state.id))
        assert undone == ["three", "two", "one"]

        redone = []
        for _ in range(3):
            await repo.reverse(state.id, direction="redo")
            redone.append(await summary(repo, state.id))
        assert redone == ["two", "three", "four"]

    async def test_an_empty_stack_refuses_rather_than_failing(
        self, repo: DocumentRepo
    ) -> None:
        state = await repo.create(make_doc(), title="R")
        assert await repo.reverse(state.id, direction="undo") is None
        assert await repo.reverse(state.id, direction="redo") is None


class TestRenamingASectionHeading:
    """The one text on the page nobody could change.

    Every op reaches a nid, and a section is not a node -- it is an entry in
    ``doc.sections`` saying what the résumé calls this part of itself. So
    "Experience" could not be renamed to "Selected Work" by hand or by the
    assistant, on any document.
    """

    async def test_the_heading_can_be_set(self, repo: DocumentRepo) -> None:
        from studio.doc.ops import SetField
        from studio.doc.schema import SectionMeta

        doc = make_doc()
        doc.sections = [SectionMeta(key="experience", label="Experience", order=0)]
        state = await repo.create(doc, title="R")

        _, applied, rejected = await repo.apply(
            state.id,
            [SetField(target="section.experience", value="Selected Work")],
            ctx=OpContext(actor="user"),
        )
        assert not rejected and len(applied) == 1

        after = (await repo.get(state.id)).doc
        assert after.sections[0].label == "Selected Work"
        # The key is what the tools, the importer and the layout all address.
        # Only the label moves.
        assert after.sections[0].key == "experience"

    async def test_it_is_undoable_like_any_other_edit(
        self, repo: DocumentRepo
    ) -> None:
        from studio.doc.ops import SetField
        from studio.doc.schema import SectionMeta

        doc = make_doc()
        doc.sections = [SectionMeta(key="experience", label="Experience", order=0)]
        state = await repo.create(doc, title="R")
        await repo.apply(
            state.id,
            [SetField(target="section.experience", value="Selected Work")],
            ctx=OpContext(actor="user"),
        )

        assert await repo.reverse(state.id, direction="undo") is not None
        assert (await repo.get(state.id)).doc.sections[0].label == "Experience"

    async def test_a_document_that_declares_none_still_renames(
        self, repo: DocumentRepo
    ) -> None:
        # Nothing declared is rendered from the default set, so the headings on
        # screen are real and renaming one has to work.
        state = await repo.create(make_doc(), title="R")
        assert (await repo.get(state.id)).doc.sections == []

        from studio.doc.ops import SetField

        _, applied, rejected = await repo.apply(
            state.id,
            [SetField(target="section.skills", value="Toolkit")],
            ctx=OpContext(actor="user"),
        )
        assert not rejected and len(applied) == 1

        after = (await repo.get(state.id)).doc
        assert {meta.key: meta.label for meta in after.sections}["skills"] == "Toolkit"
        # And the rest of the default set came with it, rather than the
        # document being left with one section and losing the others.
        assert len(after.sections) > 1

    async def test_a_section_that_does_not_exist_says_which_do(
        self, repo: DocumentRepo
    ) -> None:
        from studio.doc.ops import SetField
        from studio.doc.schema import SectionMeta

        doc = make_doc()
        doc.sections = [SectionMeta(key="experience", label="Experience", order=0)]
        state = await repo.create(doc, title="R")

        _, _, rejected = await repo.apply(
            state.id,
            [SetField(target="section.nosuch", value="x")],
            ctx=OpContext(actor="user"),
        )
        assert rejected
        assert "experience" in rejected[0].message

    def test_renaming_a_heading_is_the_lowest_tier(self) -> None:
        # A heading is what the résumé calls a part of itself, not a claim
        # about the person, so it needs no consent gate.
        from studio.doc.index import NodeIndex
        from studio.doc.apply import tier_of
        from studio.doc.ops import SetField

        doc = make_doc()
        op = SetField(target="section.experience", value="Selected Work")
        assert tier_of(op, NodeIndex(doc)) == "A"
