// The document schema, in the browser's language.
//
// Mirrors apps/api/studio/doc/{schema,ops}.py, which is the source of truth.
// Edit this file when a model gains a field: the API suite's
// test_contract_covers_the_models fails by name if one is missing here.
//
// It used to carry "GENERATED FILE — DO NOT EDIT" and did not: the generator
// held a hand-typed copy of this file and overwrote it, so an edit made here
// disappeared on the next run with CI reporting only a dirty tree.

export type BulletStyle = 'bullet' | 'plain';

/**
 * How a run of short items is set: one comma-separated line, or a bulleted
 * list. `auto` is the heuristic below in `mustStack` — the shape the page picks
 * when the document does not say. Optional, because a document written before
 * the field existed does not carry it and means `auto` by saying nothing.
 */
export type ListDisplay = 'auto' | 'inline' | 'list';

export type SkillSource = 'original' | 'jd' | 'resume' | 'user';

export type Tier = 'A' | 'B' | 'C';

export type Template = 'plain' | 'ruled' | 'compact' | 'book' | 'centered' | 'banner' | 'bold' | 'quiet' | 'portrait' | 'profile' | 'badge';

export type Layout = 'stack' | 'sidebar_left' | 'sidebar_right';

export type RejectCode = 'unknown_node' | 'kind_mismatch' | 'stale_expect' | 'not_grounded' | 'tier_denied' | 'invalid_args' | 'duplicate' | 'node_busy' | 'invariant_violation' | 'budget_exceeded';

export type NodeKind = 'exp' | 'edu' | 'prj' | 'blt' | 'skl' | 'sgp' | 'cst' | 'cit' | 'sum' | 'pag' | 'frm' | 'img' | 'shp' | 'txb';

export type NodeId = string;

export interface TextNode {
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
  /** Id of an uploaded image, or null. */
  photo: string | null;
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
  display?: ListDisplay;
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
  /** Applies to `strings`. */
  display?: ListDisplay;
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
  /** Presentation only, and never touched by an op. */
  template: Template;
  layout: Layout;
  /** True while nothing in the document is yet the user's own. */
  scaffold: boolean;
  /** Nodes the assistant invented while scaffolding, pending confirmation. */
  unverified: NodeId[];
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

// --- Operations -----------------------------------------------------------
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
  /** When it last changed, ISO-8601 and UTC. Null on older servers. */
  updated_at?: string | null;
  /**
   * The posting this résumé is aimed at, verbatim, or null.
   *
   * Held on the document rather than sent with a message: tailoring is not one
   * instruction, and carried on the turn it survived exactly one exchange.
   */
  job_description?: string | null;
  /** The canvas this board sits on. */
  canvas_id?: string | null;
}

export interface ApplyResponse extends DocumentResponse {
  applied: AppliedOp[];
  rejected: RejectedOp[];
}

/**
 * A résumé and the versions of it aimed at particular jobs.
 *
 * The thing the register lists. Its boards are ordinary documents, which is
 * what keeps ops, undo, export and the agent working on a board exactly as
 * they worked on a document — a board *is* a document.
 */
export interface CanvasResponse {
  id: string;
  title: string;
  /** Every board on it, in full, so a card cannot go stale against what it opens. */
  boards: DocumentResponse[];
  updated_at?: string | null;
}
