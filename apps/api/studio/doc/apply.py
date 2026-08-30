"""The one place a document is mutated.

Ported in spirit from Resume-Matcher's ``apply_diffs``
(``apps/backend/app/services/improver.py:226``), keeping the properties that
made it trustworthy — pure, deepcopy-in, gate-per-change, applied/rejected
returned rather than raised — and replacing path-regex authorization with
per-op tier derivation.

The tier of an op is derived **from what it touches**, never from what the
caller claims. A tool cannot mislabel itself into a lower tier, because the
label is not an input.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from studio.doc.index import NodeIndex
from studio.doc.nodes import NodeKind, kind_of
from studio.doc.ops import (
    AppliedOp,
    DocOp,
    InsertNode,
    MoveNode,
    RejectCode,
    RejectedOp,
    Reorder,
    RemoveNode,
    SetField,
    SetSection,
    SetStyle,
    SetText,
    Tier,
)
from studio.doc.schema import (
    CustomItemNode,
    CustomSectionNode,
    EducationNode,
    ExperienceNode,
    ProjectNode,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

# Fields that assert who the user is or where they worked. Getting one of these
# wrong is not a style regression, it is a false statement on a document someone
# takes to an interview — so they are consent-gated regardless of caller.
IDENTITY_FIELDS: frozenset[str] = frozenset(
    {"company", "title", "institution", "degree", "name"}
)

# Editable without consent: descriptive, low-blast-radius entry metadata.
ENTRY_SOFT_FIELDS: frozenset[str] = frozenset({"years", "location", "role"})

# Nodes whose text a Tier A tool may rewrite.
TEXT_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.BULLET, NodeKind.SUMMARY, NodeKind.SKILL}
)

_TOP_LEVEL_LISTS: dict[str, str] = {
    "experience": "experience",
    "education": "education",
    "projects": "projects",
    "skills": "skills",
    "custom": "custom",
}

# What each top-level list actually holds. Note "skills" holds *groups*, not
# individual skills — adding a skill means naming its group's nid as the parent.
_TOP_LEVEL_ELEMENT: dict[str, type[Any]] = {
    "experience": ExperienceNode,
    "education": EducationNode,
    "projects": ProjectNode,
    "skills": SkillGroup,
    "custom": CustomSectionNode,
}

_ENTRY_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.EXPERIENCE, NodeKind.EDUCATION, NodeKind.PROJECT}
)

# Node type -> the id prefix its instances must carry.
_KIND_OF_TYPE: dict[type[Any], NodeKind] = {
    ExperienceNode: NodeKind.EXPERIENCE,
    EducationNode: NodeKind.EDUCATION,
    ProjectNode: NodeKind.PROJECT,
    SkillGroup: NodeKind.SKILL_GROUP,
    SkillItem: NodeKind.SKILL,
    CustomSectionNode: NodeKind.CUSTOM_SECTION,
    CustomItemNode: NodeKind.CUSTOM_ITEM,
    TextNode: NodeKind.BULLET,
}


@dataclass
class OpContext:
    """Authorization and provenance for one batch.

    ``granted_tiers`` is what the turn is allowed to do. ``consent_tokens`` holds
    one-shot approvals for Tier C ops the user explicitly confirmed.
    ``busy_nids`` are nodes the user currently has focus in — the agent is
    refused there, because user intent outranks agent intent.
    """

    granted_tiers: set[Tier] = field(default_factory=lambda: {"A"})
    consent_tokens: set[str] = field(default_factory=set)
    busy_nids: set[str] = field(default_factory=set)
    actor: str = "agent"

    def allows(self, tier: Tier, ref: str = "") -> bool:
        if tier in self.granted_tiers:
            return True
        return tier == "C" and ref in self.consent_tokens


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _matches(actual: Any, expect: str | None) -> bool:
    """Optimistic-concurrency check.

    Verbatim in behaviour from ``_verify_original_matches``
    (``improver.py:212``): case- and whitespace-insensitive, and a missing
    ``expect`` means "no claim about the current value", not "match anything
    dangerous" — callers decide whether to require it.
    """
    if expect is None:
        return True
    return _norm(actual) == _norm(expect)


def _split_target(target: str) -> tuple[str, str]:
    owner, _, attribute = target.partition(".")
    return owner, attribute


def tier_of(op: DocOp, index: NodeIndex) -> Tier:
    """Derive the risk tier of an op from what it touches."""
    if isinstance(op, SetField):
        owner, attribute = _split_target(op.target)
        if owner == "personal":
            return "C"
        if attribute in IDENTITY_FIELDS:
            return "C"
        if attribute in ENTRY_SOFT_FIELDS:
            return "B"
        return "C"

    if isinstance(op, RemoveNode):
        location = index.get(op.nid)
        if location is None:
            return "A"
        if location.kind in _ENTRY_KINDS or location.kind is NodeKind.CUSTOM_SECTION:
            return "C"
        return "B" if location.kind is NodeKind.SKILL else "A"

    if isinstance(op, InsertNode):
        if op.parent in _TOP_LEVEL_LISTS and op.parent != "skills":
            return "C"
        return "B" if op.parent == "skills" else "A"

    if isinstance(op, SetText):
        location = index.get(op.nid)
        if location is not None and location.kind is NodeKind.SKILL:
            return "B"
        return "A"

    if isinstance(op, SetSection):
        return "C" if op.visible is False else "A"

    return "A"


def _resolve_container(
    doc: StudioDoc, index: NodeIndex, parent: str
) -> tuple[list[Any] | None, type[Any] | None]:
    """Return the list named by ``parent`` and the node type it holds.

    Returning the *expected element type* alongside the list is what lets
    ``_do_insert`` enforce kind agreement. An earlier version inferred the type
    from the parent's name and happily inserted a ``SkillItem`` into the list of
    ``SkillGroup``s, producing a document that passed every per-op gate and then
    crashed the index on the next lookup. Deriving the type from the container
    makes that unrepresentable.
    """
    if parent in _TOP_LEVEL_LISTS:
        return getattr(doc, _TOP_LEVEL_LISTS[parent]), _TOP_LEVEL_ELEMENT[parent]

    location = index.get(parent)
    if location is None:
        return None, None

    node = location.node
    if isinstance(node, (ExperienceNode, ProjectNode, CustomItemNode)):
        return node.bullets, TextNode
    if isinstance(node, SkillGroup):
        return node.items, SkillItem
    if isinstance(node, CustomSectionNode):
        return node.items, CustomItemNode
    return None, None


def apply_ops(
    doc: StudioDoc,
    ops: list[DocOp],
    ctx: OpContext | None = None,
) -> tuple[StudioDoc, list[AppliedOp], list[RejectedOp]]:
    """Apply ``ops`` to a copy of ``doc``.

    Never mutates ``doc``. Ops are applied in order against the working copy, so
    an op may legitimately depend on an earlier one in the same batch. A
    rejected op does not abort the batch: partial progress with an explicit
    rejection list is far more useful to an agent than an all-or-nothing failure
    it cannot diagnose.
    """
    ctx = ctx or OpContext()
    working = doc.model_copy(deep=True)
    applied: list[AppliedOp] = []
    rejected: list[RejectedOp] = []

    for op in ops:
        index = NodeIndex(working)
        reject = _apply_one(working, index, op, ctx)
        if reject is None:
            applied.append(
                AppliedOp(op=op.model_dump(mode="json"), touched=_touched(op))
            )
        else:
            rejected.append(reject)

    # Gate 6: the batch as a whole must still be a valid document. A single op
    # can be individually legal and still leave the document inconsistent.
    try:
        working = StudioDoc.model_validate(working.model_dump())
    except ValidationError as exc:  # pragma: no cover - defensive
        return (
            doc,
            [],
            [
                RejectedOp(
                    op={},
                    code=RejectCode.INVARIANT_VIOLATION,
                    message=f"Batch left the document invalid: {exc.error_count()} errors",
                )
            ],
        )

    duplicate = _first_duplicate(working)
    if duplicate is not None:
        return (
            doc,
            [],
            [
                RejectedOp(
                    op={},
                    code=RejectCode.INVARIANT_VIOLATION,
                    message=f"Duplicate node id {duplicate}",
                )
            ],
        )

    return working, applied, rejected


def _touched(op: DocOp) -> list[str]:
    for attribute in ("nid", "parent", "target", "key"):
        value = getattr(op, attribute, None)
        if isinstance(value, str) and value:
            return [value]
    return []


def _first_duplicate(doc: StudioDoc) -> str | None:
    seen: set[str] = set()
    stack: list[Any] = [doc]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
            continue
        if isinstance(current, list):
            stack.extend(current)
            continue
        nid = getattr(current, "nid", None)
        if isinstance(nid, str):
            if nid in seen:
                return nid
            seen.add(nid)
        # Read model_fields off the class: instance access is deprecated in
        # Pydantic 2.11 and removed in v3.
        fields = getattr(type(current), "model_fields", None)
        if fields:
            stack.extend(getattr(current, name) for name in fields)
    return None


def _reject(op: DocOp, code: str, message: str, hint: Any = None) -> RejectedOp:
    return RejectedOp(
        op=op.model_dump(mode="json"), code=code, message=message, hint=hint
    )


def _apply_one(
    doc: StudioDoc, index: NodeIndex, op: DocOp, ctx: OpContext
) -> RejectedOp | None:
    """Apply one op in place. Returns a rejection, or None on success."""
    # Gate 3: tier authorization, from what the op touches.
    tier = tier_of(op, index)
    ref = _touched(op)[0] if _touched(op) else ""
    if not ctx.allows(tier, ref):
        return _reject(
            op,
            RejectCode.TIER_DENIED,
            f"This change needs tier {tier} authorization",
            {"tier": tier},
        )

    # The user's caret wins. Refusing here is what stops the agent from
    # overwriting a line someone is mid-sentence in.
    if ctx.actor == "agent":
        for nid in _touched(op):
            if nid in ctx.busy_nids:
                return _reject(
                    op,
                    RejectCode.NODE_BUSY,
                    "The user is editing that line; ask before changing it",
                    {"nid": nid},
                )

    if isinstance(op, SetText):
        return _do_set_text(index, op)
    if isinstance(op, SetStyle):
        return _do_set_style(index, op)
    if isinstance(op, SetField):
        return _do_set_field(doc, index, op)
    if isinstance(op, RemoveNode):
        return _do_remove(index, op)
    if isinstance(op, InsertNode):
        return _do_insert(doc, index, op)
    if isinstance(op, MoveNode):
        return _do_move(doc, index, op)
    if isinstance(op, Reorder):
        return _do_reorder(doc, index, op)
    if isinstance(op, SetSection):
        return _do_set_section(doc, op)
    return _reject(op, RejectCode.INVALID_ARGS, "Unknown operation")


def _do_set_text(index: NodeIndex, op: SetText) -> RejectedOp | None:
    location = index.get(op.nid)
    if location is None:  # Gate 1
        return _reject(
            op,
            RejectCode.UNKNOWN_NODE,
            f"No node {op.nid}",
            {"candidates": index.nearest(op.nid)},
        )
    if location.kind not in TEXT_KINDS:  # Gate 2
        return _reject(
            op,
            RejectCode.KIND_MISMATCH,
            f"{op.nid} is a {location.kind.value}, which has no editable text",
        )
    if not _matches(location.node.text, op.expect):  # Gate 4
        return _reject(
            op,
            RejectCode.STALE_EXPECT,
            "The current text does not match what you expected",
            {"actual": location.node.text},
        )
    op.before = location.node.text
    location.node.text = op.value
    return None


def _do_set_style(index: NodeIndex, op: SetStyle) -> RejectedOp | None:
    location = index.get(op.nid)
    if location is None:
        return _reject(op, RejectCode.UNKNOWN_NODE, f"No node {op.nid}")
    if location.kind is not NodeKind.BULLET:
        return _reject(
            op, RejectCode.KIND_MISMATCH, f"{op.nid} is not a bullet"
        )
    op.before = location.node.style
    location.node.style = op.style
    return None


def _do_set_field(doc: StudioDoc, index: NodeIndex, op: SetField) -> RejectedOp | None:
    owner, attribute = _split_target(op.target)

    if owner == "personal":
        if attribute not in type(doc.personal).model_fields:
            return _reject(
                op, RejectCode.INVALID_ARGS, f"No personal field {attribute!r}"
            )
        current = getattr(doc.personal, attribute)
        if not _matches(current, op.expect):
            return _reject(
                op,
                RejectCode.STALE_EXPECT,
                "Current value does not match",
                {"actual": current},
            )
        op.before = current
        setattr(doc.personal, attribute, op.value or "")
        return None

    location = index.get(owner)
    if location is None:
        return _reject(
            op,
            RejectCode.UNKNOWN_NODE,
            f"No node {owner}",
            {"candidates": index.nearest(owner)},
        )
    if attribute not in type(location.node).model_fields:
        return _reject(
            op,
            RejectCode.INVALID_ARGS,
            f"{location.kind.value} has no field {attribute!r}",
        )
    current = getattr(location.node, attribute)
    if not _matches(current, op.expect):
        return _reject(
            op,
            RejectCode.STALE_EXPECT,
            "Current value does not match",
            {"actual": current},
        )
    op.before = current
    setattr(location.node, attribute, op.value)
    return None


def _do_remove(index: NodeIndex, op: RemoveNode) -> RejectedOp | None:
    location = index.get(op.nid)
    if location is None:
        return _reject(
            op,
            RejectCode.UNKNOWN_NODE,
            f"No node {op.nid}",
            {"candidates": index.nearest(op.nid)},
        )
    if location.container is None or location.position is None:
        return _reject(
            op, RejectCode.KIND_MISMATCH, f"{op.nid} is not removable"
        )
    text = getattr(location.node, "text", None)
    if op.expect is not None and not _matches(text, op.expect):
        return _reject(
            op,
            RejectCode.STALE_EXPECT,
            "The node you are deleting is not what you expected",
            {"actual": text},
        )
    op.before = location.node.model_dump(mode="json")
    location.container.pop(location.position)
    return None


def _do_insert(doc: StudioDoc, index: NodeIndex, op: InsertNode) -> RejectedOp | None:
    container, element_type = _resolve_container(doc, index, op.parent)
    if container is None or element_type is None:
        return _reject(
            op, RejectCode.UNKNOWN_NODE, f"No list to insert into at {op.parent!r}"
        )
    try:
        node = element_type.model_validate(op.node)
    except ValidationError as exc:
        return _reject(
            op,
            RejectCode.INVALID_ARGS,
            f"Not a well-formed {element_type.__name__} for {op.parent!r}",
            {"errors": exc.error_count()},
        )
    # Gate 2 for inserts: a node's own id must agree with the list it is going
    # into, so a bullet can never land in the experience list.
    supplied_kind = kind_of(str(op.node.get("nid", "")))
    expected_kind = _KIND_OF_TYPE.get(element_type)
    if supplied_kind is not None and expected_kind is not None and supplied_kind is not expected_kind:
        return _reject(
            op,
            RejectCode.KIND_MISMATCH,
            f"{op.parent!r} holds {expected_kind.value} nodes, not {supplied_kind.value}",
        )
    if getattr(node, "nid", None) and node.nid in index:
        return _reject(op, RejectCode.DUPLICATE, f"{node.nid} already exists")

    position = len(container) if op.index < 0 else min(op.index, len(container))
    container.insert(position, node)
    op.before = None
    return None


def _do_move(doc: StudioDoc, index: NodeIndex, op: MoveNode) -> RejectedOp | None:
    location = index.get(op.nid)
    if location is None or location.container is None or location.position is None:
        return _reject(op, RejectCode.UNKNOWN_NODE, f"No movable node {op.nid}")
    target, _ = _resolve_container(doc, index, op.parent)
    if target is None:
        return _reject(op, RejectCode.UNKNOWN_NODE, f"No list at {op.parent!r}")

    op.before = {"parent": location.parent_nid or location.field, "index": location.position}
    node = location.container.pop(location.position)
    position = len(target) if op.index < 0 else min(op.index, len(target))
    target.insert(position, node)
    return None


def _do_reorder(doc: StudioDoc, index: NodeIndex, op: Reorder) -> RejectedOp | None:
    container, _ = _resolve_container(doc, index, op.parent)
    if container is None:
        return _reject(op, RejectCode.UNKNOWN_NODE, f"No list at {op.parent!r}")

    by_id = {getattr(node, "nid", None): node for node in container}
    op.before = [getattr(node, "nid", None) for node in container]

    # Salvage rather than reject: place the ids we recognise in the order asked
    # for, drop ones we do not, and append anything omitted so nothing is ever
    # silently lost. Carried over from the reorder handling in apply_diffs,
    # where rejecting the whole list over one bad id proved far too brittle.
    reordered = [by_id[nid] for nid in op.order if nid in by_id]
    placed = {id(node) for node in reordered}
    reordered.extend(node for node in container if id(node) not in placed)

    container[:] = reordered
    return None


def _do_set_section(doc: StudioDoc, op: SetSection) -> RejectedOp | None:
    for section in doc.sections:
        if section.key == op.key:
            op.before = {"visible": section.visible, "order": section.order}
            if op.visible is not None:
                section.visible = op.visible
            if op.order is not None:
                section.order = op.order
            return None
    return _reject(op, RejectCode.UNKNOWN_NODE, f"No section {op.key!r}")


def invert(op: DocOp) -> DocOp | None:
    """The op that undoes ``op``, given its recorded ``before``.

    Returns None when the op was never applied (no ``before`` recorded), which
    is the honest answer rather than a no-op that silently corrupts an undo
    stack.
    """
    if isinstance(op, SetText):
        if op.before is None:
            return None
        return SetText(nid=op.nid, value=str(op.before), reason="undo")
    if isinstance(op, SetStyle):
        if op.before is None:
            return None
        return SetStyle(nid=op.nid, style=op.before, reason="undo")
    if isinstance(op, SetField):
        return SetField(target=op.target, value=op.before, reason="undo")
    if isinstance(op, RemoveNode):
        if not isinstance(op.before, dict):
            return None
        return InsertNode(parent="", index=-1, node=op.before, reason="undo")
    if isinstance(op, Reorder):
        if not isinstance(op.before, list):
            return None
        return Reorder(parent=op.parent, order=op.before, reason="undo")
    return None


__all__ = ["apply_ops", "invert", "tier_of", "OpContext", "IDENTITY_FIELDS"]
