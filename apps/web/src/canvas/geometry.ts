/**
 * Rectangle arithmetic for the canvas.
 *
 * No DOM, no React, no units beyond points. Everything a drag needs to compute
 * lives here so it can be tested directly — the interaction layer above is
 * then only pointer plumbing, which is the part that is genuinely awkward to
 * test and the part least likely to be wrong.
 */

import type { Rect } from '@/contracts/doc';

/** The eight handles, plus the body. */
export type Handle =
  | 'nw'
  | 'n'
  | 'ne'
  | 'e'
  | 'se'
  | 's'
  | 'sw'
  | 'w';

export const HANDLES: readonly Handle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

/** Smallest a box may be dragged to. Below this it cannot be grabbed again. */
export const MIN_SIZE = 12;

export function right(rect: Rect): number {
  return rect.x + rect.w;
}

export function bottom(rect: Rect): number {
  return rect.y + rect.h;
}

export function centerX(rect: Rect): number {
  return rect.x + rect.w / 2;
}

export function centerY(rect: Rect): number {
  return rect.y + rect.h / 2;
}

export function translate(rect: Rect, dx: number, dy: number): Rect {
  return { ...rect, x: rect.x + dx, y: rect.y + dy };
}

/** The smallest rect containing all of them. */
export function bounds(rects: readonly Rect[]): Rect | null {
  if (!rects.length) return null;
  const x = Math.min(...rects.map((r) => r.x));
  const y = Math.min(...rects.map((r) => r.y));
  const w = Math.max(...rects.map(right)) - x;
  const h = Math.max(...rects.map(bottom)) - y;
  return { x, y, w, h };
}

export function contains(rect: Rect, x: number, y: number): boolean {
  return x >= rect.x && x <= right(rect) && y >= rect.y && y <= bottom(rect);
}

/** Do two rects overlap at all? Used by the marquee. */
export function intersects(a: Rect, b: Rect): boolean {
  return !(right(a) < b.x || right(b) < a.x || bottom(a) < b.y || bottom(b) < a.y);
}

/** The rect between two points, in any drag direction. */
export function marquee(x1: number, y1: number, x2: number, y2: number): Rect {
  return {
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    w: Math.abs(x2 - x1),
    h: Math.abs(y2 - y1),
  };
}

export interface ResizeOptions {
  /** Keep the original proportions. */
  aspect?: boolean;
  /** Grow from the centre rather than the opposite corner. */
  fromCenter?: boolean;
  min?: number;
}

/**
 * Apply a resize by dragging `handle` by (dx, dy).
 *
 * The edge opposite the handle stays put, which is what makes a resize feel
 * like grabbing that edge rather than moving the whole box. Dragging a handle
 * past its opposite edge is clamped rather than allowed to invert: a flipped
 * rect has negative dimensions that every consumer downstream would have to
 * defend against, for a gesture nobody means to make.
 */
export function resize(
  rect: Rect,
  handle: Handle,
  dx: number,
  dy: number,
  options: ResizeOptions = {}
): Rect {
  const min = options.min ?? MIN_SIZE;
  const west = handle.includes('w');
  const east = handle.includes('e');
  const north = handle.startsWith('n');
  const south = handle.startsWith('s');

  let { x, y, w, h } = rect;

  if (options.fromCenter) {
    if (east || west) {
      const delta = east ? dx : -dx;
      w = Math.max(min, rect.w + delta * 2);
      x = centerX(rect) - w / 2;
    }
    if (north || south) {
      const delta = south ? dy : -dy;
      h = Math.max(min, rect.h + delta * 2);
      y = centerY(rect) - h / 2;
    }
  } else {
    if (east) w = Math.max(min, rect.w + dx);
    if (west) {
      w = Math.max(min, rect.w - dx);
      x = right(rect) - w;
    }
    if (south) h = Math.max(min, rect.h + dy);
    if (north) {
      h = Math.max(min, rect.h - dy);
      y = bottom(rect) - h;
    }
  }

  if (options.aspect && rect.w > 0 && rect.h > 0) {
    const ratio = rect.w / rect.h;
    // Drive from whichever axis the handle actually controls; a corner uses
    // the larger change so the box tracks the pointer rather than lagging it.
    const drivesWidth =
      (east || west) && (!(north || south) || Math.abs(dx) >= Math.abs(dy));
    if (drivesWidth) h = Math.max(min, w / ratio);
    else w = Math.max(min, h * ratio);

    if (west) x = right(rect) - w;
    if (north) y = bottom(rect) - h;
    if (options.fromCenter) {
      x = centerX(rect) - w / 2;
      y = centerY(rect) - h / 2;
    }
  }

  return { x, y, w, h };
}

/** Keep a rect inside a page, without resizing it. */
export function clampToPage(rect: Rect, page: { width: number; height: number }): Rect {
  return {
    ...rect,
    x: Math.min(Math.max(rect.x, 0), Math.max(0, page.width - rect.w)),
    y: Math.min(Math.max(rect.y, 0), Math.max(0, page.height - rect.h)),
  };
}

/** Kinds that may be rotated. */
const ROTATABLE = ['img_', 'shp_'];

/**
 * Whether this element can be rotated.
 *
 * Frames cannot, deliberately. A rotated `contentEditable` breaks caret
 * positioning in every browser -- clicking into text puts the cursor in the
 * wrong place, and selection drags along the unrotated axis. The schema
 * carries `rotation` on frames from day one so allowing it later is a UI
 * change rather than a migration, but shipping it now would trade a real
 * editing bug for a decorative flourish.
 */
export function isRotatable(nid: string): boolean {
  return ROTATABLE.some((prefix) => nid.startsWith(prefix));
}

/** Snap increment while Shift is held, in degrees. */
export const ROTATION_SNAP = 15;

/**
 * The angle, in degrees, from a rect's centre to a point.
 *
 * Zero points up, matching the handle's resting position above the box, and
 * increases clockwise like CSS `rotate()`. Getting either convention wrong
 * makes the box spin the wrong way under the pointer, which reads as broken
 * long before anyone works out why.
 */
export function angleTo(rect: Rect, x: number, y: number): number {
  const radians = Math.atan2(y - centerY(rect), x - centerX(rect));
  return (radians * 180) / Math.PI + 90;
}

/** Fold any angle into [0, 360). */
export function normaliseAngle(degrees: number): number {
  return ((degrees % 360) + 360) % 360;
}

/**
 * The rotation a drag should produce.
 *
 * `start` is where the pointer grabbed the handle and `current` where it is
 * now, so the box turns *with* the pointer rather than snapping its handle to
 * it -- grabbing a handle should never itself move anything.
 */
export function rotationFor(
  original: number,
  rect: Rect,
  start: { x: number; y: number },
  current: { x: number; y: number },
  snap = false
): number {
  const delta = angleTo(rect, current.x, current.y) - angleTo(rect, start.x, start.y);
  const raw = normaliseAngle(original + delta);
  return snap ? normaliseAngle(Math.round(raw / ROTATION_SNAP) * ROTATION_SNAP) : raw;
}
