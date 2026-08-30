/**
 * Client-side op mirror.
 *
 * The server is the source of truth and runs the real engine: gates, grounding,
 * tier checks, drift guards. This is a deliberately thin replica that applies
 * an already-validated op to the local copy so the change is visible the instant
 * `patch_applied` arrives, rather than a round trip later.
 *
 * It is not a second implementation of the engine and must never become one.
 * It runs no gates and makes no decisions: by the time an op reaches here the
 * server has already accepted it. The turn ends with a refetch that reconciles
 * anything this could not express, so a gap here degrades to a small delay
 * rather than a divergence.
 */

import type { DocOp, StudioDoc, TextNode } from '@/contracts/doc';

type AnyRecord = Record<string, unknown>;

/**
 * Every text-bearing node in the document, by id.
 *
 * Must match the server's TEXT_KINDS exactly: bullets, the summary, and skill
 * items. Missing a kind here does not corrupt anything, but it does make edits
 * to that kind invisible until the end-of-turn refetch, which is precisely the
 * lag this mirror exists to remove.
 */
function textNodes(doc: StudioDoc): Map<string, TextNode | { nid: string; text: string }> {
  const found = new Map<string, TextNode | { nid: string; text: string }>();
  if (doc.summary) found.set(doc.summary.nid, doc.summary);
  for (const entry of doc.experience) {
    for (const bullet of entry.bullets) found.set(bullet.nid, bullet);
  }
  for (const entry of doc.projects) {
    for (const bullet of entry.bullets) found.set(bullet.nid, bullet);
  }
  for (const entry of doc.education) {
    if (entry.detail) found.set(entry.detail.nid, entry.detail);
  }
  for (const group of doc.skills) {
    for (const item of group.items) found.set(item.nid, item);
  }
  for (const section of doc.custom) {
    if (section.text) found.set(section.text.nid, section.text);
    for (const item of section.items) {
      for (const bullet of item.bullets) found.set(bullet.nid, bullet);
    }
    for (const item of section.strings) found.set(item.nid, item);
  }
  return found;
}

/** Lists that can hold a removable node, keyed by the node's id. */
function containers(doc: StudioDoc): Map<string, { list: unknown[]; index: number }> {
  const found = new Map<string, { list: unknown[]; index: number }>();

  const scan = (list: unknown[]) => {
    list.forEach((node, index) => {
      const nid = (node as AnyRecord)?.nid;
      if (typeof nid === 'string') found.set(nid, { list, index });
    });
  };

  scan(doc.experience);
  scan(doc.education);
  scan(doc.projects);
  scan(doc.skills);
  scan(doc.custom);

  for (const entry of doc.experience) scan(entry.bullets);
  for (const entry of doc.projects) scan(entry.bullets);
  for (const group of doc.skills) scan(group.items);
  for (const section of doc.custom) {
    scan(section.items);
    scan(section.strings);
    for (const item of section.items) scan(item.bullets);
  }

  return found;
}

function listFor(doc: StudioDoc, parent: string): unknown[] | null {
  switch (parent) {
    case 'experience':
      return doc.experience;
    case 'education':
      return doc.education;
    case 'projects':
      return doc.projects;
    case 'skills':
      return doc.skills;
    case 'custom':
      return doc.custom;
    default:
      break;
  }

  for (const entry of doc.experience) if (entry.nid === parent) return entry.bullets;
  for (const entry of doc.projects) if (entry.nid === parent) return entry.bullets;
  for (const group of doc.skills) if (group.nid === parent) return group.items;
  for (const section of doc.custom) {
    if (section.nid === parent) return section.items;
    for (const item of section.items) if (item.nid === parent) return item.bullets;
  }
  return null;
}

/**
 * Apply one op to a copy of the document.
 *
 * Returns the original object when an op cannot be mirrored, so the caller can
 * tell nothing changed and wait for the reconciling refetch.
 */
export function applyOp(doc: StudioDoc, op: DocOp): StudioDoc {
  const next: StudioDoc = structuredClone(doc);

  switch (op.op) {
    case 'set_text': {
      const node = textNodes(next).get(op.nid);
      if (!node) return doc;
      node.text = op.value;
      return next;
    }

    case 'set_style': {
      const node = textNodes(next).get(op.nid);
      // Skills carry text but no style; the server rejects this too.
      if (!node || !('style' in node)) return doc;
      (node as TextNode).style = op.style;
      return next;
    }

    case 'set_field': {
      const [owner, field] = op.target.split('.');
      if (!field) return doc;
      if (owner === 'personal') {
        (next.personal as unknown as AnyRecord)[field] = op.value;
        return next;
      }
      const entry = [...next.experience, ...next.education, ...next.projects].find(
        (candidate) => candidate.nid === owner
      );
      if (!entry) return doc;
      (entry as unknown as AnyRecord)[field] = op.value;
      return next;
    }

    case 'remove_node': {
      const found = containers(next).get(op.nid);
      if (!found) return doc;
      found.list.splice(found.index, 1);
      return next;
    }

    case 'insert_node': {
      const list = listFor(next, op.parent);
      if (!list) return doc;
      const position = op.index < 0 ? list.length : Math.min(op.index, list.length);
      list.splice(position, 0, op.node);
      return next;
    }

    case 'reorder': {
      const list = listFor(next, op.parent);
      if (!list) return doc;
      const byId = new Map(
        list.map((node) => [(node as AnyRecord).nid as string, node])
      );
      // Same salvage rule as the server: keep what we recognise in the order
      // asked for, then append anything omitted so nothing is lost.
      const ordered = op.order.map((nid) => byId.get(nid)).filter(Boolean) as unknown[];
      const placed = new Set(ordered);
      for (const node of list) if (!placed.has(node)) ordered.push(node);
      list.splice(0, list.length, ...ordered);
      return next;
    }

    case 'move_node': {
      const found = containers(next).get(op.nid);
      const target = listFor(next, op.parent);
      if (!found || !target) return doc;
      const [node] = found.list.splice(found.index, 1);
      const position = op.index < 0 ? target.length : Math.min(op.index, target.length);
      target.splice(position, 0, node);
      return next;
    }

    case 'set_section': {
      const section = next.sections.find((candidate) => candidate.key === op.key);
      if (!section) return doc;
      if (op.visible !== null && op.visible !== undefined) section.visible = op.visible;
      if (op.order !== null && op.order !== undefined) section.order = op.order;
      return next;
    }

    default:
      return doc;
  }
}

export function applyOps(doc: StudioDoc, ops: DocOp[]): StudioDoc {
  return ops.reduce(applyOp, doc);
}
