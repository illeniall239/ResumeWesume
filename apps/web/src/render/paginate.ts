/**
 * Working out where the printed pages will break.
 *
 * The studio renders one continuous flow while the export is paginated, so
 * until now the two disagreed silently: an entire section could be pushed to a
 * second page, leaving the first 87% empty, and nothing on screen said so. The
 * user found out when they opened the PDF.
 *
 * This is deliberately a pure function over measurements rather than anything
 * that touches the DOM, because the interesting part is the arithmetic and the
 * arithmetic is what has to match Chromium. Given each block's natural offset
 * and height in one continuous flow, it decides which blocks start a new sheet
 * and how much space to insert before them.
 *
 * The rule it mirrors is the print stylesheet's: **a block marked unbreakable
 * moves whole rather than splitting.** That is the behaviour that surprises
 * people — a job two lines too tall does not lose two lines to the next page,
 * it leaves a gap and moves entirely — so a naive ruler drawn every 277mm
 * would be wrong in exactly the case worth showing.
 */

export interface MeasuredBlock {
  /** Stable identity — a node id, or `title:experience` for a section heading. */
  key: string;
  /**
   * Offset from the top of the continuous flow, in px, before any spacing.
   *
   * Measured from where the text starts — the column's **content** box — not
   * from the edge of the paper. The page margin sits outside this coordinate
   * system, which is what lets `step` be a clean paper-to-paper distance.
   */
  top: number;
  height: number;
  /** A section heading, which must never be the last thing on a page. */
  heading?: boolean;
}

export interface PageGeometry {
  /** Usable height of one sheet, in px: paper height less both margins. */
  contentHeight: number;
  /**
   * Distance between the top of one sheet's content and the next's, in px.
   * Larger than `contentHeight` by the two margins plus the visual gap between
   * sheets, which is the space a break has to skip over.
   */
  step: number;
}

export interface Pagination {
  /** Spacer height, in px, to insert before the block with this key. */
  breaks: ReadonlyMap<string, number>;
  pages: number;
}

const EMPTY: Pagination = { breaks: new Map(), pages: 1 };

/**
 * Decide the page breaks for one measured flow.
 *
 * Blocks must arrive in document order. A block taller than a whole page is
 * left to overflow rather than being hidden or clipped: it is rare, it is
 * what the browser does when `break-inside: avoid` cannot be honoured, and a
 * silently truncated bullet would be far worse than an ugly one.
 */
export function paginate(
  blocks: readonly MeasuredBlock[],
  geometry: PageGeometry
): Pagination {
  if (!blocks.length || geometry.contentHeight <= 0 || geometry.step <= 0) {
    return EMPTY;
  }

  const breaks = new Map<string, number>();
  let page = 0;
  let pushed = 0;

  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index];
    const start = block.top + pushed;
    const pageBottom = page * geometry.step + geometry.contentHeight;

    if (start + block.height <= pageBottom) continue;

    // Taller than any page. Let it run over; the next block starts fresh.
    if (block.height > geometry.contentHeight) {
      page += 1;
      continue;
    }

    // A heading immediately above goes with the block it introduces, or it is
    // left stranded at the foot of a page announcing nothing.
    //
    // Unless the two cannot share a page at all. Moving the heading then puts
    // it alone on the next sheet and pushes the block to a third — stranding
    // it worse and costing a page. Browsers drop `break-after: avoid` in the
    // same situation, for the same reason.
    let target = block;
    let targetIndex = index;
    const previous = blocks[index - 1];
    if (
      previous?.heading &&
      !breaks.has(previous.key) &&
      previous.height + block.height <= geometry.contentHeight
    ) {
      target = previous;
      targetIndex = index - 1;
    }

    const nextTop = (page + 1) * geometry.step;
    const spacer = nextTop - (target.top + pushed);
    if (spacer <= 0) {
      page += 1;
      continue;
    }

    breaks.set(target.key, spacer);
    pushed += spacer;
    page += 1;

    // Re-examine this block if the heading above it was what moved: it has
    // shifted down with the heading and may now fit.
    if (targetIndex !== index) index -= 1;
  }

  return { breaks, pages: page + 1 };
}

/** Millimetres to CSS pixels, at the 96dpi the CSS `mm` unit assumes. */
export function mmToPx(mm: number): number {
  return (mm * 96) / 25.4;
}

/**
 * Page geometry from paper size and margins, both in millimetres.
 *
 * `gap` is the visual space drawn between sheets; it is part of the step so
 * that content pushed to a new sheet clears it.
 */
export function geometryFor({
  heightMm,
  marginMm,
  gapPx,
}: {
  heightMm: number;
  marginMm: number;
  gapPx: number;
}): PageGeometry {
  const contentHeight = mmToPx(heightMm - marginMm * 2);
  return { contentHeight, step: mmToPx(heightMm) + gapPx };
}
