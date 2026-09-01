/**
 * The order elements are written into the DOM.
 *
 * Chromium's PDF text layer follows **DOM order, not visual order**. A page
 * whose elements are emitted in paint order therefore extracts in paint order,
 * which is arbitrary — so a resume that looks like two tidy columns can come
 * out of a parser interleaved line by line, which is precisely the reputation
 * placed layouts have.
 *
 * The fix is to emit in reading order and restore paint order with `z-index`.
 * This module decides what reading order is.
 *
 * The interesting case is columns. A naive top-to-bottom sort zig-zags between
 * a sidebar and a main column, producing exactly the scrambling it was meant to
 * avoid. So elements are grouped into columns by horizontal overlap first, and
 * only then read top-to-bottom within each — the same shape of judgement the
 * PDF importer makes when it reads a two-column resume, arrived at from the
 * other side.
 */

import type { AnyElement, PageNode } from '@/contracts/doc';

/**
 * How much two elements must overlap horizontally to count as one column,
 * as a fraction of the narrower one. Below this they are side by side.
 */
const COLUMN_OVERLAP = 0.5;

function overlapRatio(a: AnyElement, b: AnyElement): number {
  const left = Math.max(a.rect.x, b.rect.x);
  const right = Math.min(a.rect.x + a.rect.w, b.rect.x + b.rect.w);
  const shared = right - left;
  if (shared <= 0) return 0;
  const narrower = Math.min(a.rect.w, b.rect.w);
  return narrower > 0 ? shared / narrower : 0;
}

/**
 * Group elements into columns by horizontal overlap.
 *
 * Transitive on purpose: a full-width header band overlaps both columns and
 * would otherwise merge them into one, so grouping runs left-to-right and an
 * element joins the first column it substantially overlaps.
 */
function columns(elements: readonly AnyElement[]): AnyElement[][] {
  const byLeft = [...elements].sort((a, b) => a.rect.x - b.rect.x);
  const groups: AnyElement[][] = [];

  for (const element of byLeft) {
    const home = groups.find((group) =>
      group.some((member) => overlapRatio(member, element) >= COLUMN_OVERLAP)
    );
    if (home) home.push(element);
    else groups.push([element]);
  }
  return groups;
}

/**
 * Elements of one page, in the order a reader (or a parser) should meet them.
 *
 * Shapes are included so the caller can render everything from one list; they
 * contribute no text, so their position in the order is irrelevant.
 */
export function readingOrder(page: PageNode): AnyElement[] {
  const groups = columns(page.elements);

  // Columns left to right; within a column, top to bottom.
  groups.sort((a, b) => leftEdge(a) - leftEdge(b));
  for (const group of groups) {
    group.sort((a, b) => a.rect.y - b.rect.y || a.rect.x - b.rect.x);
  }

  // A full-width element spanning every column is a banner, and belongs before
  // the columns it sits above rather than inside whichever it landed in.
  return groups.flat();
}

function leftEdge(group: readonly AnyElement[]): number {
  return Math.min(...group.map((element) => element.rect.x));
}

/**
 * Reading order across a whole document, honouring an explicit override.
 *
 * `doc.reading_order` is set only when someone has said the derived order is
 * wrong, so it wins outright — but unknown ids are dropped and anything it
 * omits is appended, the same salvage rule the engine's `reorder` uses. An
 * override that has drifted must not silently delete an element from the text
 * layer.
 */
export function documentReadingOrder(
  pages: readonly PageNode[],
  override?: readonly string[] | null
): AnyElement[] {
  const derived = pages.flatMap((page) => readingOrder(page));
  if (!override?.length) return derived;

  const byId = new Map(derived.map((element) => [element.nid, element]));
  const seen = new Set<string>();
  const ordered: AnyElement[] = [];

  for (const nid of override) {
    const element = byId.get(nid);
    if (element && !seen.has(nid)) {
      seen.add(nid);
      ordered.push(element);
    }
  }
  for (const element of derived) if (!seen.has(element.nid)) ordered.push(element);
  return ordered;
}
