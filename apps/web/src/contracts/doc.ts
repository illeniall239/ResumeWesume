// GENERATED FILE — DO NOT EDIT.
// Source: apps/api/studio/doc/{schema,ops}.py
// Regenerate: cd apps/api && uv run python scripts/gen_contracts.py

export type BulletStyle = 'bullet' | 'plain';

export type SkillSource = 'original' | 'jd' | 'resume' | 'user';

export type Tier = 'A' | 'B' | 'C';

export type RejectCode = 'unknown_node' | 'kind_mismatch' | 'stale_expect' | 'not_grounded' | 'tier_denied' | 'invalid_args' | 'duplicate' | 'node_busy' | 'invariant_violation' | 'budget_exceeded';

export type NodeKind = 'exp' | 'edu' | 'prj' | 'blt' | 'skl' | 'sgp' | 'cst' | 'cit' | 'sum';

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

export interface StudioDoc {
  schema_version: 1;
  personal: PersonalInfo;
  summary: TextNode | null;
  experience: ExperienceNode[];
  education: EducationNode[];
  projects: ProjectNode[];
  skills: SkillGroup[];
  custom: CustomSectionNode[];
  sections: SectionMeta[];
}

// --- Operations -----------------------------------------------------------
// The eight primitives. Every agent tool call and every direct user edit
// compiles to one of these, so the client mirror only has to implement these.

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

export type DocOp =
  | SetTextOp
  | SetFieldOp
  | InsertNodeOp
  | RemoveNodeOp
  | MoveNodeOp
  | ReorderOp
  | SetStyleOp
  | SetSectionOp;

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
