/**
 * Correcting a layout once the browser has measured it.
 *
 * The server places frames without knowing how tall their text is, because it
 * cannot know: it has no fonts, no line-breaking and no glyph widths, and an
 * estimate would disagree with Chromium — which is the whole class of bug that
 * made screen-versus-PDF divergence a recurring problem here before. So it
 * writes plausible placeholder heights and marks every frame `autogrow`.
 *
 * The browser is the only thing that can measure, so the browser corrects it.
 * On a freshly migrated document the difference is not subtle: a section
 * estimated at 96pt renders at 712, and the frames beneath it are drawn *on
 * top of* it until this runs.
 *
 * The rule is the one the flowing renderer already used, and it is worth
 * keeping because it is what people expect from a page: **a frame moves whole
 * or not at all.** One that will not fit on the rest of a page goes to the next
 * one, leaving a gap, rather than being split across the boundary.
 */

import type { Rect } from '@/contracts/doc';

export interface MeasuredFrame {
  nid: string;
  /** The page it currently sits on. */
  page: string;
  rect: Rect;
  /** What the browser actually rendered, in points. */
  height: number;
}

export interface ReflowGeometry {
  /** Usable height of one page: paper less both margins, in points. */
  contentHeight: number;
  /** Top of the content area, in points. */
  top: number;
  /**
   * Usable width of one page, in points.
   *
   * Only used to tell a frame that spans the sheet from one sitting in a
   * column. A layout with no columns never consults it.
   */
  contentWidth: number;
}

export interface Placement {
  nid: string;
  /** Index of the page it belongs on, counting from zero. */
  page: number;
  y: number;
  height: number;
}

export interface Reflow {
  placements: Placement[];
  /** How many pages the corrected layout needs. */
  pages: number;
  /** Whether anything actually moved. */
  changed: boolean;
}

/** Tolerance in points. Sub-point drift is not worth a version bump. */
const EPSILON = 0.5;

/** How far apart two frames' left edges may be and still be one column. */
const COLUMN_TOLERANCE = 2;

/**
 * The key naming the column a frame sits in.
 *
 * A frame as wide as the content area is not in a column at all — it spans the
 * sheet, and nothing may sit beside it. Everything else is grouped by its left
 * edge, which is what the server sets when it places a rail, and what a person
 * dragging a box left or right changes.
 */
const SPAN = 'span';

function columnOf(rect: Rect, contentWidth: number): string {
  if (rect.w >= contentWidth - COLUMN_TOLERANCE) return SPAN;
  return String(Math.round(rect.x / COLUMN_TOLERANCE));
}

interface Cursor {
  page: number;
  y: number;
}

/**
 * Stack measured frames down the pages they need, one cursor per column.
 *
 * Frames are taken in the order given, which is document order — for a
 * migrated document that is the order the sections already rendered in, so a
 * reflow cannot silently rearrange somebody's resume.
 *
 * **Columns are read off the geometry, never declared.** A single-column
 * document is every frame spanning the sheet, which collapses this to one
 * cursor and the exact arithmetic it had before — the reason the existing
 * behaviour is preserved to the point rather than approximated. A sidebar is
 * two groups of left edges, each filling and paginating independently, which
 * is what lets a short rail of skills sit beside a long column of jobs instead
 * of being stacked under it.
 *
 * Without this a layout could be *placed* in columns and not survive being
 * opened: the pass ran one cursor over every frame in document order and
 * rewrote its `y`, so the rail was dealt back into the main column on first
 * measure. That is why the two-column template that used to exist was removed
 * rather than fixed — the editor could not hold the arrangement the gallery
 * was advertising.
 */
export function reflow(
  frames: readonly MeasuredFrame[],
  geometry: ReflowGeometry,
  pageOrder: readonly string[]
): Reflow {
  const placements: Placement[] = [];
  const columns = new Map<string, Cursor>();
  const bottom = geometry.top + geometry.contentHeight;
  let changed = false;

  /**
   * Where a column that has not been seen yet begins.
   *
   * Not the top of the page: a spanning frame placed before it -- the header,
   * in every sidebar layout -- has already claimed that space, and a column
   * first met afterwards has to start below it or it is drawn over the header.
   */
  let floor: Cursor = { page: 0, y: geometry.top };

  const cursorFor = (key: string): Cursor => {
    const found = columns.get(key);
    if (found) return found;
    const fresh: Cursor = { ...floor };
    columns.set(key, fresh);
    return fresh;
  };

  /** The point every column has cleared: where a spanning frame must go. */
  const clearOfEverything = (): Cursor => {
    let page = 0;
    let y = geometry.top;
    for (const cursor of columns.values()) {
      if (cursor.page > page || (cursor.page === page && cursor.y > y)) {
        page = cursor.page;
        y = cursor.y;
      }
    }
    return { page, y };
  };

  for (const frame of frames) {
    const height = Math.max(frame.height, 0);
    const key = columnOf(frame.rect, geometry.contentWidth);
    const spanning = key === SPAN;
    const cursor = spanning ? clearOfEverything() : cursorFor(key);

    // Move whole rather than split. A frame taller than a whole page is left
    // to overflow: it is rare, it is what the browser does when it cannot
    // honour a break, and clipping it would delete content silently.
    if (cursor.y + height > bottom && height <= geometry.contentHeight && cursor.y > geometry.top) {
      cursor.page += 1;
      cursor.y = geometry.top;
    }

    const currentPage = pageOrder.indexOf(frame.page);
    if (
      currentPage !== cursor.page ||
      Math.abs(frame.rect.y - cursor.y) > EPSILON ||
      Math.abs(frame.rect.h - height) > EPSILON
    ) {
      changed = true;
    }

    placements.push({ nid: frame.nid, page: cursor.page, y: cursor.y, height });
    cursor.y += height;

    if (cursor.y > bottom) {
      // The frame overflowed the page on its own; the next one starts fresh.
      cursor.page += 1;
      cursor.y = geometry.top;
    }

    if (spanning) {
      // Nothing sits beside a full-width frame, so every column resumes below
      // it -- including the ones not seen yet, which is what `floor` carries.
      for (const existing of columns.keys()) columns.set(existing, { ...cursor });
      columns.set(SPAN, { ...cursor });
      floor = { ...cursor };
    }
  }

  const pages = placements.reduce((most, place) => Math.max(most, place.page + 1), 1);
  return { placements, pages, changed };
}
