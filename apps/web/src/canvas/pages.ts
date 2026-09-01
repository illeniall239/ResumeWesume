/**
 * What can be done to a page, and whether it is allowed.
 *
 * Pure, because the interesting part is a *rule*, not a gesture: deleting a
 * page takes its elements with it, and if one of those was the only frame
 * rendering your work history then the engine's coverage gate refuses the
 * whole batch. A disabled button with a reason is a far better answer than a
 * click that appears to work and then silently does nothing.
 *
 * The rule mirrors the server's rather than guessing at it: content is
 * stranded when a frame bound to it disappears and no other frame covers it.
 */

import type { DocOp, PageNode, StudioDoc } from '@/contracts/doc';

/** Refs that a frame somewhere else on the document still renders. */
function coveredElsewhere(doc: StudioDoc, doomed: PageNode): Set<string> {
  const kept = new Set<string>();
  for (const page of doc.pages) {
    if (page.nid === doomed.nid) continue;
    for (const element of page.elements) {
      if ('ref' in element) kept.add(element.ref);
    }
  }
  return kept;
}

export interface PageAction {
  allowed: boolean;
  /** Why not, phrased for a tooltip. */
  reason?: string;
}

/**
 * Whether this page can be deleted.
 *
 * A page holding only decoration -- images, shapes, hand-placed text -- goes
 * freely. A page holding the only frame for a section or a job does not,
 * because that content would have nowhere left to render.
 */
export function canDeletePage(doc: StudioDoc, pageNid: string): PageAction {
  if (doc.pages.length <= 1) {
    return { allowed: false, reason: 'A document needs at least one page.' };
  }

  const page = doc.pages.find((candidate) => candidate.nid === pageNid);
  if (!page) return { allowed: false, reason: 'No such page.' };

  const elsewhere = coveredElsewhere(doc, page);
  const stranded = page.elements
    .filter((element) => 'ref' in element)
    .map((element) => (element as { ref: string }).ref)
    // A free text block dies with its frame, so it strands nothing: the block
    // is removed in the same batch.
    .filter((ref) => !ref.startsWith('txb_'))
    .filter((ref) => !elsewhere.has(ref));

  if (stranded.length) {
    return {
      allowed: false,
      reason:
        'This page holds the only copy of some of your resume. Move it to another page first.',
    };
  }
  return { allowed: true };
}

/**
 * Ops to remove elements, taking the words that live only inside them.
 *
 * A frame bound to a section is a *view* of content that exists in the resume,
 * so removing it returns that content to the flow and deletes nothing. A frame
 * bound to a `txb_` block is the only home those words have: remove it alone
 * and the block is covered by no frame, which the engine's coverage gate reads
 * as stranded content and refuses -- rolling back the whole batch, so the box
 * simply would not delete. Worse, were it allowed, the words would survive as
 * an orphan nobody can see or reach while still reaching the ATS export.
 *
 * Cascading here rather than inside the engine's `_do_remove` is the same
 * choice `remove_entry` made: the extra ops ride in the same batch, so this is
 * one version and one undo step, and every op keeps exactly one effect and one
 * expressible inverse.
 */
export function removeElements(doc: StudioDoc, nids: readonly string[]): DocOp[] {
  const doomed = new Set(nids);

  // Refs a frame that *survives* this removal still renders. A block covered
  // by another frame is not stranded and must be left alone.
  const surviving = new Set<string>();
  for (const page of doc.pages) {
    for (const element of page.elements) {
      if ('ref' in element && !doomed.has(element.nid)) surviving.add(element.ref);
    }
  }

  const blocks: string[] = [];
  for (const page of doc.pages) {
    for (const element of page.elements) {
      if (!doomed.has(element.nid) || !('ref' in element)) continue;
      if (!element.ref.startsWith('txb_')) continue;
      if (surviving.has(element.ref) || blocks.includes(element.ref)) continue;
      blocks.push(element.ref);
    }
  }

  return [
    ...blocks.map((nid) => ({ op: 'remove_node', nid }) as DocOp),
    ...nids.map((nid) => ({ op: 'remove_node', nid }) as DocOp),
  ];
}

/**
 * Ops to delete a page.
 *
 * The page's elements go through `removeElements`, so a free text block on it
 * dies with its frame by the same rule that applies to deleting that box on
 * its own.
 */
export function deletePage(doc: StudioDoc, pageNid: string): DocOp[] {
  const page = doc.pages.find((candidate) => candidate.nid === pageNid);
  if (!page) return [];

  return [
    ...removeElements(doc, page.elements.map((element) => element.nid)).filter(
      // The elements themselves need no op: removing the page takes them.
      (op) => !page.elements.some((element) => element.nid === (op as { nid: string }).nid)
    ),
    { op: 'remove_node', nid: pageNid } as DocOp,
  ];
}

/** Ops to move a page one position earlier or later. */
export function movePage(doc: StudioDoc, pageNid: string, delta: -1 | 1): DocOp[] {
  const order = doc.pages.map((page) => page.nid);
  const from = order.indexOf(pageNid);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= order.length) return [];

  const next = [...order];
  [next[from], next[to]] = [next[to], next[from]];
  return [{ op: 'reorder', parent: 'pages', order: next } as DocOp];
}

export function canMovePage(doc: StudioDoc, pageNid: string, delta: -1 | 1): boolean {
  const index = doc.pages.findIndex((page) => page.nid === pageNid);
  return index >= 0 && index + delta >= 0 && index + delta < doc.pages.length;
}
