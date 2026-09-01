"""The eight primitive mutations.

Every tool the agent can call, and every direct edit the user makes, compiles
down to this closed set. One vocabulary means one place that mutates a document
(``apply.py``), one place that gates it, and one place to test.

Each op records ``before`` when applied, which makes ``invert`` total. That
single property buys undo, the ops log, and 409 rebase without any of them
needing their own machinery.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from studio.doc.nodes import NodeId

Tier = Literal["A", "B", "C"]


class _Op(BaseModel):
    """Common shape. ``before`` is filled in by ``apply_ops``, not by callers."""

    before: Any = None
    # Why this op exists, surfaced in the UI and the ops log. Free text from the
    # model; never parsed, never trusted for authorization.
    reason: str = ""


class SetText(_Op):
    """Replace the text of a text-bearing node (bullet, summary, skill)."""

    op: Literal["set_text"] = "set_text"
    nid: NodeId
    value: str
    # Optimistic concurrency: if present, the node's current text must match
    # (stripped, case-folded) or the op is rejected as stale.
    expect: str | None = None


class SetField(_Op):
    """Set a scalar field on a node or on ``personal``.

    ``target`` is ``"personal.email"`` or ``"exp_7f3a2.company"``. This is the
    one op that names something other than a bare nid, because these fields are
    not nodes in their own right.
    """

    op: Literal["set_field"] = "set_field"
    target: str
    value: str | None
    expect: str | None = None


class InsertNode(_Op):
    """Insert a new node into a list.

    ``parent`` is a nid for a nested list (bullets of an entry) or a section key
    for a top-level one ("experience"). ``index`` of -1 appends.
    """

    op: Literal["insert_node"] = "insert_node"
    parent: str
    index: int = -1
    node: dict[str, Any]


class RemoveNode(_Op):
    op: Literal["remove_node"] = "remove_node"
    nid: NodeId
    expect: str | None = None


class MoveNode(_Op):
    op: Literal["move_node"] = "move_node"
    nid: NodeId
    parent: str
    index: int


class Reorder(_Op):
    """Permute a list.

    ``order`` is the full set of child nids in their new order. Unknown ids are
    dropped and omitted ones appended — a deliberate salvage of the model's
    intent rather than an all-or-nothing rejection, carried over from the
    reorder handling in Resume-Matcher's ``apply_diffs``.
    """

    op: Literal["reorder"] = "reorder"
    parent: str
    order: list[NodeId]


class SetStyle(_Op):
    op: Literal["set_style"] = "set_style"
    nid: NodeId
    style: Literal["bullet", "plain"]


class SetSection(_Op):
    op: Literal["set_section"] = "set_section"
    key: str
    visible: bool | None = None
    order: int | None = None


class SetGeometry(_Op):
    """Move or resize a placed element.

    Every field is optional so a gesture compiles to exactly what it changed --
    a drag is ``{x, y}``, a resize is ``{w, h}`` -- which is what lets a stream
    of them coalesce into one op per gesture instead of one per pointer event.

    Deliberately its own op rather than widening ``SetField``. ``tier_of``
    derives a tier from the *attribute name*, so ``x`` and ``fill`` would both
    fall through its default and every drag would demand consent.
    """

    op: Literal["set_geometry"] = "set_geometry"
    nid: NodeId
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    rotation: float | None = None
    # Compared with a small tolerance, not for equality: geometry arrives as
    # floats from a browser and exact comparison would reject honest values.
    expect: dict[str, float] | None = None


class SetElementStyle(_Op):
    """Change how a placed element looks, never what it says."""

    op: Literal["set_element_style"] = "set_element_style"
    nid: NodeId
    patch: dict[str, Any] = Field(default_factory=dict)


DocOp = Annotated[
    Union[
        SetText,
        SetField,
        InsertNode,
        RemoveNode,
        MoveNode,
        Reorder,
        SetStyle,
        SetSection,
        SetGeometry,
        SetElementStyle,
    ],
    Field(discriminator="op"),
]


class RejectCode:
    """Closed set. Both the UI and the model-facing repair message key off these,
    so a new failure mode must be named here rather than described in prose."""

    UNKNOWN_NODE = "unknown_node"
    KIND_MISMATCH = "kind_mismatch"
    STALE_EXPECT = "stale_expect"
    NOT_GROUNDED = "not_grounded"
    TIER_DENIED = "tier_denied"
    INVALID_ARGS = "invalid_args"
    DUPLICATE = "duplicate"
    NODE_BUSY = "node_busy"
    INVARIANT_VIOLATION = "invariant_violation"
    BUDGET_EXCEEDED = "budget_exceeded"


class AppliedOp(BaseModel):
    op: dict[str, Any]
    touched: list[NodeId] = Field(default_factory=list)


class RejectedOp(BaseModel):
    op: dict[str, Any]
    code: str
    message: str
    # Machine-readable recovery aid: the actual current text for STALE_EXPECT,
    # candidate nids for UNKNOWN_NODE. This is what a weak model needs to fix
    # its own call on retry rather than repeating the same mistake.
    hint: dict[str, Any] | None = None
