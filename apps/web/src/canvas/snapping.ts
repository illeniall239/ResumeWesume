/**
 * Alignment guides, and the snapping that goes with them.
 *
 * The feel being chased is Canva's: drag a box and it catches on its
 * neighbours' edges and centres, with a line appearing to say *why* it caught.
 * Three details separate that from something that merely fights you.
 *
 * **The threshold is in screen pixels, not document points.** Convert it by the
 * current zoom or snapping is glue at 50% and does nothing at 200%, because a
 * fixed 6pt is a different distance under the pointer at every zoom level.
 *
 * **The guide extends across everything it aligns.** Drawing only the snapped
 * line says "a line appeared"; extending it to span the moving selection *and*
 * every element sharing that line says "these three are aligned", which is the
 * whole informational content of the gesture.
 *
 * **It can always be defeated.** A snap the user cannot override is a bug, so
 * the caller passes `enabled: false` while a modifier is held.
 *
 * Candidates are built once per gesture, not per pointer event: they cannot
 * change while a drag is in flight, and rebuilding them 60 times a second is
 * the difference between a solver and a stutter.
 */

import type { Rect } from '@/contracts/doc';

import { bottom, centerX, centerY, right } from './geometry';

export type Axis = 'x' | 'y';

/** Why a line exists, which decides how ties are broken. */
export type LineKind = 'edge' | 'center';

export interface Candidate {
  at: number;
  kind: LineKind;
  /** Elements sitting on this line, for drawing the guide across them. */
  owners: string[];
  /** Extent along the *other* axis, so the guide can be drawn. */
  from: number;
  to: number;
}

export interface CandidateSet {
  x: Candidate[];
  y: Candidate[];
}

export interface Guide {
  axis: Axis;
  at: number;
  from: number;
  to: number;
  owners: string[];
}

export interface SnapResult {
  dx: number;
  dy: number;
  guides: Guide[];
}

export interface SnapOptions {
  /** Snap distance in *screen* pixels. */
  threshold?: number;
  /** Current zoom, so the threshold means the same thing at any scale. */
  zoom?: number;
  enabled?: boolean;
}

const DEFAULT_THRESHOLD = 6;

function add(list: Candidate[], at: number, kind: LineKind, owner: string, from: number, to: number): void {
  const existing = list.find((c) => Math.abs(c.at - at) < 0.01 && c.kind === kind);
  if (existing) {
    existing.owners.push(owner);
    existing.from = Math.min(existing.from, from);
    existing.to = Math.max(existing.to, to);
    return;
  }
  list.push({ at, kind, owners: [owner], from, to });
}

/**
 * Lines a drag can catch on: every other element's edges and centres, plus the
 * page's margins and centre.
 *
 * Built once per gesture. `moving` is excluded because an element cannot align
 * to itself.
 */
export function buildCandidates(
  others: readonly { nid: string; rect: Rect }[],
  page: { width: number; height: number; margin: number }
): CandidateSet {
  const x: Candidate[] = [];
  const y: Candidate[] = [];

  for (const { nid, rect } of others) {
    add(x, rect.x, 'edge', nid, rect.y, bottom(rect));
    add(x, right(rect), 'edge', nid, rect.y, bottom(rect));
    add(x, centerX(rect), 'center', nid, rect.y, bottom(rect));
    add(y, rect.y, 'edge', nid, rect.x, right(rect));
    add(y, bottom(rect), 'edge', nid, rect.x, right(rect));
    add(y, centerY(rect), 'center', nid, rect.x, right(rect));
  }

  // The page itself: margins and centre lines. These matter most on an empty
  // page, where there is nothing else to align to.
  add(x, page.margin, 'edge', 'page', 0, page.height);
  add(x, page.width - page.margin, 'edge', 'page', 0, page.height);
  add(x, page.width / 2, 'center', 'page', 0, page.height);
  add(y, page.margin, 'edge', 'page', 0, page.width);
  add(y, page.height - page.margin, 'edge', 'page', 0, page.width);
  add(y, page.height / 2, 'center', 'page', 0, page.width);

  x.sort((a, b) => a.at - b.at);
  y.sort((a, b) => a.at - b.at);
  return { x, y };
}

interface Probe {
  at: number;
  kind: LineKind;
}

function probes(rect: Rect, axis: Axis): Probe[] {
  return axis === 'x'
    ? [
        { at: rect.x, kind: 'edge' },
        { at: centerX(rect), kind: 'center' },
        { at: right(rect), kind: 'edge' },
      ]
    : [
        { at: rect.y, kind: 'edge' },
        { at: centerY(rect), kind: 'center' },
        { at: bottom(rect), kind: 'edge' },
      ];
}

function solveAxis(
  rect: Rect,
  axis: Axis,
  candidates: readonly Candidate[],
  tolerance: number
): { delta: number; guide: Guide } | null {
  let best: { delta: number; candidate: Candidate; kind: LineKind } | null = null;

  for (const probe of probes(rect, axis)) {
    for (const candidate of candidates) {
      const delta = candidate.at - probe.at;
      if (Math.abs(delta) > tolerance) continue;
      if (
        best === null ||
        Math.abs(delta) < Math.abs(best.delta) - 0.001 ||
        // Ties go to edge-over-centre: catching two edges together reads as
        // deliberate, catching a centre at the same distance reads as sloppy.
        (Math.abs(Math.abs(delta) - Math.abs(best.delta)) <= 0.001 &&
          probe.kind === 'edge' &&
          best.kind === 'center')
      ) {
        best = { delta, candidate, kind: probe.kind };
      }
    }
  }

  if (!best) return null;

  // Extend the guide across the moving selection as well as its owners, which
  // is what makes it read as an alignment rather than a stray line.
  const span = axis === 'x' ? [rect.y, bottom(rect)] : [rect.x, right(rect)];
  return {
    delta: best.delta,
    guide: {
      axis,
      at: best.candidate.at,
      from: Math.min(best.candidate.from, span[0]),
      to: Math.max(best.candidate.to, span[1]),
      owners: best.candidate.owners,
    },
  };
}

/**
 * Snap a moving rect to the nearest candidate on each axis independently.
 *
 * Returns the correction to apply, and the guides to draw. Axes are solved
 * separately so a box can catch a vertical line without being dragged
 * sideways by a horizontal one.
 */
export function snap(
  rect: Rect,
  candidates: CandidateSet,
  options: SnapOptions = {}
): SnapResult {
  if (options.enabled === false) return { dx: 0, dy: 0, guides: [] };

  const zoom = options.zoom && options.zoom > 0 ? options.zoom : 1;
  const tolerance = (options.threshold ?? DEFAULT_THRESHOLD) / zoom;

  const x = solveAxis(rect, 'x', candidates.x, tolerance);
  const y = solveAxis(rect, 'y', candidates.y, tolerance);

  return {
    dx: x?.delta ?? 0,
    dy: y?.delta ?? 0,
    guides: [x?.guide, y?.guide].filter(Boolean) as Guide[],
  };
}
