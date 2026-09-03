/**
 * Reading a node back out of the document.
 *
 * Needed in two places that both have to show a person what a change actually
 * does: the revision cloud, which reveals the reading it superseded, and the
 * stamp block, which states a proposed change as before and after rather than
 * as the tool arguments that produce it.
 *
 * Deliberately a search rather than an index. `doc/apply.ts` already keeps the
 * authoritative traversal and this must never become a second one that can
 * disagree with it; a linear walk over a résumé -- tens of nodes, not
 * thousands -- costs nothing and cannot drift.
 */

import type { StudioDoc } from '@/contracts/doc';

type AnyRecord = Record<string, unknown>;

/**
 * The fields worth showing, in the order a person would read them.
 *
 * Only text a human wrote. Node ids, styles, geometry and grounding metadata
 * are all real fields on these objects and none of them belong in a sentence
 * that says "this is what it used to say".
 */
const READABLE = [
  'text',
  'summary',
  'title',
  'name',
  'company',
  'institution',
  'degree',
  'role',
  'years',
  'label',
  'value',
] as const;

function find(node: unknown, nid: string, seen: Set<unknown>): AnyRecord | null {
  if (!node || typeof node !== 'object' || seen.has(node)) return null;
  seen.add(node);

  if (Array.isArray(node)) {
    for (const item of node) {
      const hit = find(item, nid, seen);
      if (hit) return hit;
    }
    return null;
  }

  const record = node as AnyRecord;
  if (record.nid === nid) return record;

  for (const value of Object.values(record)) {
    if (value && typeof value === 'object') {
      const hit = find(value, nid, seen);
      if (hit) return hit;
    }
  }
  return null;
}

/** The node with this id, anywhere in the document. */
export function nodeById(doc: StudioDoc | null, nid: string): AnyRecord | null {
  if (!doc) return null;
  return find(doc, nid, new Set());
}

/**
 * What a node says, as one line a person can read.
 *
 * An entry has no single text of its own, so it is rendered as the fields that
 * identify it -- "Senior Backend Engineer · Northwind Systems" -- which is how
 * someone would name that row out loud. Returns an empty string for a node
 * that carries no readable text at all (a page, a shape), because showing
 * "before: nothing" is worse than showing no before at all.
 */
export function textOf(doc: StudioDoc | null, nid: string): string {
  const node = nodeById(doc, nid);
  if (!node) return '';

  // A frame is a window onto content, not content. Read what it renders, so a
  // revision row for a placed box says what the box says rather than
  // "Removed" -- a frame carries no readable field of its own, and the empty
  // string is what the caller draws that label from.
  const ref = node.ref;
  if (typeof ref === 'string' && ref) return textOf(doc, ref);

  // A text block's words are in its lines. Without this the walk below found
  // `role` first and a footer read as "caption".
  // A shape says nothing and never will -- it is decoration, and no ATS reads
  // it. Named rather than left empty, because the empty string is what a
  // revision row draws "Removed" from, and a rule that was just drawn is not
  // a removal.
  const shape = node.shape;
  if (typeof shape === 'string' && shape) {
    return shape === 'line' ? 'Line' : `${shape[0].toUpperCase()}${shape.slice(1)}`;
  }

  // An image has no words either, but it has a description written for exactly
  // this purpose.
  if (typeof node.asset === 'string' && node.asset) {
    const alt = typeof node.alt === 'string' ? node.alt.trim() : '';
    return alt || 'Image';
  }

  const lines = node.lines;
  if (Array.isArray(lines)) {
    const said = lines
      .map((line) => (line as AnyRecord | null)?.text)
      .filter((text): text is string => typeof text === 'string' && text.trim() !== '')
      .join(' ');
    if (said) return said;
  }

  const parts: string[] = [];
  for (const field of READABLE) {
    const value = node[field];
    if (typeof value === 'string' && value.trim()) parts.push(value.trim());
    // One field is enough when it is the node's whole content.
    if (field === 'text' && parts.length) return parts[0];
    if (field === 'summary' && parts.length) return parts[0];
  }
  return parts.slice(0, 2).join(' · ');
}

/**
 * A field path -- `set_field` addresses `exp_7f3a2.title`, not a bare nid.
 *
 * Split once from the left so a value containing a dot survives, which node
 * ids do not but future paths might.
 *
 * **Not every owner is a node.** `personal` is a plain object hanging off the
 * document root with no `nid` of its own, so resolving it through `nodeById`
 * finds nothing -- and `personal.*` is the single most common Tier C target
 * there is, because `tier_of` returns "C" for every field on it. Looked up
 * only through the index, the consent gate for a change to someone's name,
 * email or location showed them what it would become and nothing about what it
 * replaced, which is most of what they need to judge it.
 */
export function readTarget(doc: StudioDoc | null, target: string): string {
  const cut = target.indexOf('.');
  if (cut < 0) return textOf(doc, target);

  const owner = target.slice(0, cut);
  const attribute = target.slice(cut + 1);

  const container =
    nodeById(doc, owner) ??
    ((doc as unknown as AnyRecord | null)?.[owner] as AnyRecord | undefined);

  const value = container?.[attribute];
  return typeof value === 'string' ? value : '';
}
