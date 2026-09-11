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

  scan(doc.blocks);
  for (const block of doc.blocks) scan(block.lines);
  scan(doc.pages);
  for (const page of doc.pages) scan(page.elements ?? []);

  return found;
}

/** Whether a nid is already taken, anywhere in the document. */
function exists(doc: StudioDoc, nid: string): boolean {
  // `containers` already walks pages and their elements, so this covers a
  // frame and a page as well as a bullet.
  return containers(doc).has(nid) || textNodes(doc).has(nid);
}

/**
 * Fill in what the server would fill in.
 *
 * This mirror exists to predict the server's answer, and the server validates
 * every inserted node against a Pydantic model that supplies defaults. Splicing
 * the caller's object in verbatim means the client briefly holds a shape the
 * server never produces -- and a page without `elements` is not a cosmetic
 * difference, it is an undefined that the next op iterates.
 *
 * Only containers are filled: a missing list is a crash, a missing scalar is
 * merely absent, and inventing values for scalars here would mean guessing at
 * the server's model rather than defending against a shape it cannot hold.
 */
function withDefaults(node: AnyRecord): AnyRecord {
  const nid = typeof node.nid === 'string' ? node.nid : '';
  if (nid.startsWith('pag_') && !Array.isArray(node.elements)) {
    return { ...node, elements: [] };
  }
  if (nid.startsWith('txb_') && !Array.isArray(node.lines)) {
    return { ...node, lines: [] };
  }
  if ((nid.startsWith('exp_') || nid.startsWith('prj_')) && !Array.isArray(node.bullets)) {
    return { ...node, bullets: [] };
  }
  if (nid.startsWith('sgp_') && !Array.isArray(node.items)) {
    return { ...node, items: [] };
  }
  return node;
}

/** Every placed element on any page, by nid. */
function elements(doc: StudioDoc): Map<string, AnyRecord> {
  const found = new Map<string, AnyRecord>();
  for (const page of doc.pages) {
    // Tolerant of a page with no `elements` array. The server fills that
    // default during validation, so a node this mirror has applied optimistically
    // can be missing it -- and a crash here takes the whole editor down for
    // something the next server response would have corrected on its own.
    for (const element of page.elements ?? []) {
      found.set(element.nid, element as unknown as AnyRecord);
    }
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
    case 'blocks':
      return doc.blocks;
    case 'pages':
      return doc.pages;
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
  for (const block of doc.blocks) if (block.nid === parent) return block.lines;
  for (const page of doc.pages) if (page.nid === parent) return page.elements;
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
      // Any addressable node, not just the three entry lists: the server
      // resolves this through its own index, so a skill group's label or a
      // custom section's item would otherwise edit on the server and not on
      // screen until the response landed.
      const found = containers(next).get(owner);
      const node = found?.list[found.index];
      if (!node) return doc;
      (node as AnyRecord)[field] = op.value;
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
      // The server rejects an insert whose nid is already in the document and
      // applies the rest of the batch; mirroring that keeps this a prediction
      // rather than a second opinion. It also makes an insert idempotent,
      // which matters because the ops that create a page are derived from a
      // measurement that can be taken twice -- replaying one produced two
      // pages sharing an id, which React renders as a duplicate-key warning
      // and the server would refuse outright.
      const nid = (op.node as AnyRecord)?.nid;
      if (typeof nid === 'string' && exists(next, nid)) return doc;
      const position = op.index < 0 ? list.length : Math.min(op.index, list.length);
      list.splice(position, 0, withDefaults(op.node));
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
      // De-duplicated, keeping the first mention: a repeated id would put the
      // same object in the list twice, which the server's duplicate gate reads
      // as a corrupt document and rolls the whole batch back.
      const seen = new Set<string>();
      const ordered: unknown[] = [];
      for (const nid of op.order) {
        const node = byId.get(nid);
        if (node && !seen.has(nid)) {
          seen.add(nid);
          ordered.push(node);
        }
      }
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

    case 'set_geometry': {
      const element = elements(next).get(op.nid);
      if (!element) return doc;
      const rect = element.rect as AnyRecord;
      for (const field of ['x', 'y', 'w', 'h'] as const) {
        const value = op[field];
        if (value !== null && value !== undefined) rect[field] = value;
      }
      if (op.rotation !== null && op.rotation !== undefined) {
        element.rotation = op.rotation;
      }
      return next;
    }

    case 'set_element_style': {
      const element = elements(next).get(op.nid);
      if (!element) return doc;
      const style = element.style as AnyRecord | undefined;
      for (const [key, value] of Object.entries(op.patch)) {
        // Style keys live on `style`, everything else on the element itself.
        // The server validates which is which; the mirror only has to land
        // the value somewhere the renderer will read it.
        if (style && key in style) style[key] = value;
        else element[key] = value;
      }
      return next;
    }

    case 'set_section': {
      const section = next.sections.find((candidate) => candidate.key === op.key);
      if (section) {
        if (op.visible !== null && op.visible !== undefined) section.visible = op.visible;
        if (op.order !== null && op.order !== undefined) section.order = op.order;
        return next;
      }

      // A section that exists as content but has no row in the order yet.
      //
      // `add_section` creates a custom section and, in the same batch, the row
      // that positions it. The server mints the row when it meets that op; this
      // mirror used to return the document untouched, so for the length of the
      // turn the client held a section the order had never heard of -- and both
      // renderers draw one of those in a tail after everything the order
      // accounts for. Asked for Certifications after Education, the page showed
      // it last while the server's copy had it in the right place, and the two
      // only agreed once the turn's document arrived.
      //
      // Mirroring the server here is the whole job of this file: an optimistic
      // edit that disagrees with the server is worse than no optimistic edit.
      const own = next.custom.find((candidate) => candidate.key === op.key);
      if (!own) return doc;
      next.sections = [
        ...next.sections,
        {
          key: op.key,
          label: own.label || op.key,
          visible: op.visible ?? true,
          order: op.order ?? next.sections.length,
        },
      ];
      return next;
    }

    default:
      return doc;
  }
}

export function applyOps(doc: StudioDoc, ops: DocOp[]): StudioDoc {
  return ops.reduce(applyOp, doc);
}
