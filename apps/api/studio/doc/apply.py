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
    SetElementStyle,
    SetField,
    SetGeometry,
    SetSection,
    SetStyle,
    SetText,
    Tier,
)
from studio.doc.schema import (
    AnyElement,
    CustomItemNode,
    FrameElement,
    CustomSectionNode,
    EducationNode,
    ExperienceNode,
    ProjectNode,
    PageNode,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextBlockNode,
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
    "blocks": "blocks",
    "pages": "pages",
}

# What each top-level list actually holds. Note "skills" holds *groups*, not
# individual skills — adding a skill means naming its group's nid as the parent.
_TOP_LEVEL_ELEMENT: dict[str, type[Any]] = {
    "experience": ExperienceNode,
    "education": EducationNode,
    "projects": ProjectNode,
    "skills": SkillGroup,
    "custom": CustomSectionNode,
    "blocks": TextBlockNode,
    "pages": PageNode,
}

# Kinds a page's element list accepts. Unlike every other container this one is
# genuinely heterogeneous, so the single-type check the others use does not
# apply and membership is tested against this set instead.
_ELEMENT_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.FRAME, NodeKind.IMAGE, NodeKind.SHAPE}
)

# Kinds that describe layout rather than content. Nothing in this set may be
# reached by ``set_field``, which would otherwise be a back door: it gates on
# the attribute *name* existing, so ``frm_x.rect`` would pass the per-op check
# and fail only at the whole-batch revalidation -- discarding the user's work
# alongside the bad op.
_LAYOUT_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.PAGE, NodeKind.FRAME, NodeKind.IMAGE, NodeKind.SHAPE}
)

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


# How much of a value a truncated ``expect`` must still carry before it counts
# as a claim about that value. The outline clips at 72 characters, so a genuine
# one carries about seventy.
_MIN_TRUNCATED_EXPECT = 20


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _flat(value: Any) -> str:
    """``_norm``, with internal whitespace collapsed as the outline collapses it."""
    return " ".join(_norm(value).split())


def _matches(actual: Any, expect: str | None) -> bool:
    """Optimistic-concurrency check.

    Verbatim in behaviour from ``_verify_original_matches``
    (``improver.py:212``): case- and whitespace-insensitive, and a missing
    ``expect`` means "no claim about the current value", not "match anything
    dangerous" — callers decide whether to require it.

    The ellipsis clause is not a loosening of that guarantee, it is a fix for a
    hole we dug ourselves. The agent reads the document as an outline that
    clips every value to a snippet, so a bullet longer than that is only ever
    *visible* to the model in truncated form. The model then quotes back what
    it was shown, exactly and honestly, and an equality check rejects it — on
    one real document that was all eighteen bullets, none of which could be
    rewritten at all.

    Accepting the truncation keeps the property ``expect`` exists for. It is a
    staleness check: it asks "is this still what you read?". A prefix answers
    that just as well, because text that changed underneath would not match the
    prefix either. The floor stops a two-character stub from standing in for a
    claim about the whole value.
    """
    if expect is None:
        return True

    actual_flat, expect_flat = _flat(actual), _flat(expect)
    if actual_flat == expect_flat:
        return True

    for mark in ("…", "..."):
        if expect_flat.endswith(mark):
            head = expect_flat[: -len(mark)].strip()
            return len(head) >= _MIN_TRUNCATED_EXPECT and actual_flat.startswith(head)
    return False


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
        # Deleting a page can take a lot of layout with it; deleting one box
        # cannot lose content, because the coverage gate refuses to strand it.
        if location.kind is NodeKind.PAGE:
            return "C"
        if location.kind in _ELEMENT_KINDS:
            return "B"
        if location.kind in _ENTRY_KINDS or location.kind is NodeKind.CUSTOM_SECTION:
            return "C"
        # A free text block is the only home its words have -- the reasoning
        # above ("deleting a box cannot lose content") is exactly what does not
        # hold here, because there is no flow for them to return to. Without
        # this it falls through to the default below and the model can delete
        # a hand-placed caption under a Tier A grant. Constrains only the model:
        # the author is granted every tier (routers/documents.py).
        if location.kind is NodeKind.BLOCK:
            return "C"
        return "B" if location.kind is NodeKind.SKILL else "A"

    if isinstance(op, SetGeometry):
        # Cannot change a word and cannot delete anything, and is exactly
        # invertible. Moving a box is not a claim about the person.
        return "A"

    if isinstance(op, SetElementStyle):
        # Read off the payload, not declared -- same rule as SetSection: making
        # something invisible is a content-loss shape however it is spelled.
        return "C" if op.patch.get("visible") is False else "A"

    if isinstance(op, InsertNode):
        # Blank paper and placed boxes destroy nothing. Without these two
        # branches "pages" falls into the top-level rule below and adding a
        # page would demand consent.
        if op.parent == "pages":
            return "A"
        if kind_of(op.parent) is NodeKind.PAGE:
            return "A"
        if op.parent == "blocks":
            return "A"
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


def _kind_fits(kind: NodeKind | None, element_type: type[Any] | None) -> bool:
    """Whether a node of ``kind`` may live in a container holding ``element_type``.

    ``element_type is None`` means the page element list, which is the one
    genuinely heterogeneous container in the document; membership there is a set
    test rather than a type equality.
    """
    if kind is None:
        return True  # an unparseable id is caught by validation, not here
    if element_type is None:
        return kind in _ELEMENT_KINDS
    expected = _KIND_OF_TYPE.get(element_type)
    return expected is None or kind is expected


def _expected_label(element_type: type[Any] | None) -> str:
    if element_type is None:
        return "frame, image or shape"
    expected = _KIND_OF_TYPE.get(element_type)
    return expected.value if expected else element_type.__name__


def _validate_element(element_type: type[Any] | None, raw: dict[str, Any]) -> Any:
    """Build a node for a container, homogeneous or not."""
    if element_type is not None:
        return element_type.model_validate(raw)
    from pydantic import TypeAdapter

    return TypeAdapter(AnyElement).validate_python(raw)


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
    if isinstance(node, TextBlockNode):
        return node.lines, TextNode
    if isinstance(node, PageNode):
        # Heterogeneous: frames, images and shapes share one list, so there is
        # no single element type to return. Callers test against _ELEMENT_KINDS.
        return node.elements, None
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

    # Gate 7: coverage. Every content node must be rendered by exactly one
    # frame, or free placement becomes a way to lose a job without being told.
    # Checked after the whole batch, so "delete this box and the job in it"
    # succeeds as one batch while "delete the box and strand the job" does not.
    orphan = _first_orphan(working)
    if orphan is not None:
        return (
            doc,
            [],
            [
                RejectedOp(
                    op={},
                    code=RejectCode.INVARIANT_VIOLATION,
                    message=f"Nothing on the page would render {orphan}",
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


# Content that a frame can be bound to. A frame naming one of these covers it
# and everything beneath it.
_COVERABLE_SECTIONS: tuple[str, ...] = (
    "personal",
    "summary",
    "experience",
    "education",
    "projects",
    "skills",
    "custom",
    "blocks",
)


def _section_roots(doc: StudioDoc) -> dict[str, list[Any]]:
    return {
        "summary": [doc.summary] if doc.summary is not None else [],
        "experience": list(doc.experience),
        "education": list(doc.education),
        "projects": list(doc.projects),
        "skills": list(doc.skills),
        "custom": list(doc.custom),
        "blocks": list(doc.blocks),
    }


def _first_orphan(doc: StudioDoc) -> str | None:
    """The first content node no frame would render, or None.

    Coverage is checked over *containers*, not leaves: a frame bound to
    ``"experience"`` covers every job and every bullet under it, so a bullet
    added later is covered automatically by its ancestor. That is the property
    that stops this becoming a second structure which can silently drift out of
    step with the content it describes.

    A document with no pages is a flowing document, which renders everything by
    definition -- so the gate is skipped entirely and v1 documents, fresh
    imports and the ATS path are all unaffected.
    """
    if not doc.pages:
        return None

    bound = {
        element.ref
        for page in doc.pages
        for element in page.elements
        if isinstance(element, FrameElement)
    }

    # A frame pointing at nothing is its own kind of orphan: it renders an
    # empty box and the content it claimed is covered by no one.
    index = NodeIndex(doc)
    for ref in bound:
        if ref not in _COVERABLE_SECTIONS and index.get(ref) is None:
            return ref

    for key, roots in _section_roots(doc).items():
        if key in bound:
            continue
        for node in roots:
            nid = getattr(node, "nid", None)
            if nid and nid not in bound:
                return nid
    return None


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
    if isinstance(op, SetGeometry):
        return _do_set_geometry(index, op)
    if isinstance(op, SetElementStyle):
        return _do_set_element_style(index, op)
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
    # Layout is not reachable from here. This gate checks only that an
    # attribute *name* exists, so without this a `set_field` on `frm_x.rect`
    # would pass and be caught only by the whole-batch revalidation -- which
    # rolls everything back, discarding the user's other work alongside it.
    if location.kind in _LAYOUT_KINDS:
        return _reject(
            op,
            RejectCode.INVALID_ARGS,
            f"{owner} is a layout element; use set_geometry or set_element_style",
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
    # Where it was, not just what it was. The node dump alone cannot be undone:
    # the inverse has nowhere to put it back, and an append would silently move
    # a bullet to the bottom of its job.
    op.before = {
        "parent": location.parent_nid or location.field,
        "index": location.position,
        "node": location.node.model_dump(mode="json"),
    }
    location.container.pop(location.position)
    return None


def _do_insert(doc: StudioDoc, index: NodeIndex, op: InsertNode) -> RejectedOp | None:
    container, element_type = _resolve_container(doc, index, op.parent)
    if container is None:
        return _reject(
            op, RejectCode.UNKNOWN_NODE, f"No list to insert into at {op.parent!r}"
        )
    try:
        node = _validate_element(element_type, op.node)
    except ValidationError as exc:
        name = element_type.__name__ if element_type is not None else "element"
        return _reject(
            op,
            RejectCode.INVALID_ARGS,
            f"Not a well-formed {name} for {op.parent!r}",
            {"errors": exc.error_count()},
        )
    # Gate 2 for inserts: a node's own id must agree with the list it is going
    # into, so a bullet can never land in the experience list.
    supplied_kind = kind_of(str(op.node.get("nid", "")))
    if not _kind_fits(supplied_kind, element_type):
        expected = _expected_label(element_type)
        return _reject(
            op,
            RejectCode.KIND_MISMATCH,
            f"{op.parent!r} holds {expected} nodes, not "
            f"{supplied_kind.value if supplied_kind else 'unknown'}",
        )
    if getattr(node, "nid", None) and node.nid in index:
        return _reject(op, RejectCode.DUPLICATE, f"{node.nid} already exists")

    position = len(container) if op.index < 0 else min(op.index, len(container))
    container.insert(position, node)
    # The id that actually landed, which is what the inverse removes. An insert
    # has no prior state to restore, so ``before`` records the effect rather
    # than a value -- without it an insert is simply not undoable.
    op.before = {"nid": getattr(node, "nid", None)}
    return None


def _do_move(doc: StudioDoc, index: NodeIndex, op: MoveNode) -> RejectedOp | None:
    location = index.get(op.nid)
    if location is None or location.container is None or location.position is None:
        return _reject(op, RejectCode.UNKNOWN_NODE, f"No movable node {op.nid}")
    target, element_type = _resolve_container(doc, index, op.parent)
    if target is None:
        return _reject(op, RejectCode.UNKNOWN_NODE, f"No list at {op.parent!r}")

    # Kind agreement, exactly as ``_do_insert`` checks it. Without this, a move
    # is a silent corruption rather than a rejection: the destination list is
    # typed, so gate 6's revalidation *coerces* the node into the new type
    # instead of failing. A bullet moved into "experience" became a phantom
    # empty job still carrying its blt_ id; an experience entry moved into
    # "education" became an EducationNode that kept its exp_ prefix and lost
    # every bullet. Neither produced a single rejection.
    if not _kind_fits(location.kind, element_type):
        return _reject(
            op,
            RejectCode.KIND_MISMATCH,
            f"{op.parent!r} holds {_expected_label(element_type)} nodes, "
            f"not {location.kind.value}",
        )

    # A node cannot contain itself. Left unchecked this pops the node out of the
    # tree and then inserts it into a list that is no longer reachable, which
    # deletes it.
    if op.parent == op.nid:
        return _reject(
            op, RejectCode.INVALID_ARGS, "A node cannot be moved into itself"
        )

    op.before = {"parent": location.parent_nid or location.field, "index": location.position}
    node = location.container.pop(location.position)
    # Note the index is interpreted against the list *after* removal, which for
    # an intra-list move shifts everything below the old position up by one.
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
    # De-duplicated as it goes, keeping the first mention. A repeated id would
    # otherwise put the same object in the list twice, which gate 7 then reads
    # as a duplicate nid and discards the whole batch -- so one careless id in
    # a reorder would throw away every other op the caller sent.
    seen: set[str] = set()
    reordered = []
    for nid in op.order:
        if nid in by_id and nid not in seen:
            seen.add(nid)
            reordered.append(by_id[nid])
    placed = {id(node) for node in reordered}
    reordered.extend(node for node in container if id(node) not in placed)

    container[:] = reordered
    return None


# Geometry arrives as floats from a browser, so ``expect`` is compared with a
# tolerance. Exact equality would reject honest values over the last bit of a
# float, which is a maddening thing to debug and buys nothing.
_GEOMETRY_TOLERANCE = 0.5

_GEOMETRY_FIELDS = ("x", "y", "w", "h")


def _do_set_geometry(index: NodeIndex, op: SetGeometry) -> RejectedOp | None:
    location = index.get(op.nid)
    if location is None:
        return _reject(
            op,
            RejectCode.UNKNOWN_NODE,
            f"No node {op.nid}",
            {"candidates": index.nearest(op.nid)},
        )
    if location.kind not in _ELEMENT_KINDS:
        return _reject(
            op,
            RejectCode.KIND_MISMATCH,
            f"{op.nid} is not a placed element",
        )

    rect = location.node.rect
    if op.expect is not None:
        for field in _GEOMETRY_FIELDS:
            wanted = op.expect.get(field)
            if wanted is None:
                continue
            if abs(getattr(rect, field) - float(wanted)) > _GEOMETRY_TOLERANCE:
                return _reject(
                    op,
                    RejectCode.STALE_EXPECT,
                    "The element has moved since you read it",
                    {"actual": rect.model_dump()},
                )

    op.before = {
        "x": rect.x,
        "y": rect.y,
        "w": rect.w,
        "h": rect.h,
        "rotation": getattr(location.node, "rotation", 0.0),
    }
    for field in _GEOMETRY_FIELDS:
        value = getattr(op, field)
        if value is not None:
            setattr(rect, field, float(value))
    if op.rotation is not None:
        location.node.rotation = float(op.rotation)
    return None


def _do_set_element_style(index: NodeIndex, op: SetElementStyle) -> RejectedOp | None:
    location = index.get(op.nid)
    if location is None:
        return _reject(
            op,
            RejectCode.UNKNOWN_NODE,
            f"No node {op.nid}",
            {"candidates": index.nearest(op.nid)},
        )
    if location.kind not in _ELEMENT_KINDS:
        return _reject(
            op, RejectCode.KIND_MISMATCH, f"{op.nid} is not a placed element"
        )
    if not op.patch:
        return _reject(op, RejectCode.INVALID_ARGS, "Nothing to change")

    node = location.node
    # Keys are validated against the element's own model and its style model, so
    # a typo is a rejection the caller can read rather than a silently ignored
    # field that leaves the UI showing a change that never happened.
    style_fields = set(type(node.style).model_fields) if hasattr(node, "style") else set()
    own_fields = set(type(node).model_fields) - {"nid", "rect", "rotation", "style"}

    unknown = [k for k in op.patch if k not in style_fields and k not in own_fields]
    if unknown:
        return _reject(
            op,
            RejectCode.INVALID_ARGS,
            f"No such element property: {', '.join(sorted(unknown))}",
        )

    before: dict[str, Any] = {}
    for key, value in op.patch.items():
        target = node.style if key in style_fields else node
        before[key] = getattr(target, key, None)
        setattr(target, key, value)
    op.before = before
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

    Total across all ten ops. It was not always: ``move_node``, ``insert_node``
    and ``set_section`` returned None even where the handler had already
    recorded everything needed, so a document could be edited in ways that could
    not be undone -- tolerable while the only writer was a chat turn with its
    own checkpoint, and not tolerable the moment a user can drag something.
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
        # No ``before is None`` guard here would clear the field on an
        # unapplied op, so an inverse is only offered once one was recorded.
        if op.before is None:
            return None
        return SetField(target=op.target, value=op.before, reason="undo")

    if isinstance(op, RemoveNode):
        if not isinstance(op.before, dict) or "node" not in op.before:
            return None
        return InsertNode(
            parent=str(op.before.get("parent") or ""),
            index=int(op.before.get("index", -1)),
            node=op.before["node"],
            reason="undo",
        )

    if isinstance(op, InsertNode):
        if not isinstance(op.before, dict) or not op.before.get("nid"):
            return None
        return RemoveNode(nid=str(op.before["nid"]), reason="undo")

    if isinstance(op, MoveNode):
        if not isinstance(op.before, dict):
            return None
        return MoveNode(
            nid=op.nid,
            parent=str(op.before.get("parent") or ""),
            index=int(op.before.get("index", -1)),
            reason="undo",
        )

    if isinstance(op, Reorder):
        if not isinstance(op.before, list):
            return None
        return Reorder(parent=op.parent, order=op.before, reason="undo")

    if isinstance(op, SetSection):
        if not isinstance(op.before, dict):
            return None
        return SetSection(
            key=op.key,
            visible=op.before.get("visible"),
            order=op.before.get("order"),
            reason="undo",
        )

    if isinstance(op, SetGeometry):
        if not isinstance(op.before, dict):
            return None
        return SetGeometry(nid=op.nid, reason="undo", **op.before)

    if isinstance(op, SetElementStyle):
        if not isinstance(op.before, dict):
            return None
        return SetElementStyle(nid=op.nid, patch=op.before, reason="undo")

    return None


__all__ = ["apply_ops", "invert", "tier_of", "OpContext", "IDENTITY_FIELDS"]
