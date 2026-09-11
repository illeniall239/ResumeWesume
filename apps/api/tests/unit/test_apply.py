"""The mutation engine's contract.

These are the tests that let us point the product at a weak local model without
crossing our fingers. The adversarial block at the bottom is the important half:
it asserts that no sequence of badly-formed model output can corrupt a document.
"""

from __future__ import annotations

import pytest

from studio.doc.apply import OpContext, apply_ops, invert, tier_of
from studio.doc.nodes import NodeKind, is_valid, kind_of, mint
from studio.doc.ops import (
    InsertNode,
    MoveNode,
    RejectCode,
    RemoveNode,
    Reorder,
    SetField,
    SetSection,
    SetStyle,
    SetText,
)
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    ExperienceNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

BULLET_A = "blt_aaaaa"
BULLET_B = "blt_bbbbb"
EXP = "exp_11111"
SKILL_PY = "skl_pppp1"
GROUP = "sgp_ggggg"


def make_doc() -> StudioDoc:
    return StudioDoc(
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
                    TextNode(nid=BULLET_A, text="Rebuilt the payments ledger."),
                    TextNode(nid=BULLET_B, text="Led the async migration."),
                ],
            )
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


def all_tiers() -> OpContext:
    return OpContext(granted_tiers={"A", "B", "C"})


# --------------------------------------------------------------------------
# Node identity
# --------------------------------------------------------------------------


class TestNodeIds:
    def test_prefix_encodes_kind(self) -> None:
        nid = mint(NodeKind.BULLET)
        assert nid.startswith("blt_")
        assert kind_of(nid) is NodeKind.BULLET

    def test_ids_are_drawn_from_a_space_too_large_to_collide_in_a_resume(
        self,
    ) -> None:
        # This asserted 500 mints were all distinct, which is a coin flip, not
        # a property: five characters of a 31-letter alphabet is 28.6M ids, so
        # 500 draws collide about once in every 230 runs -- and it duly failed
        # a full suite run once, for nothing. The real guarantee is two-part:
        # the space is far larger than any document, and a collision that does
        # happen is caught by the duplicate gate rather than written to disk
        # (see `test_duplicate_nid_rejected`).
        minted = [mint(NodeKind.SKILL) for _ in range(500)]

        assert len(set(minted)) >= 499
        # Random, not a counter: a sequence would repeat across two processes
        # minting into the same document.
        assert len({nid[4] for nid in minted}) > 20

    @pytest.mark.parametrize(
        "bad", ["", "blt", "blt_", "xyz_aaaaa", "blt_toolongsuffix", "blt_AAAAA", "blt_iiiii"]
    )
    def test_malformed_ids_rejected(self, bad: str) -> None:
        # Returns None rather than raising: unparseable ids arrive from models
        # routinely and are a rejection, not an exception.
        assert kind_of(bad) is None
        assert not is_valid(bad)


# --------------------------------------------------------------------------
# Purity — the property everything else leans on
# --------------------------------------------------------------------------


class TestPurity:
    def test_never_mutates_input(self) -> None:
        doc = make_doc()
        snapshot = doc.model_dump()
        apply_ops(doc, [SetText(nid=BULLET_A, value="changed")], all_tiers())
        assert doc.model_dump() == snapshot

    def test_rejected_batch_leaves_document_untouched(self) -> None:
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc, [SetText(nid="blt_zzzzz", value="ghost")], all_tiers()
        )
        assert applied == []
        assert len(rejected) == 1
        assert result.model_dump() == doc.model_dump()


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------


class TestGates:
    def test_unknown_node_returns_candidates(self) -> None:
        doc = make_doc()
        _, _, rejected = apply_ops(
            doc, [SetText(nid="blt_zzzzz", value="x")], all_tiers()
        )
        assert rejected[0].code == RejectCode.UNKNOWN_NODE
        # The hint is what lets a weak model self-correct instead of giving up.
        assert rejected[0].hint is not None
        assert rejected[0].hint["candidates"]

    def test_kind_mismatch_rejected(self) -> None:
        doc = make_doc()
        _, _, rejected = apply_ops(
            doc, [SetStyle(nid=EXP, style="plain")], all_tiers()
        )
        assert rejected[0].code == RejectCode.KIND_MISMATCH

    def test_stale_expect_returns_actual_text(self) -> None:
        doc = make_doc()
        _, _, rejected = apply_ops(
            doc,
            [SetText(nid=BULLET_A, value="new", expect="something else entirely")],
            all_tiers(),
        )
        assert rejected[0].code == RejectCode.STALE_EXPECT
        # Models fix this ~90% of the time when handed the real value.
        assert rejected[0].hint["actual"] == "Rebuilt the payments ledger."

    def test_the_truncated_text_the_agent_was_shown_is_accepted(self) -> None:
        """The agent reads an outline that clips every value to a snippet.

        A bullet longer than that snippet is only ever *visible* to the model
        truncated, so quoting it back honestly used to be rejected forever --
        on one real resume that was all eighteen bullets, none of which could
        be rewritten at all.
        """
        from studio.agent.context import _clip

        doc = make_doc()
        long_text = (
            "Consolidated and standardized data from four disparate sources in "
            "Excel, resolving inconsistencies to improve reporting accuracy."
        )
        doc.experience[0].bullets[0].text = long_text
        shown = _clip(long_text)
        assert shown.endswith("…"), "the outline must actually be clipping here"

        result, applied, rejected = apply_ops(
            doc,
            [SetText(nid=BULLET_A, value="Standardised four data sources.", expect=shown)],
            all_tiers(),
        )
        assert rejected == []
        assert len(applied) == 1
        assert result.experience[0].bullets[0].text == "Standardised four data sources."

    def test_a_truncated_expect_still_catches_a_stale_edit(self) -> None:
        """The property the gate exists for, kept: text that changed underneath
        does not match the prefix either."""
        doc = make_doc()
        doc.experience[0].bullets[0].text = (
            "Consolidated and standardized data from four disparate sources."
        )
        _, _, rejected = apply_ops(
            doc,
            [
                SetText(
                    nid=BULLET_A,
                    value="new",
                    expect="Consolidated and standardized data from NINE disparate…",
                )
            ],
            all_tiers(),
        )
        assert rejected[0].code == RejectCode.STALE_EXPECT

    def test_a_two_character_stub_is_not_a_claim_about_the_value(self) -> None:
        """Otherwise "C…" would authorise rewriting anything starting with C."""
        doc = make_doc()
        _, _, rejected = apply_ops(
            doc, [SetText(nid=BULLET_A, value="new", expect="Re…")], all_tiers()
        )
        assert rejected[0].code == RejectCode.STALE_EXPECT

    def test_an_ascii_ellipsis_is_accepted_too(self) -> None:
        """Models emit "..." as readily as "…"."""
        doc = make_doc()
        doc.experience[0].bullets[0].text = "Rebuilt the payments ledger end to end."
        _, applied, rejected = apply_ops(
            doc,
            [SetText(nid=BULLET_A, value="new", expect="Rebuilt the payments ledger...")],
            all_tiers(),
        )
        assert rejected == []
        assert len(applied) == 1

    def test_a_truncation_of_a_different_node_is_still_refused(self) -> None:
        doc = make_doc()
        _, _, rejected = apply_ops(
            doc,
            [SetText(nid=BULLET_A, value="new", expect="Owned the design system used by…")],
            all_tiers(),
        )
        assert rejected[0].code == RejectCode.STALE_EXPECT

    def test_matching_expect_applies(self) -> None:
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc,
            [
                SetText(
                    nid=BULLET_A,
                    value="Cut settlement latency 96%.",
                    # Case- and whitespace-insensitive, as in the ported engine.
                    expect="  rebuilt the PAYMENTS ledger.  ",
                )
            ],
            all_tiers(),
        )
        assert not rejected and len(applied) == 1
        assert result.experience[0].bullets[0].text == "Cut settlement latency 96%."


# --------------------------------------------------------------------------
# Tier derivation — the authorization model
# --------------------------------------------------------------------------


class TestTiers:
    def test_bullet_edit_is_tier_a(self) -> None:
        doc = make_doc()
        from studio.doc.index import NodeIndex

        assert tier_of(SetText(nid=BULLET_A, value="x"), NodeIndex(doc)) == "A"

    def test_personal_info_is_tier_c(self) -> None:
        doc = make_doc()
        from studio.doc.index import NodeIndex

        op = SetField(target="personal.email", value="new@example.com")
        assert tier_of(op, NodeIndex(doc)) == "C"

    def test_identity_field_is_tier_c(self) -> None:
        doc = make_doc()
        from studio.doc.index import NodeIndex

        assert tier_of(SetField(target=f"{EXP}.company", value="Other"), NodeIndex(doc)) == "C"

    def test_soft_entry_field_is_tier_b(self) -> None:
        doc = make_doc()
        from studio.doc.index import NodeIndex

        assert tier_of(SetField(target=f"{EXP}.years", value="2020"), NodeIndex(doc)) == "B"

    def test_removing_an_entry_is_tier_c(self) -> None:
        doc = make_doc()
        from studio.doc.index import NodeIndex

        assert tier_of(RemoveNode(nid=EXP), NodeIndex(doc)) == "C"

    def test_tier_a_context_cannot_touch_personal_info(self) -> None:
        """The headline safety property: a plain content-editing turn cannot
        rewrite who the user is, no matter what the model emits."""
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc,
            [SetField(target="personal.email", value="attacker@evil.com")],
            OpContext(granted_tiers={"A"}),
        )
        assert applied == []
        assert rejected[0].code == RejectCode.TIER_DENIED
        assert result.personal.email == "alex@example.com"

    def test_consent_token_authorizes_one_target(self) -> None:
        doc = make_doc()
        ctx = OpContext(granted_tiers={"A"}, consent_tokens={"personal.email"})
        result, applied, _ = apply_ops(
            doc, [SetField(target="personal.email", value="new@example.com")], ctx
        )
        assert len(applied) == 1
        assert result.personal.email == "new@example.com"


# --------------------------------------------------------------------------
# The user's caret outranks the agent
# --------------------------------------------------------------------------


class TestBusyNodes:
    def test_agent_refused_on_focused_node(self) -> None:
        doc = make_doc()
        ctx = OpContext(granted_tiers={"A"}, busy_nids={BULLET_A}, actor="agent")
        result, applied, rejected = apply_ops(
            doc, [SetText(nid=BULLET_A, value="agent text")], ctx
        )
        assert applied == []
        assert rejected[0].code == RejectCode.NODE_BUSY
        assert result.experience[0].bullets[0].text == "Rebuilt the payments ledger."

    def test_user_is_not_blocked_by_their_own_focus(self) -> None:
        doc = make_doc()
        ctx = OpContext(granted_tiers={"A"}, busy_nids={BULLET_A}, actor="user")
        _, applied, _ = apply_ops(doc, [SetText(nid=BULLET_A, value="mine")], ctx)
        assert len(applied) == 1


# --------------------------------------------------------------------------
# Structural edits — the capabilities the old engine could not express
# --------------------------------------------------------------------------


class TestStructural:
    def test_remove_bullet(self) -> None:
        doc = make_doc()
        result, applied, _ = apply_ops(doc, [RemoveNode(nid=BULLET_A)], all_tiers())
        assert len(applied) == 1
        assert [b.nid for b in result.experience[0].bullets] == [BULLET_B]

    def test_add_bullet(self) -> None:
        doc = make_doc()
        new = {"nid": mint(NodeKind.BULLET), "text": "Shipped the ingest tier."}
        result, applied, rejected = apply_ops(
            doc, [InsertNode(parent=EXP, index=-1, node=new)], all_tiers()
        )
        assert not rejected and len(applied) == 1
        assert result.experience[0].bullets[-1].text == "Shipped the ingest tier."

    def test_add_experience_entry(self) -> None:
        doc = make_doc()
        entry = {
            "nid": mint(NodeKind.EXPERIENCE),
            "title": "Staff Engineer",
            "company": "Acme",
            "years": "2019 - 2021",
        }
        result, applied, rejected = apply_ops(
            doc, [InsertNode(parent="experience", index=0, node=entry)], all_tiers()
        )
        assert not rejected and len(applied) == 1
        assert result.experience[0].company == "Acme"
        assert len(result.experience) == 2

    def test_remove_skill(self) -> None:
        doc = make_doc()
        result, applied, _ = apply_ops(doc, [RemoveNode(nid=SKILL_PY)], all_tiers())
        assert len(applied) == 1
        assert [s.text for s in result.skills[0].items] == ["Go"]

    def test_skill_item_cannot_be_inserted_into_the_group_list(self) -> None:
        """Regression, found by the property suite.

        ``skills`` holds SkillGroups, not individual skills. An earlier version
        inferred the node type from the parent's *name* and cheerfully inserted a
        SkillItem there: every per-op gate passed, the document was structurally
        wrong, and the next index build crashed on ``group.items``. The element
        type now comes from the container, so this is rejected up front.
        """
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc,
            [InsertNode(parent="skills", node={"nid": mint(NodeKind.SKILL), "text": "Rust"})],
            all_tiers(),
        )
        assert applied == []
        assert rejected[0].code == RejectCode.KIND_MISMATCH
        assert result.model_dump() == doc.model_dump()

    def test_adding_a_skill_names_its_group(self) -> None:
        """The correct way to add a skill: the group's nid is the parent."""
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc,
            [InsertNode(parent=GROUP, node={"nid": mint(NodeKind.SKILL), "text": "Rust"})],
            all_tiers(),
        )
        assert not rejected and len(applied) == 1
        assert [s.text for s in result.skills[0].items] == ["Python", "Go", "Rust"]

    def test_bullet_cannot_be_inserted_into_the_experience_list(self) -> None:
        doc = make_doc()
        _, applied, rejected = apply_ops(
            doc,
            [InsertNode(parent="experience", node={"nid": mint(NodeKind.BULLET), "text": "x"})],
            all_tiers(),
        )
        assert applied == []
        assert rejected[0].code == RejectCode.KIND_MISMATCH

    def test_duplicate_nid_rejected(self) -> None:
        doc = make_doc()
        _, _, rejected = apply_ops(
            doc,
            [InsertNode(parent=EXP, node={"nid": BULLET_A, "text": "clone"})],
            all_tiers(),
        )
        assert rejected[0].code == RejectCode.DUPLICATE


class TestMoveKinds:
    """Moving a node must respect the destination's type.

    ``_do_move`` used to discard the element type that ``_resolve_container``
    returns, while ``_do_insert`` checked it. The destination list is typed, so
    the batch revalidation *coerced* the node rather than failing -- every case
    below applied with zero rejections and silently corrupted the document.
    ``MoveNode`` had no test anywhere and was absent from the property suite,
    which is how it survived.
    """

    def test_a_bullet_cannot_be_moved_into_the_experience_list(self) -> None:
        # Produced a phantom empty job still carrying its blt_ id.
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc, [MoveNode(nid=BULLET_A, parent="experience", index=0)], all_tiers()
        )
        assert applied == []
        assert rejected[0].code == RejectCode.KIND_MISMATCH
        assert result.model_dump() == doc.model_dump()

    def test_an_experience_entry_cannot_be_moved_into_education(self) -> None:
        # Produced an EducationNode keeping the exp_ prefix, bullets dropped.
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc, [MoveNode(nid=EXP, parent="education", index=0)], all_tiers()
        )
        assert applied == []
        assert rejected[0].code == RejectCode.KIND_MISMATCH
        assert result.education == []
        assert result.model_dump() == doc.model_dump()

    def test_a_skill_cannot_be_moved_into_the_skills_group_list(self) -> None:
        """"skills" holds groups, not items -- the same trap `_do_insert`
        already guards."""
        doc = make_doc()
        _, applied, rejected = apply_ops(
            doc, [MoveNode(nid=SKILL_PY, parent="skills", index=0)], all_tiers()
        )
        assert applied == []
        assert rejected[0].code == RejectCode.KIND_MISMATCH

    def test_a_node_cannot_be_moved_into_itself(self) -> None:
        """Pops the node out of the tree, then inserts it into a list nothing
        can reach any more -- a deletion wearing a move's clothes."""
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc, [MoveNode(nid=EXP, parent=EXP, index=0)], all_tiers()
        )
        assert applied == []
        assert rejected[0].code in {RejectCode.KIND_MISMATCH, RejectCode.INVALID_ARGS}
        assert result.model_dump() == doc.model_dump()

    def test_a_bullet_moves_between_jobs(self) -> None:
        """The cross-parent move that is legitimate, and must keep working."""
        doc = make_doc()
        doc.experience.append(
            ExperienceNode(nid="exp_22222", title="Engineer", company="Contoso")
        )
        result, applied, rejected = apply_ops(
            doc, [MoveNode(nid=BULLET_A, parent="exp_22222", index=0)], all_tiers()
        )
        assert rejected == []
        assert len(applied) == 1
        assert [b.nid for b in result.experience[0].bullets] == [BULLET_B]
        assert [b.nid for b in result.experience[1].bullets] == [BULLET_A]

    def test_an_entry_moves_within_its_own_list(self) -> None:
        doc = make_doc()
        doc.experience.append(
            ExperienceNode(nid="exp_22222", title="Engineer", company="Contoso")
        )
        result, _, rejected = apply_ops(
            doc, [MoveNode(nid=EXP, parent="experience", index=1)], all_tiers()
        )
        assert rejected == []
        assert [e.nid for e in result.experience] == ["exp_22222", EXP]


class TestReorderSalvage:
    def test_pure_permutation(self) -> None:
        doc = make_doc()
        result, _, rejected = apply_ops(
            doc, [Reorder(parent=EXP, order=[BULLET_B, BULLET_A])], all_tiers()
        )
        assert not rejected
        assert [b.nid for b in result.experience[0].bullets] == [BULLET_B, BULLET_A]

    def test_unknown_ids_dropped_and_omitted_appended(self) -> None:
        """Salvage the intent rather than rejecting the batch — and never lose a
        node the model simply forgot to mention."""
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc, [Reorder(parent=EXP, order=["blt_ghost", BULLET_B])], all_tiers()
        )
        assert not rejected and len(applied) == 1
        assert [b.nid for b in result.experience[0].bullets] == [BULLET_B, BULLET_A]


# --------------------------------------------------------------------------
# Undo
# --------------------------------------------------------------------------


    def test_a_repeated_id_does_not_discard_the_batch(self) -> None:
        """A duplicated id used to insert the same object twice, which gate 7
        then read as a duplicate nid and rolled the whole batch back -- so one
        careless id threw away every other op the caller sent."""
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc,
            [
                Reorder(parent=EXP, order=[BULLET_B, BULLET_B, BULLET_A]),
                SetText(nid=BULLET_A, value="survived"),
            ],
            all_tiers(),
        )
        assert rejected == []
        assert len(applied) == 2
        assert [b.nid for b in result.experience[0].bullets] == [BULLET_B, BULLET_A]
        assert result.experience[0].bullets[1].text == "survived"


class TestInvert:
    def test_set_text_round_trips(self) -> None:
        doc = make_doc()
        op = SetText(nid=BULLET_A, value="changed")
        once, applied, _ = apply_ops(doc, [op], all_tiers())
        assert applied
        undo = invert(op)
        assert undo is not None
        twice, _, _ = apply_ops(once, [undo], all_tiers())
        assert twice.model_dump() == doc.model_dump()

    def test_remove_round_trips_to_the_original_position(self) -> None:
        """A restored node goes back where it was, not onto the end.

        This test used to patch ``undo.parent`` by hand and assert the bullet
        came back last, because the inverse shipped ``parent=""`` -- which the
        engine cannot resolve -- and ``index=-1``. Both were the recorded
        ``before`` being too thin, and undoing a delete quietly reordered the
        document.
        """
        doc = make_doc()
        op = RemoveNode(nid=BULLET_A)
        once, _, _ = apply_ops(doc, [op], all_tiers())

        undo = invert(op)
        assert undo is not None
        twice, applied, rejected = apply_ops(once, [undo], all_tiers())

        assert rejected == []
        assert len(applied) == 1
        assert twice.model_dump() == doc.model_dump()

    def test_insert_round_trips(self) -> None:
        doc = make_doc()
        op = InsertNode(parent=EXP, index=0, node={"nid": "blt_ccccc", "text": "New."})
        once, _, _ = apply_ops(doc, [op], all_tiers())
        assert len(once.experience[0].bullets) == 3

        undo = invert(op)
        assert undo is not None
        twice, _, rejected = apply_ops(once, [undo], all_tiers())
        assert rejected == []
        assert twice.model_dump() == doc.model_dump()

    def test_move_round_trips(self) -> None:
        """Undoing a move needs the node's *old* parent and index, which
        ``_do_move`` records and ``invert`` previously ignored."""
        doc = make_doc()
        op = MoveNode(nid=BULLET_A, parent=EXP, index=1)
        once, _, _ = apply_ops(doc, [op], all_tiers())
        assert [b.nid for b in once.experience[0].bullets] == [BULLET_B, BULLET_A]

        undo = invert(op)
        assert undo is not None
        twice, _, rejected = apply_ops(once, [undo], all_tiers())
        assert rejected == []
        assert twice.model_dump() == doc.model_dump()

    def test_set_section_round_trips(self) -> None:
        doc = make_doc()
        op = SetSection(key="experience", visible=False, order=9)
        once, _, _ = apply_ops(doc, [op], all_tiers())

        undo = invert(op)
        assert undo is not None
        twice, _, rejected = apply_ops(once, [undo], all_tiers())
        assert rejected == []
        assert twice.model_dump() == doc.model_dump()

    def test_every_op_kind_is_invertible_once_applied(self) -> None:
        """Undo is only as good as its least-covered op.

        Three of the eight returned None even where the handler had already
        recorded what was needed, so a document could be edited in ways that
        could not be undone.
        """
        doc = make_doc()
        ops: list = [
            SetText(nid=BULLET_A, value="changed"),
            SetStyle(nid=BULLET_A, style="plain"),
            SetField(target=f"{EXP}.years", value="2020 - 2024"),
            RemoveNode(nid=BULLET_B),
            InsertNode(parent=EXP, index=0, node={"nid": "blt_ddddd", "text": "X"}),
            MoveNode(nid=BULLET_A, parent=EXP, index=0),
            Reorder(parent=EXP, order=[BULLET_A]),
            SetSection(key="skills", order=1),
        ]
        for op in ops:
            fresh = make_doc()
            once, applied, rejected = apply_ops(fresh, [op], all_tiers())
            assert rejected == [], f"{op.op} was rejected: {rejected}"
            assert invert(op) is not None, f"{op.op} has no inverse after applying"

    def test_unapplied_op_has_no_inverse(self) -> None:
        # Honest None rather than a no-op that silently corrupts an undo stack.
        assert invert(SetText(nid=BULLET_A, value="x")) is None
        assert invert(MoveNode(nid=BULLET_A, parent=EXP, index=0)) is None
        assert invert(InsertNode(parent=EXP, node={"nid": "blt_zzzzz"})) is None
        assert invert(SetSection(key="skills", order=2)) is None


# --------------------------------------------------------------------------
# Adversarial: a weak model must not be able to corrupt a document
# --------------------------------------------------------------------------


class TestAdversarial:
    """Each case is a real failure shape seen from small local models."""

    @pytest.mark.parametrize(
        "op",
        [
            SetText(nid="", value="x"),
            SetText(nid="workExperience[0].description[1]", value="x"),  # old path DSL
            SetText(nid="0", value="x"),  # bare index
            SetText(nid="blt_" + "z" * 40, value="x"),
            SetStyle(nid="sum_00001", style="bullet"),  # summary is not a bullet
            SetField(target="personal.nonexistent", value="x"),
            SetField(target="nosuchnode.company", value="x"),
            SetField(target="", value="x"),
            RemoveNode(nid="blt_ghost"),
            InsertNode(parent="nowhere", node={"text": "x"}),
            InsertNode(parent="experience", node={"not": "a valid entry"}),
            Reorder(parent="blt_ghost", order=[]),
            SetSection(key="nosuchsection", visible=False),
        ],
    )
    def test_malformed_op_is_rejected_not_fatal(self, op) -> None:
        doc = make_doc()
        result, applied, rejected = apply_ops(doc, [op], all_tiers())
        # No exception escaped, exactly one rejection, document untouched.
        assert applied == []
        assert len(rejected) == 1
        assert rejected[0].code in vars(RejectCode).values()
        assert result.model_dump() == doc.model_dump()

    def test_flood_of_bad_ops_leaves_document_valid(self) -> None:
        doc = make_doc()
        flood = [SetText(nid=f"blt_{i:05d}", value="junk") for i in range(200)]
        result, applied, rejected = apply_ops(doc, flood, all_tiers())
        assert applied == []
        assert len(rejected) == 200
        assert StudioDoc.model_validate(result.model_dump())

    def test_identity_never_drifts_without_authorization(self) -> None:
        """The invariant that matters most: with only content-editing rights,
        no op sequence can change who the user is or where they worked."""
        doc = make_doc()
        hostile = [
            SetField(target="personal.name", value="Someone Else"),
            SetField(target="personal.email", value="attacker@evil.com"),
            SetField(target=f"{EXP}.company", value="Fabricated Corp"),
            SetField(target=f"{EXP}.title", value="Chief Executive"),
            RemoveNode(nid=EXP),
        ]
        result, applied, rejected = apply_ops(
            doc, hostile, OpContext(granted_tiers={"A"})
        )
        assert applied == []
        assert len(rejected) == len(hostile)
        assert result.personal.name == "Alex Morgan"
        assert result.personal.email == "alex@example.com"
        assert result.experience[0].company == "Northwind"
        assert result.experience[0].title == "Senior Engineer"
        assert len(result.experience) == 1

    def test_partial_batch_keeps_good_ops(self) -> None:
        """A rejected op must not discard the valid work beside it — an agent
        needs partial progress plus a diagnosis, not all-or-nothing."""
        doc = make_doc()
        result, applied, rejected = apply_ops(
            doc,
            [
                SetText(nid=BULLET_A, value="Good edit."),
                SetText(nid="blt_ghost", value="bad"),
                SetText(nid=BULLET_B, value="Also good."),
            ],
            all_tiers(),
        )
        assert len(applied) == 2
        assert len(rejected) == 1
        assert result.experience[0].bullets[0].text == "Good edit."
        assert result.experience[0].bullets[1].text == "Also good."


class TestCoercion:
    def test_non_string_text_does_not_crash(self) -> None:
        # A model returning a number for a text field used to blow up several
        # layers away on .strip(); coerce at the boundary instead.
        node = TextNode(nid=BULLET_A, text=12345)  # type: ignore[arg-type]
        assert node.text == "12345"

    def test_unknown_style_falls_back_to_bullet(self) -> None:
        node = TextNode(nid=BULLET_A, text="x", style="fancy")  # type: ignore[arg-type]
        assert node.style == "bullet"

    def test_no_parallel_array_to_desync(self) -> None:
        """The structural fix for the old descriptionStyles bug: style lives on
        the bullet, so removing one cannot shift another's marker."""
        doc = make_doc()
        doc.experience[0].bullets[0].style = "plain"
        result, _, _ = apply_ops(doc, [RemoveNode(nid=BULLET_A)], all_tiers())
        assert len(result.experience[0].bullets) == 1
        assert result.experience[0].bullets[0].nid == BULLET_B
        assert result.experience[0].bullets[0].style == "bullet"


class TestAgentEllipsis:
    """The other half of the truncation bug.

    The agent reads an outline that marks every clipped value with "…". Having
    read forty of them it writes one into the replacement text, and a resume
    bullet trailing off mid-thought is a defect the user must spot and fix by
    hand. Observed live: "Architected RAG-based CMS with Qdrant and Kimi..."
    """

    def rewrite(self, value: str) -> str:
        from studio.agent.tools import RewriteText, RewriteTextArgs

        ops = RewriteText().compile(
            RewriteTextArgs(nid=BULLET_A, value=value), make_doc()
        )
        return ops[0].value

    def test_a_trailing_unicode_ellipsis_is_dropped(self) -> None:
        assert self.rewrite("Architected a RAG-based CMS with Qdrant…") == (
            "Architected a RAG-based CMS with Qdrant"
        )

    def test_a_trailing_ascii_ellipsis_is_dropped(self) -> None:
        assert self.rewrite("Architected a RAG-based CMS with Qdrant...") == (
            "Architected a RAG-based CMS with Qdrant"
        )

    def test_dangling_punctuation_goes_with_it(self) -> None:
        assert self.rewrite("Cut latency, improved throughput, …") == (
            "Cut latency, improved throughput"
        )

    def test_ordinary_text_is_untouched(self) -> None:
        for value in (
            "Rebuilt the payments ledger.",
            "Cut p99 latency from 1.8s to 340ms",
            "Owned CI/CD for 40 services",
        ):
            assert self.rewrite(value) == value

    def test_an_ellipsis_mid_sentence_is_left_alone(self) -> None:
        value = "Handled the long tail… and everything else."
        assert self.rewrite(value) == value

    def test_a_value_that_is_only_an_ellipsis_is_left_for_the_engine(self) -> None:
        """Stripping it would produce an empty rewrite, which is a different
        and worse failure than a visibly silly one."""
        assert self.rewrite("…") == "…"


class TestSkillsStayReadable:
    """The skills group has a length past which more entries subtract."""

    def test_a_duplicate_is_refused_by_name(self) -> None:
        """Rejecting silently is not enough -- the model retries.

        Told only "no", it proposed the same skill three more times and the turn
        stalled with nothing added, so the message says what to do instead.
        """
        from studio.agent.tools import REGISTRY, ToolError

        doc = StudioDoc(
            skills=[
                SkillGroup(
                    nid="sgp_ggggg",
                    key="technical",
                    items=[SkillItem(nid="skl_ppppp", text="Python")],
                )
            ]
        )
        spec = REGISTRY.get("add_skill")
        args = spec.Args(skill="python", group="technical", evidence="user_request")

        with pytest.raises(ToolError, match="already in"):
            spec.compile(args, doc)

    def test_a_full_group_is_refused(self) -> None:
        from studio.agent.tools import MAX_SKILLS_PER_GROUP, REGISTRY, ToolError

        doc = StudioDoc(
            skills=[
                SkillGroup(
                    nid="sgp_ggggg",
                    key="technical",
                    items=[
                        SkillItem(nid=f"skl_{index:05d}", text=f"Skill {index}")
                        for index in range(MAX_SKILLS_PER_GROUP)
                    ],
                )
            ]
        )
        spec = REGISTRY.get("add_skill")
        args = spec.Args(skill="Kubernetes", group="technical", evidence="user_request")

        with pytest.raises(ToolError, match="as many as a reader takes in"):
            spec.compile(args, doc)


class TestSkillGroupMatching:
    """The group key is whatever the résumé was imported with.

    `add_skill` defaults its group to "technical". A real résumé came in with
    the group named "technicalSkills", so every add_skill on that document
    failed with "No skill group 'technical'" while the group sat right there in
    the error message.
    """

    def _doc(self) -> StudioDoc:
        return StudioDoc(
            skills=[
                SkillGroup(nid="sgp_aaaaa", key="technicalSkills", items=[]),
                SkillGroup(nid="sgp_bbbbb", key="certificationsTraining", items=[]),
            ]
        )

    def test_the_default_reaches_an_imported_group(self) -> None:
        from studio.agent.tools import _match_group

        assert _match_group("technical", self._doc()).key == "technicalSkills"

    def test_case_and_punctuation_do_not_matter(self) -> None:
        from studio.agent.tools import _match_group

        assert _match_group("TECHNICAL", self._doc()).key == "technicalSkills"
        assert (
            _match_group("certifications & training", self._doc()).key
            == "certificationsTraining"
        )

    def test_a_group_that_is_not_there_is_still_reported(self) -> None:
        """Loose is not limitless -- a wrong guess still gets told."""
        from studio.agent.tools import _match_group

        assert _match_group("languages", self._doc()) is None
