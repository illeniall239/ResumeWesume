"""Generate the TypeScript contract from the Pydantic models.

The schema is described in two languages. Hand-maintaining both guarantees
drift, and drift in a document editor means data loss — so the Python models are
the single source and the TypeScript is derived.

Run in CI and fail on a dirty tree: a schema change then breaks the build loudly
instead of surfacing as a mystery runtime error in the browser.

    uv run python scripts/gen_contracts.py
    git diff --exit-code ../web/src/contracts
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / "apps" / "web" / "src" / "contracts" / "doc.ts"

HEADER = """// GENERATED FILE — DO NOT EDIT.
// Source: apps/api/studio/doc/{schema,ops}.py
// Regenerate: cd apps/api && uv run python scripts/gen_contracts.py
"""

# Literal unions worth naming rather than inlining, so a bad value is a compile
# error at the call site instead of a silent string.
ENUMS = {
    "BulletStyle": ["bullet", "plain"],
    "SkillSource": ["original", "jd", "resume", "user"],
    "Tier": ["A", "B", "C"],
    "RejectCode": [
        "unknown_node",
        "kind_mismatch",
        "stale_expect",
        "not_grounded",
        "tier_denied",
        "invalid_args",
        "duplicate",
        "node_busy",
        "invariant_violation",
        "budget_exceeded",
    ],
    "NodeKind": [
        "exp",
        "edu",
        "prj",
        "blt",
        "skl",
        "sgp",
        "cst",
        "cit",
        "sum",
    ],
}


def _union(values: list[str]) -> str:
    return " | ".join(f"'{value}'" for value in values)


def build() -> str:
    from studio.doc.schema import (
        ElementStyle,
        FrameElement,
        ImageElement,
        PageNode,
        Rect,
        ShapeElement,
        StudioDoc,
        TextBlockNode,
    )

    lines: list[str] = [HEADER]

    for name, values in ENUMS.items():
        lines.append(f"export type {name} = {_union(values)};\n")

    lines.append("export type NodeId = string;\n")

    lines.append(
        """export interface TextNode {
  nid: NodeId;
  text: string;
  style: BulletStyle;
}

export interface PersonalInfo {
  name: string;
  title: string;
  email: string;
  phone: string;
  location: string;
  website: string | null;
  linkedin: string | null;
  github: string | null;
}

export interface ExperienceNode {
  nid: NodeId;
  title: string;
  company: string;
  location: string | null;
  years: string;
  bullets: TextNode[];
}

export interface EducationNode {
  nid: NodeId;
  institution: string;
  degree: string;
  years: string;
  detail: TextNode | null;
}

export interface ProjectNode {
  nid: NodeId;
  name: string;
  role: string;
  years: string;
  github: string | null;
  website: string | null;
  bullets: TextNode[];
}

export interface SkillItem {
  nid: NodeId;
  text: string;
  source: SkillSource;
}

export interface SkillGroup {
  nid: NodeId;
  key: string;
  label: string;
  items: SkillItem[];
}

export interface CustomItemNode {
  nid: NodeId;
  title: string;
  subtitle: string | null;
  location: string | null;
  years: string;
  bullets: TextNode[];
}

export interface CustomSectionNode {
  nid: NodeId;
  key: string;
  label: string;
  kind: 'text' | 'itemList' | 'stringList';
  text: TextNode | null;
  items: CustomItemNode[];
  strings: SkillItem[];
}

export interface SectionMeta {
  key: string;
  label: string;
  visible: boolean;
  order: number;
}

// --- Layout ---------------------------------------------------------------
// Where content sits, kept in its own subtree so the ATS export, the importer
// and the agent's tools can all keep addressing content without knowing that
// pages exist. A frame does not hold content, it points at it.

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface ElementStyle {
  align: 'left' | 'center' | 'right';
  font_scale: number;
  color: string | null;
  background: string | null;
  padding: number;
  radius: number;
  opacity: number;
}

export interface FrameElement {
  nid: NodeId;
  /** A section key or a content nid. Renders that subtree. */
  ref: string;
  rect: Rect;
  rotation: number;
  autogrow: 'none' | 'height';
  visible: boolean;
  locked: boolean;
  /** Placed by hand: the reflow pass leaves this frame where it is. */
  pinned: boolean;
  style: ElementStyle;
}

export interface ImageElement {
  nid: NodeId;
  asset: string;
  rect: Rect;
  rotation: number;
  fit: 'cover' | 'contain';
  crop: Rect | null;
  alt: string;
  visible: boolean;
  locked: boolean;
  style: ElementStyle;
}

export interface ShapeElement {
  nid: NodeId;
  shape: 'rect' | 'ellipse' | 'line';
  rect: Rect;
  rotation: number;
  fill: string | null;
  stroke: string | null;
  stroke_width: number;
  visible: boolean;
  locked: boolean;
}

export type AnyElement = FrameElement | ImageElement | ShapeElement;

export interface PageNode {
  nid: NodeId;
  size: 'A4' | 'Letter';
  orientation: 'portrait' | 'landscape';
  background: string | null;
  /** Z-order IS list order: the last element paints on top. */
  elements: AnyElement[];
}

export interface TextBlockNode {
  nid: NodeId;
  role: 'heading' | 'body' | 'caption' | 'contact' | 'none';
  lines: TextNode[];
}

export interface StudioDoc {
  schema_version: 1 | 2;
  personal: PersonalInfo;
  summary: TextNode | null;
  experience: ExperienceNode[];
  education: EducationNode[];
  projects: ProjectNode[];
  skills: SkillGroup[];
  custom: CustomSectionNode[];
  sections: SectionMeta[];
  blocks: TextBlockNode[];
  /** Empty means "render as one flowing column". */
  pages: PageNode[];
  reading_order: NodeId[] | null;
}
"""
    )

    lines.append(
        """// --- Operations -----------------------------------------------------------
// The ten primitives. Every agent tool call and every direct user edit
// compiles to one of these, so the client mirror only has to implement these.
// Eight touch content; two touch layout.

export interface SetTextOp {
  op: 'set_text';
  nid: NodeId;
  value: string;
  expect?: string | null;
  reason?: string;
}

export interface SetFieldOp {
  op: 'set_field';
  target: string;
  value: string | null;
  expect?: string | null;
  reason?: string;
}

export interface InsertNodeOp {
  op: 'insert_node';
  parent: string;
  index: number;
  node: Record<string, unknown>;
  reason?: string;
}

export interface RemoveNodeOp {
  op: 'remove_node';
  nid: NodeId;
  expect?: string | null;
  reason?: string;
}

export interface MoveNodeOp {
  op: 'move_node';
  nid: NodeId;
  parent: string;
  index: number;
  reason?: string;
}

export interface ReorderOp {
  op: 'reorder';
  parent: string;
  order: NodeId[];
  reason?: string;
}

export interface SetStyleOp {
  op: 'set_style';
  nid: NodeId;
  style: BulletStyle;
  reason?: string;
}

export interface SetSectionOp {
  op: 'set_section';
  key: string;
  visible?: boolean | null;
  order?: number | null;
  reason?: string;
}

export interface SetGeometryOp {
  op: 'set_geometry';
  nid: NodeId;
  x?: number | null;
  y?: number | null;
  w?: number | null;
  h?: number | null;
  rotation?: number | null;
  expect?: Record<string, number> | null;
  reason?: string;
}

export interface SetElementStyleOp {
  op: 'set_element_style';
  nid: NodeId;
  patch: Record<string, unknown>;
  reason?: string;
}

export type DocOp =
  | SetTextOp
  | SetFieldOp
  | InsertNodeOp
  | RemoveNodeOp
  | MoveNodeOp
  | ReorderOp
  | SetStyleOp
  | SetSectionOp
  | SetGeometryOp
  | SetElementStyleOp;

export interface AppliedOp {
  op: DocOp;
  touched: NodeId[];
}

export interface RejectedOp {
  op: DocOp;
  code: RejectCode;
  message: string;
  hint?: Record<string, unknown> | null;
}

export interface DocumentResponse {
  id: string;
  title: string;
  version: number;
  hash: string;
  doc: StudioDoc;
}

export interface ApplyResponse extends DocumentResponse {
  applied: AppliedOp[];
  rejected: RejectedOp[];
}
"""
    )

    rendered = "\n".join(lines)

    # Assert the Python models have not grown fields the hand-written blocks
    # above are missing. This is what makes the generator a real gate rather
    # than a convenience.
    #
    # Model by model, because only `StudioDoc` was checked before: a field
    # added to a *nested* model -- an element, a page, a rect -- reached
    # neither the contract nor this gate, which is exactly the drift the
    # generator exists to catch. `pinned` on `FrameElement` went straight
    # through that hole and had to be noticed by hand.
    for model in (
        StudioDoc,
        Rect,
        ElementStyle,
        FrameElement,
        ImageElement,
        ShapeElement,
        PageNode,
        TextBlockNode,
    ):
        missing = set(model.model_fields) - _fields_in_block(rendered, model.__name__)
        if missing:
            raise SystemExit(
                f"{model.__name__} has fields the TypeScript contract does not "
                f"cover: {sorted(missing)}. Update scripts/gen_contracts.py."
            )

    return rendered


def _fields_in_block(rendered: str, name: str) -> set[str]:
    """Property names declared in one ``export interface`` of the output."""
    match = re.search(rf"export interface {name}\b[^{{]*{{(.*?)\n}}", rendered, re.S)
    if not match:
        raise SystemExit(f"No TypeScript interface for {name}.")
    return set(re.findall(r"^\s*(\w+)\??:", match.group(1), re.M))


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
