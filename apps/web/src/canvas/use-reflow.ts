/**
 * Measuring a freshly laid-out document and committing the correction.
 *
 * Runs once per document *version*, and only when the stored geometry
 * disagrees with what the browser rendered. On a migrated document it always
 * does — the server placed the frames without being able to measure them — and
 * until it runs the sections are drawn on top of one another.
 *
 * Version, not page ids. Keyed on the page ids alone this ran exactly once and
 * then never again, because editing text does not change what the pages are
 * called. That is fine for a document nobody edits and wrong for this one: an
 * agent turn rewrites a two-line bullet into four lines, the frame keeps the
 * height measured for the old text, and the section below is drawn over the
 * top of it. Dividers through the middle of a sentence is what that looks like
 * on screen.
 *
 * Re-running is cheap and it terminates: the pass commits ops, the version
 * changes, the pass measures once more, finds nothing to correct, and stops.
 *
 * The correction is committed as ordinary ops, so it takes one version, is
 * undoable, and is visible in the op log like any other edit. It is not a
 * special path that writes geometry behind the engine's back.
 */

'use client';

import { useEffect, useRef } from 'react';

import type { DocOp, Rect, StudioDoc } from '@/contracts/doc';

import { reflow, type MeasuredFrame } from './reflow';
import { useView } from './view';
import { pageSpec, pxToPt } from './units';

/**
 * Read every frame's rendered height out of the DOM, in points.
 *
 * `zoom` matters and is easy to forget: a measured rect comes back in screen
 * pixels, so at 150% every frame reads half again as tall and the pass would
 * "correct" the document to a layout nobody asked for.
 */
export function measure(root: HTMLElement, doc: StudioDoc, zoom = 1): MeasuredFrame[] {
  const byNid = new Map<string, { page: string; rect: Rect }>();
  for (const page of doc.pages) {
    for (const element of page.elements) {
      if (!('ref' in element)) continue;
      // Anything placed by hand is not part of the column this pass corrects.
      // Measuring it swept it into the stack and overwrote the position the
      // user dragged it to, so neither a text box nor a job moved onto page
      // two would stay where it was put. Images and shapes are already outside
      // by not being frames at all.
      //
      // Two rules, not one: `pinned` is set the moment something is dragged,
      // while a text box is hand-placed from the instant it is created -- and
      // documents made before `pinned` existed carry boxes without the flag.
      if (element.pinned || element.ref.startsWith('txb_')) continue;
      byNid.set(element.nid, { page: page.nid, rect: element.rect });
    }
  }

  const measured: MeasuredFrame[] = [];
  for (const node of root.querySelectorAll<HTMLElement>('.element--frame')) {
    const nid = node.dataset.element;
    const known = nid ? byNid.get(nid) : undefined;
    if (!nid || !known) continue;
    measured.push({
      nid,
      page: known.page,
      rect: known.rect,
      height: pxToPt(node.getBoundingClientRect().height / (zoom > 0 ? zoom : 1)),
    });
  }

  // In the order the *document* declares, not the order it is stored in and
  // not the order it currently happens to be drawn in.
  //
  // `reflow` stacks frames in the order it is given them, so this call decides
  // what the page ends up looking like. Both of the obvious inputs are wrong:
  //
  //   `page.elements` order is not the reading order. Moving a section rewrites
  //   every frame's `y` and leaves the array alone -- deliberately, so nothing
  //   keyed on element identity has to be rebuilt. Handed that, this pass
  //   restacked the old arrangement on the next paint and the section sprang
  //   back where it came from.
  //
  //   `rect.y` is the order a reader sees, which sounds authoritative and is
  //   not, because during a turn it is stale. The server re-stacks a batch that
  //   moves a section; the client mirror does not. So the optimistic document
  //   still has a newly added section's frame where `cover_for` first put it --
  //   the foot of the last page -- and this pass measured that, stacked it
  //   last, and committed `set_geometry` back over the server's correct answer.
  //   The op log showed it exactly: one agent batch placing Certifications
  //   third, immediately followed by a `layout` batch putting it at y=415.
  //
  // The order that is neither stale nor guessed is the one the document
  // declares, which is what `layout` on the server and `DocumentFlow` in the
  // browser both already render by. Deriving it here means this pass can only
  // ever agree with them.
  const flow = flowOrder(doc);
  const rank = new Map(flow.map((ref, index) => [ref, index]));
  const pageIndex = new Map(doc.pages.map((page, index) => [page.nid, index]));
  const byNidRef = new Map<string, string>();
  for (const page of doc.pages) {
    for (const element of page.elements) {
      if ('ref' in element) byNidRef.set(element.nid, element.ref);
    }
  }

  return measured.sort((a, b) => {
    const aRank = rank.get(byNidRef.get(a.nid) ?? '');
    const bRank = rank.get(byNidRef.get(b.nid) ?? '');
    // Anything the flow does not name -- a hand-placed box -- keeps its
    // geometric position relative to the rest rather than being swept to one
    // end. It is excluded from the stack above anyway; this only keeps the
    // comparator total.
    if (aRank === undefined || bRank === undefined) {
      return (
        (pageIndex.get(a.page) ?? 0) - (pageIndex.get(b.page) ?? 0) ||
        a.rect.y - b.rect.y
      );
    }
    return aRank - bRank;
  });
}

/**
 * Every frame ref the document would render, in reading order.
 *
 * Mirrors `autolayout.layout` on the server: the header, then each visible
 * section in `order`, and inside a section its own heading frame followed by
 * one frame per entry. A custom section is keyed by its node id, because that
 * is what both renderers resolve it by.
 */
export function flowOrder(doc: StudioDoc): string[] {
  // Defensive on every list. This runs against whatever the store currently
  // holds, which mid-load or in a fixture can be a document missing the arrays
  // the schema would have filled in -- and a measure pass that throws takes the
  // whole correction with it, silently, inside a rAF.
  const order: string[] = ['personal'];
  const all = doc.sections ?? [];
  const custom = doc.custom ?? [];
  const visible = all.filter((meta) => meta.visible);
  const declared = [...(visible.length ? visible : all)].sort(
    (a, b) => a.order - b.order
  );

  for (const meta of declared) {
    const own = custom.find((section) => section.key === meta.key);
    if (own) {
      order.push(own.nid);
      continue;
    }
    order.push(meta.key);
    const entries =
      meta.key === 'experience'
        ? doc.experience
        : meta.key === 'education'
          ? doc.education
          : meta.key === 'projects'
            ? doc.projects
            : [];
    for (const entry of entries ?? []) order.push(entry.nid);
  }
  return order;
}

/**
 * Correct a document's geometry from what the browser actually rendered.
 *
 * `commit` is the store's `edit`. Guarded by a ref rather than by state: this
 * must run at most once per document, and re-running on its own result would
 * be a loop that writes a version every frame.
 */
export function useReflow(
  ref: React.RefObject<HTMLElement | null>,
  doc: StudioDoc | null,
  commit: (ops: DocOp[]) => Promise<void> | void,
  version = 0
): void {
  const zoom = useView((state) => state.zoom);
  const done = useRef<string | null>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root || !doc?.pages.length) return;

    const signature = reflowSignature(doc, version);
    if (done.current === signature) return;

    // One frame after paint, so fonts and layout have settled. Measuring in
    // the same tick reads pre-layout zeros.
    const handle = requestAnimationFrame(() => {
      const frames = measure(root, doc, zoom);
      if (!frames.length) return;

      const spec = pageSpec(doc.pages[0].size, doc.pages[0].orientation);
      const pageOrder = doc.pages.map((page) => page.nid);
      const result = reflow(
        frames,
        {
          contentHeight: spec.height - spec.margin * 2,
          contentWidth: spec.width - spec.margin * 2,
          top: spec.margin,
        },
        pageOrder
      );

      done.current = signature;
      if (!result.changed) return;

      const ops: DocOp[] = [];

      // Pages first, so the moves below have somewhere to land. Minted once
      // and used for both the inserts and the placement lookup below, so the
      // two can never disagree about what the third page is called.
      const minted = mintPages(pageOrder, result.pages);
      for (const nid of minted) {
        ops.push({
          op: 'insert_node',
          parent: 'pages',
          index: -1,
          // Every field the renderer and the client mirror read, not just the
          // ones the server needs. The server fills defaults on validation;
          // `doc/apply.ts` splices the node in verbatim, so a page without
          // `elements` reaches the very next op as an undefined it iterates.
          node: {
            nid,
            size: doc.pages[0].size,
            orientation: doc.pages[0].orientation,
            background: null,
            elements: [],
          },
        } as DocOp);
      }
      const pages = [...pageOrder, ...minted];

      const current = new Map(frames.map((frame) => [frame.nid, frame]));
      for (const placement of result.placements) {
        const frame = current.get(placement.nid);
        if (!frame) continue;

        const target = pages[placement.page];
        if (target && frame.page !== target) {
          ops.push({
            op: 'move_node',
            nid: placement.nid,
            parent: target,
            index: -1,
          } as DocOp);
        }
        ops.push({
          op: 'set_geometry',
          nid: placement.nid,
          y: placement.y,
          h: placement.height,
        } as DocOp);
      }

      if (ops.length) void commit(ops);
    });

    return () => cancelAnimationFrame(handle);
  }, [ref, doc, commit, zoom]);
}

/**
 * What re-arms the reflow: *which* pages exist, never what order they are in.
 *
 * This pass corrects heights the server could not measure, and it stacks frames
 * down the pages to do it. Re-running it is therefore a full repagination, so
 * what re-arms it is load-bearing. Keyed on page order, moving a page with the
 * up arrow re-armed it: the click emitted one clean `reorder`, and the pass
 * immediately followed with thirteen `set_geometry` and four `move_node`,
 * pulling frames across the boundary to refill the newly-first page and landing
 * the header and summary *after* the education section.
 *
 * Sorted, so adding or deleting a page still re-arms it -- content genuinely
 * has to be restacked then -- while reordering the same set does not.
 */
export function reflowSignature(doc: StudioDoc, version = 0): string {
  return [
    version,
    ...doc.pages.map((page) => page.nid).sort(),
  ].join(',');
}

/**
 * Ids for the sheets the reflow needs and the document does not have.
 *
 * Derived from the position, not random: the effect can be torn down and
 * re-run before its ops land, and a fresh id each time would insert a second
 * empty page.
 *
 * Derived is not the same as unused, though. Position 2's id belongs to the
 * third page ever needed -- delete the second page and the document still
 * holds it while the count says to mint it again. That collision reached the
 * screen as two pages sharing `pag_27enw` and React rendering one of them,
 * so a taken id is skipped rather than proposed.
 */
export function mintPages(existing: readonly string[], wanted: number): string[] {
  const taken = new Set(existing);
  const minted: string[] = [];
  let index = existing.length;
  while (existing.length + minted.length < wanted) {
    const nid = freshPageId(index);
    index += 1;
    if (taken.has(nid)) continue;
    taken.add(nid);
    minted.push(nid);
  }
  return minted;
}

function freshPageId(index: number): string {
  const alphabet = '0123456789abcdefghjkmnpqrstvwxyz';
  let suffix = '';
  let value = index + 1;
  for (let position = 0; position < 5; position += 1) {
    suffix += alphabet[value % alphabet.length];
    value = Math.floor(value / alphabet.length) + 7 * (position + 1);
  }
  return `pag_${suffix}`;
}
