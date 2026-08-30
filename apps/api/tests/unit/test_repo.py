"""Persistence and optimistic concurrency.

The conflict tests are the point: they are what make it safe for the agent and
the user's cursor to write to the same document.
"""

from __future__ import annotations

import pytest

from studio.doc.apply import OpContext
from studio.doc.ops import RemoveNode, SetText
from studio.doc.schema import ExperienceNode, PersonalInfo, StudioDoc, TextNode
from studio.persistence.repo import DocumentRepo, VersionConflict

BULLET = "blt_aaaaa"
EXP = "exp_11111"


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
                bullets=[TextNode(nid=BULLET, text="Rebuilt the ledger.")],
            )
        ],
    )


@pytest.fixture
async def repo():
    store = DocumentRepo("sqlite+aiosqlite:///:memory:")
    await store.create_schema()
    yield store
    await store.dispose()


def tiers() -> OpContext:
    return OpContext(granted_tiers={"A", "B", "C"})


class TestRoundTrip:
    async def test_create_and_get(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc(), title="Alex Morgan")
        loaded = await repo.get(state.id)
        assert loaded is not None
        assert loaded.version == 1
        assert loaded.doc.personal.name == "Alex Morgan"
        assert loaded.content_hash == state.content_hash

    async def test_etag_shape(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc())
        assert state.etag.startswith('W/"1-')

    async def test_missing_document(self, repo: DocumentRepo) -> None:
        assert await repo.get("nope") is None


class TestApply:
    async def test_apply_bumps_version_and_hash(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc())
        updated, applied, rejected = await repo.apply(
            state.id, [SetText(nid=BULLET, value="Cut latency 96%.")], ctx=tiers()
        )
        assert len(applied) == 1 and not rejected
        assert updated.version == 2
        assert updated.content_hash != state.content_hash
        assert updated.doc.experience[0].bullets[0].text == "Cut latency 96%."

    async def test_rejected_batch_does_not_burn_a_version(
        self, repo: DocumentRepo
    ) -> None:
        """A rejected agent batch must not invalidate the client's ETag —
        otherwise every failed tool call forces a spurious refetch."""
        state = await repo.create(make_doc())
        after, applied, rejected = await repo.apply(
            state.id, [SetText(nid="blt_ghost", value="x")], ctx=tiers()
        )
        assert applied == [] and len(rejected) == 1
        assert after.version == state.version
        assert after.content_hash == state.content_hash

    async def test_ops_are_logged_with_inverses(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc())
        await repo.apply(state.id, [SetText(nid=BULLET, value="new")], ctx=tiers())
        # The log is what powers undo and rebase; an op without an inverse is
        # not undoable, so assert we recorded one.
        async with repo._session() as session:  # noqa: SLF001 - white-box on purpose
            from sqlalchemy import select

            from studio.persistence.models import DocumentOp

            rows = (await session.execute(select(DocumentOp))).scalars().all()
        assert len(rows) == 1
        assert rows[0].inverse is not None
        assert rows[0].inverse["value"] == "Rebuilt the ledger."


class TestConcurrency:
    async def test_stale_write_conflicts(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc())
        await repo.apply(
            state.id, [SetText(nid=BULLET, value="first")], expected_version=1, ctx=tiers()
        )
        # A second writer still holding version 1 must not clobber.
        with pytest.raises(VersionConflict) as excinfo:
            await repo.apply(
                state.id,
                [SetText(nid=BULLET, value="second")],
                expected_version=1,
                ctx=tiers(),
            )
        assert excinfo.value.current_version == 2

    async def test_conflict_carries_ops_since_for_rebase(
        self, repo: DocumentRepo
    ) -> None:
        """The client needs to know *what* changed, not just that it did, or a
        409 costs the user their in-flight typing."""
        state = await repo.create(make_doc())
        await repo.apply(
            state.id, [SetText(nid=BULLET, value="agent edit")], expected_version=1, ctx=tiers()
        )
        with pytest.raises(VersionConflict) as excinfo:
            await repo.apply(
                state.id, [SetText(nid=BULLET, value="user edit")], expected_version=1, ctx=tiers()
            )
        assert len(excinfo.value.ops_since) == 1
        assert excinfo.value.ops_since[0]["nid"] == BULLET

    async def test_matching_version_succeeds(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc())
        updated, _, _ = await repo.apply(
            state.id, [SetText(nid=BULLET, value="ok")], expected_version=1, ctx=tiers()
        )
        assert updated.version == 2


class TestCheckpoints:
    async def test_revert_restores_content(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc())
        checkpoint = await repo.checkpoint(state.id, label="before turn")
        await repo.apply(state.id, [RemoveNode(nid=BULLET)], ctx=tiers())

        mid = await repo.get(state.id)
        assert mid is not None and mid.doc.experience[0].bullets == []

        reverted = await repo.revert(state.id, checkpoint)
        assert [b.text for b in reverted.doc.experience[0].bullets] == [
            "Rebuilt the ledger."
        ]

    async def test_revert_moves_forward_never_back(self, repo: DocumentRepo) -> None:
        """History is append-only: a revert is a new version, so a client
        holding an old ETag still gets a conflict rather than false agreement."""
        state = await repo.create(make_doc())
        checkpoint = await repo.checkpoint(state.id)
        await repo.apply(state.id, [SetText(nid=BULLET, value="changed")], ctx=tiers())
        reverted = await repo.revert(state.id, checkpoint)
        assert reverted.version == 3

    async def test_whole_turn_reverts_as_one_unit(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc())
        checkpoint = await repo.checkpoint(state.id, turn_id="turn-1")
        await repo.apply(
            state.id,
            [SetText(nid=BULLET, value="a"), SetText(nid="sum_00001", value="b")],
            ctx=tiers(),
            turn_id="turn-1",
        )
        reverted = await repo.revert(state.id, checkpoint)
        assert reverted.doc.experience[0].bullets[0].text == "Rebuilt the ledger."
        assert reverted.doc.summary is not None
        assert reverted.doc.summary.text == "Backend engineer."


class TestHashStability:
    async def test_hash_is_stable_across_reload(self, repo: DocumentRepo) -> None:
        state = await repo.create(make_doc())
        loaded = await repo.get(state.id)
        assert loaded is not None
        assert loaded.content_hash == state.content_hash

    async def test_unicode_forms_hash_identically(self) -> None:
        """NFC normalisation: the same text typed two ways must not look like a
        concurrent edit."""
        from studio.doc.hashing import content_hash

        composed = StudioDoc(personal=PersonalInfo(name="Chloé"))
        decomposed = StudioDoc(personal=PersonalInfo(name="Chloé"))
        assert content_hash(composed) == content_hash(decomposed)

    async def test_partial_payload_hashes_like_its_defaulted_form(self) -> None:
        """A client omitting optional fields must not get a spurious 409."""
        from studio.doc.hashing import content_hash

        full = StudioDoc.model_validate(
            {"personal": {"name": "Alex", "title": "", "email": "", "phone": "", "location": ""}}
        )
        partial = StudioDoc.model_validate({"personal": {"name": "Alex"}})
        assert content_hash(full) == content_hash(partial)
