"""Invariants that must hold for *any* op sequence.

The example-based tests in ``tests/unit`` cover failure shapes we have seen.
These cover the ones we have not: Hypothesis generates op sequences we would
never think to write, which is exactly the situation a weak model puts us in.

Every test here asserts a property that, if violated, means a user's resume can
be corrupted. None of them assert anything about what a *good* edit looks like.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.doc.apply import OpContext, apply_ops
from studio.doc.nodes import NodeKind, kind_of, mint
from studio.doc.ops import (
    InsertNode,
    MoveNode,
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

EXP = "exp_11111"
BULLET_A = "blt_aaaaa"
BULLET_B = "blt_bbbbb"
GROUP = "sgp_ggggg"
SKILL = "skl_pppp1"

# A pool mixing real ids with plausible-but-wrong ones: the old path DSL, bare
# indices, malformed prefixes, and ids for nodes that never existed.
NID_POOL = [
    BULLET_A,
    BULLET_B,
    EXP,
    GROUP,
    SKILL,
    "sum_00001",
    "blt_ghost",
    "exp_99999",
    "workExperience[0].description[1]",
    "0",
    "",
    "blt_",
    "xyz_aaaaa",
]

TARGET_POOL = [
    "personal.email",
    "personal.name",
    "personal.nonexistent",
    f"{EXP}.company",
    f"{EXP}.years",
    f"{EXP}.nonexistent",
    "ghost.company",
    "",
]

text = st.text(max_size=60)


def base_doc() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(
            name="Alex Morgan", email="alex@example.com", phone="+1-555-0142"
        ),
        summary=TextNode(nid="sum_00001", text="Backend engineer."),
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
            )
        ],
        skills=[
            SkillGroup(
                nid=GROUP,
                items=[SkillItem(nid=SKILL, text="Python")],
            )
        ],
        sections=list(DEFAULT_SECTIONS),
    )


nids = st.sampled_from(NID_POOL)

ops = st.one_of(
    st.builds(SetText, nid=nids, value=text, expect=st.none() | text),
    st.builds(SetStyle, nid=nids, style=st.sampled_from(["bullet", "plain"])),
    st.builds(SetField, target=st.sampled_from(TARGET_POOL), value=text),
    st.builds(RemoveNode, nid=nids),
    st.builds(
        InsertNode,
        parent=st.sampled_from(["experience", "skills", EXP, "nowhere", ""]),
        index=st.integers(min_value=-3, max_value=5),
        node=st.fixed_dictionaries({"nid": st.just(mint(NodeKind.BULLET)), "text": text}),
    ),
    st.builds(Reorder, parent=st.sampled_from([EXP, GROUP, "ghost"]), order=st.lists(nids, max_size=5)),
    st.builds(SetSection, key=st.sampled_from(["summary", "experience", "ghost"])),
    # MoveNode was missing from this strategy, and had no example-based test
    # either. That gap is exactly why `_do_move` could coerce a bullet into an
    # ExperienceNode for months without anything noticing: the one suite that
    # would have generated the case could not see the op.
    st.builds(
        MoveNode,
        nid=nids,
        parent=st.sampled_from(
            ["experience", "education", "skills", EXP, GROUP, "nowhere", ""]
        ),
        index=st.integers(min_value=-3, max_value=5),
    ),
)

op_lists = st.lists(ops, max_size=12)

SETTINGS = settings(
    max_examples=300,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)


@given(op_lists)
@SETTINGS
def test_result_always_validates(op_list) -> None:
    """No op sequence can produce a document that fails its own schema."""
    result, _, _ = apply_ops(base_doc(), op_list, OpContext(granted_tiers={"A", "B", "C"}))
    StudioDoc.model_validate(result.model_dump())


@given(op_lists)
@SETTINGS
def test_input_is_never_mutated(op_list) -> None:
    """Purity. Everything else — undo, rollback, optimistic UI — depends on it."""
    doc = base_doc()
    snapshot = doc.model_dump()
    apply_ops(doc, op_list, OpContext(granted_tiers={"A", "B", "C"}))
    assert doc.model_dump() == snapshot


@given(op_lists)
@SETTINGS
def test_node_ids_stay_unique(op_list) -> None:
    """A duplicate id makes every later reference ambiguous."""
    result, _, _ = apply_ops(base_doc(), op_list, OpContext(granted_tiers={"A", "B", "C"}))
    seen: set[str] = set()
    stack = [result]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(current)
            continue
        nid = getattr(current, "nid", None)
        if isinstance(nid, str):
            assert nid not in seen, f"duplicate {nid}"
            seen.add(nid)
        fields = getattr(type(current), "model_fields", None)
        if fields:
            stack.extend(getattr(current, name) for name in fields)


@given(op_lists)
@SETTINGS
def test_every_id_remains_well_formed(op_list) -> None:
    """An op can never write a malformed id into the document."""
    result, _, _ = apply_ops(base_doc(), op_list, OpContext(granted_tiers={"A", "B", "C"}))
    stack = [result]
    while stack:
        current = stack.pop()
        if isinstance(current, list):
            stack.extend(current)
            continue
        nid = getattr(current, "nid", None)
        if isinstance(nid, str):
            assert kind_of(nid) is not None, f"malformed id {nid!r}"
        fields = getattr(type(current), "model_fields", None)
        if fields:
            stack.extend(getattr(current, name) for name in fields)


@given(op_lists)
@SETTINGS
def test_every_node_lives_in_a_list_of_its_own_kind(op_list) -> None:
    """A node's id prefix always agrees with the list it sits in.

    This is the invariant `_do_move` violated. Checking that an id *parses* is
    not enough -- ``blt_aaaaa`` is a perfectly well-formed id, and the bug put
    it in the experience list, where the batch revalidation then coerced the
    bullet into an ExperienceNode and dropped everything it could not map.

    Stated as a property because that is the only form that survives: the
    engine has five typed top-level lists and five nested ones, and the next op
    to touch them should fail here rather than in someone's resume.
    """
    result, _, _ = apply_ops(base_doc(), op_list, OpContext(granted_tiers={"A", "B", "C"}))

    expected: list[tuple[list, NodeKind, str]] = [
        (result.experience, NodeKind.EXPERIENCE, "experience"),
        (result.education, NodeKind.EDUCATION, "education"),
        (result.projects, NodeKind.PROJECT, "projects"),
        (result.skills, NodeKind.SKILL_GROUP, "skills"),
        (result.custom, NodeKind.CUSTOM_SECTION, "custom"),
    ]
    for entry in result.experience + result.projects:
        expected.append((entry.bullets, NodeKind.BULLET, f"{entry.nid}.bullets"))
    for group in result.skills:
        expected.append((group.items, NodeKind.SKILL, f"{group.nid}.items"))
    for section in result.custom:
        expected.append((section.items, NodeKind.CUSTOM_ITEM, f"{section.nid}.items"))

    for container, kind, where in expected:
        for node in container:
            nid = getattr(node, "nid", None)
            assert kind_of(nid) is kind, f"{nid!r} is not a {kind.value} but sits in {where}"


@given(op_lists)
@SETTINGS
def test_identity_cannot_drift_under_tier_a(op_list) -> None:
    """**The safety property.**

    With only content-editing rights — the tier an ordinary "make this punchier"
    turn runs at — no sequence of operations, however malformed or hostile, can
    change who the user is, where they worked, or delete a job.
    """
    doc = base_doc()
    result, _, _ = apply_ops(doc, op_list, OpContext(granted_tiers={"A"}))

    assert result.personal.model_dump() == doc.personal.model_dump()
    assert len(result.experience) == len(doc.experience)
    for before, after in zip(doc.experience, result.experience):
        assert after.company == before.company
        assert after.title == before.title
        assert after.years == before.years


@given(op_lists)
@SETTINGS
def test_applied_and_rejected_account_for_every_op(op_list) -> None:
    """Nothing is silently dropped: every op is either applied or explained."""
    _, applied, rejected = apply_ops(
        base_doc(), op_list, OpContext(granted_tiers={"A", "B", "C"})
    )
    # A whole-batch invariant failure returns a single synthetic rejection, which
    # is the one case where the counts legitimately differ.
    if rejected and rejected[0].op == {}:
        return
    assert len(applied) + len(rejected) == len(op_list)
