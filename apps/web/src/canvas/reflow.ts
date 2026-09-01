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

/**
 * Stack measured frames down the pages they need.
 *
 * Frames are taken in the order given, which is document order — for a
 * migrated document that is the order the sections already rendered in, so a
 * reflow cannot silently rearrange somebody's resume.
 */
export function reflow(
  frames: readonly MeasuredFrame[],
  geometry: ReflowGeometry,
  pageOrder: readonly string[]
): Reflow {
  const placements: Placement[] = [];
  let page = 0;
  let y = geometry.top;
  let changed = false;

  for (const frame of frames) {
    const height = Math.max(frame.height, 0);

    // Move whole rather than split. A frame taller than a whole page is left
    // to overflow: it is rare, it is what the browser does when it cannot
    // honour a break, and clipping it would delete content silently.
    if (
      y + height > geometry.top + geometry.contentHeight &&
      height <= geometry.contentHeight &&
      y > geometry.top
    ) {
      page += 1;
      y = geometry.top;
    }

    const currentPage = pageOrder.indexOf(frame.page);
    if (
      currentPage !== page ||
      Math.abs(frame.rect.y - y) > EPSILON ||
      Math.abs(frame.rect.h - height) > EPSILON
    ) {
      changed = true;
    }

    placements.push({ nid: frame.nid, page, y, height });
    y += height;

    if (y > geometry.top + geometry.contentHeight) {
      // The frame overflowed the page on its own; the next one starts fresh.
      page += 1;
      y = geometry.top;
    }
  }

  return { placements, pages: Math.max(1, page + (y > geometry.top ? 1 : 0)), changed };
}
