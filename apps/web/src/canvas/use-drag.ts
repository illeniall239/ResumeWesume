/**
 * Turning pointer events into one op.
 *
 * The rule that shapes this file: **a drag never re-renders React.** Sixty
 * pointer events a second through a store would re-render every element on the
 * page sixty times, and the sixtieth frame would be laid out against the
 * fifty-ninth. So a live gesture writes a CSS transform straight onto the
 * moving nodes, and only `pointerup` produces a `set_geometry` op.
 *
 * Everything numeric is delegated to `geometry.ts` and `snapping.ts`, which is
 * why this stays plumbing: pointer capture, a rAF throttle, and the arithmetic
 * that converts screen pixels back into document points.
 */

'use client';

import { useCallback, useRef } from 'react';

import type { DocOp, Rect } from '@/contracts/doc';

import {
  clampToPage,
  resize,
  rotationFor,
  translate,
  type Handle,
} from './geometry';
import { useSelection } from './selection';
import { buildCandidates, snap, type CandidateSet } from './snapping';
import { pxToPt } from './units';

export interface DragTarget {
  nid: string;
  rect: Rect;
  rotation?: number;
  /** Already hand-placed, so the gesture need not say so again. */
  pinned?: boolean;
}

export interface DragContext {
  /** Every element on the page, for building snap candidates. */
  siblings: DragTarget[];
  page: { width: number; height: number; margin: number };
  zoom: number;
}

interface Session {
  handle: Handle | 'move' | 'rotate';
  startX: number;
  startY: number;
  /**
   * The page's top-left in client coordinates.
   *
   * Rotation needs it: an element's centre is in *page* points while a pointer
   * is in *client* pixels, and measuring an angle between the two origins
   * yields a number that moves in roughly the right direction while being
   * wrong -- a quarter turn of the pointer produced 8 degrees of rotation.
   * Translation and resizing only ever use pointer *deltas*, where the origin
   * cancels out, which is why this was not needed before.
   */
  originX: number;
  originY: number;
  /** The page the gesture began on, so a drop elsewhere is a move. */
  pageNid: string | null;
  originals: Map<string, Rect>;
  /** Angles at gesture start, so a rotation is relative to where it began. */
  angles: Map<string, number>;
  /** Which targets the reflow already knows to leave alone. */
  pinned: Set<string>;
  candidates: CandidateSet;
  latest: Map<string, Rect>;
  turned: Map<string, number>;
  /**
   * The inline styles React had written before the gesture started.
   *
   * A gesture paints straight onto the node, behind React's back. Blanking
   * those properties afterwards does not hand them back -- React diffs against
   * its own previous props, not against the DOM, so a value it never changed
   * is a value it never rewrites. Blanking `width` on a frame therefore left
   * it permanently auto-width: a plain click on a job made its text reflow
   * wider than the page and spill past the margin. Restoring exactly what was
   * there is kind-agnostic (a shape is sized with `height`, a frame with
   * `minHeight`, a rotated element carries a `transform`) and leaves React's
   * next diff free to overwrite it.
   */
  styles: Map<string, InlineStyles>;
}

/** The live rect of an element, for the overlay to follow mid-drag. */
export type RectReader = (nid: string) => Rect | undefined;

export function useDrag(
  context: () => DragContext,
  commit: (ops: DocOp[]) => void
) {
  const session = useRef<Session | null>(null);

  const begin = useCallback(
    (
      event: React.PointerEvent,
      targets: DragTarget[],
      handle: Handle | 'move' | 'rotate'
    ) => {
      if (!targets.length) return;
      event.preventDefault();
      event.stopPropagation();
      (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);

      const { siblings, page } = context();
      const moving = new Set(targets.map((t) => t.nid));
      const sheet = (event.currentTarget as HTMLElement).closest<HTMLElement>('.canvas-page');
      const origin = sheet?.getBoundingClientRect();

      session.current = {
        handle,
        startX: event.clientX,
        startY: event.clientY,
        originX: origin?.left ?? 0,
        originY: origin?.top ?? 0,
        pageNid: sheet?.dataset.page ?? null,
        styles: new Map(
          targets.map((t) => [t.nid, inlineStyles(t.nid)] as const)
        ),
        originals: new Map(targets.map((t) => [t.nid, { ...t.rect }])),
        latest: new Map(targets.map((t) => [t.nid, { ...t.rect }])),
        angles: new Map(targets.map((t) => [t.nid, t.rotation ?? 0])),
        pinned: new Set(targets.filter((t) => t.pinned).map((t) => t.nid)),
        turned: new Map(targets.map((t) => [t.nid, t.rotation ?? 0])),
        // Built once: candidates cannot change during a gesture, and
        // rebuilding them per pointer event is the difference between a solver
        // and a stutter. An element never snaps to itself.
        candidates: buildCandidates(
          siblings.filter((s) => !moving.has(s.nid)),
          page
        ),
      };
      useSelection.getState().beginDrag();
    },
    [context]
  );

  const move = useCallback(
    (event: React.PointerEvent) => {
      const live = session.current;
      if (!live) return;

      const { page, zoom } = context();
      const dx = pxToPt((event.clientX - live.startX) / zoom);
      const dy = pxToPt((event.clientY - live.startY) / zoom);

      if (live.handle === 'rotate') {
        // Rotation ignores snapping entirely: alignment guides describe edges,
        // and a rotated box's edges are no longer the ones they would match.
        // Shift snaps to 15 degrees instead.
        for (const [nid, original] of live.originals) {
          const next = rotationFor(
            live.angles.get(nid) ?? 0,
            original,
            {
              x: pxToPt((live.startX - live.originX) / zoom),
              y: pxToPt((live.startY - live.originY) / zoom),
            },
            {
              x: pxToPt((event.clientX - live.originX) / zoom),
              y: pxToPt((event.clientY - live.originY) / zoom),
            },
            event.shiftKey
          );
          live.turned.set(nid, next);
          paintRotation(nid, next);
        }
        return;
      }

      // Solve the snap against the primary target, then apply the same
      // correction to everything else so a multi-selection moves as one shape
      // rather than each box catching a different line.
      const [primaryNid] = live.originals.keys();
      const primary = live.originals.get(primaryNid)!;
      const proposed =
        live.handle === 'move'
          ? translate(primary, dx, dy)
          : resize(primary, live.handle, dx, dy, {
              aspect: event.shiftKey,
              fromCenter: event.altKey,
            });

      const solved = snap(proposed, live.candidates, {
        zoom,
        // Alt is the escape hatch. A snap that cannot be defeated is a bug.
        enabled: !event.altKey,
      });
      useSelection.getState().setGuides(solved.guides);

      // Clamping holds an element on its own sheet, which is right until the
      // pointer is over a different one -- then it is the thing stopping
      // content being dragged from page one to page two at all. While the
      // pointer is away, the rect runs free and is clamped on drop against
      // whichever page it landed on.
      const over = pageUnder(event.clientX, event.clientY);
      const leaving = over !== null && over.nid !== live.pageNid;

      for (const [nid, original] of live.originals) {
        const moved = translate(original, dx + solved.dx, dy + solved.dy);
        const next =
          live.handle === 'move'
            ? leaving
              ? moved
              : clampToPage(moved, page)
            : resize(original, live.handle, dx + solved.dx, dy + solved.dy, {
                aspect: event.shiftKey,
                fromCenter: event.altKey,
              });
        live.latest.set(nid, next);
        paint(nid, original, next);
      }
    },
    [context]
  );

  const end = useCallback(
    (event: React.PointerEvent) => {
      const live = session.current;
      session.current = null;
      useSelection.getState().endDrag();
      if (!live) return;
      (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);

      // Where it was let go. A gesture that ends over a different sheet is a
      // move between pages, not just a new position: geometry is page-relative,
      // so the same rect means somewhere else once the parent changes.
      const landing =
        live.handle === 'move' ? pageUnder(event.clientX, event.clientY) : null;
      const crossed = landing !== null && landing.nid !== live.pageNid;
      const { zoom, page } = context();
      // The source page's origin less the destination's, in points: what to add
      // to a rect measured against the old sheet to measure it against the new.
      const shift = crossed
        ? {
            x: pxToPt((live.originX - landing.rect.left) / zoom),
            y: pxToPt((live.originY - landing.rect.top) / zoom),
          }
        : { x: 0, y: 0 };

      const ops: DocOp[] = [];
      for (const [nid, landed] of live.latest) {
        const next = crossed
          ? clampToPage(
              { ...landed, x: landed.x + shift.x, y: landed.y + shift.y },
              page
            )
          : landed;
        const original = live.originals.get(nid)!;
        // Restored for every element the gesture touched, not only the ones
        // that moved: a drag that ends where it began still wrote inline
        // styles, and leaving those behind means React's width silently stops
        // being the one on screen.
        clearPaint(nid, live.styles.get(nid));

        if (live.handle === 'rotate') {
          const turned = live.turned.get(nid) ?? 0;
          if (Math.abs(turned - (live.angles.get(nid) ?? 0)) < 0.01) continue;
          ops.push({ op: 'set_geometry', nid, rotation: turned } as DocOp);
          continue;
        }

        if (crossed) {
          // Parent first, then position: the geometry that follows is measured
          // against the page it has just landed on.
          ops.push({ op: 'move_node', nid, parent: landing.nid, index: -1 } as DocOp);
        } else if (same(original, next)) {
          continue;
        }
        // Moving something by hand takes it out of the reflow's care. Without
        // this the pass owns every frame, so a job dragged onto page two was
        // stacked back into the column on the next load -- the drag surviving
        // the round trip and then being undone by a correction.
        if (live.handle === 'move' && !live.pinned.has(nid)) {
          live.pinned.add(nid);
          ops.push({
            op: 'set_element_style',
            nid,
            patch: { pinned: true },
          } as DocOp);
        }
        ops.push({
          op: 'set_geometry',
          nid,
          x: next.x,
          y: next.y,
          w: next.w,
          h: next.h,
          // What the gesture started from, so a stale drag is caught rather
          // than silently overwriting somebody else's move.
          expect: { x: original.x, y: original.y, w: original.w, h: original.h },
        } as DocOp);
      }
      if (ops.length) commit(ops);
    },
    [commit]
  );

  return { begin, move, end };
}

interface InlineStyles {
  transform: string;
  width: string;
  height: string;
  zIndex: string;
}

/**
 * The page under the pointer, if any.
 *
 * Read from the DOM rather than plumbed through React because a page knows
 * nothing about its neighbours -- each `PageView` renders one sheet -- and a
 * drag that crosses pages needs all of them. The sheets are on screen; asking
 * them where they are is cheaper than lifting the whole canvas into state.
 */
function pageUnder(clientX: number, clientY: number): { nid: string; rect: DOMRect } | null {
  for (const node of document.querySelectorAll<HTMLElement>('.canvas-page')) {
    const rect = node.getBoundingClientRect();
    if (
      clientX >= rect.left &&
      clientX <= rect.right &&
      clientY >= rect.top &&
      clientY <= rect.bottom &&
      node.dataset.page
    ) {
      return { nid: node.dataset.page, rect };
    }
  }
  return null;
}

/** Move the node under the pointer without going through React. */
function paint(nid: string, from: Rect, to: Rect): void {
  const node = document.querySelector<HTMLElement>(`[data-element="${nid}"]`);
  if (!node) return;
  const PT = 96 / 72;
  node.style.transform = `translate(${(to.x - from.x) * PT}px, ${(to.y - from.y) * PT}px)`;
  // Above everything, including the next sheet down. Pages are siblings and
  // later ones paint on top, so without this an element dragged towards page
  // two slides *underneath* it and the user loses sight of what they are
  // holding. `.canvas-page` sets no z-index, so it creates no stacking context
  // and this competes at the level the pages themselves do.
  node.style.zIndex = '9999';
  if (to.w !== from.w) node.style.width = `${to.w * PT}px`;
  if (to.h !== from.h && node.dataset.autogrow !== 'height') {
    node.style.height = `${to.h * PT}px`;
  }
}

function paintRotation(nid: string, degrees: number): void {
  const node = document.querySelector<HTMLElement>(`[data-element="${nid}"]`);
  if (node) node.style.transform = `rotate(${degrees}deg)`;
}

/** What React had written inline, before the gesture painted over it. */
function inlineStyles(nid: string): InlineStyles {
  const node = document.querySelector<HTMLElement>(`[data-element="${nid}"]`);
  const style = node?.style;
  return {
    transform: style?.transform ?? '',
    width: style?.width ?? '',
    height: style?.height ?? '',
    zIndex: style?.zIndex ?? '',
  };
}

function clearPaint(nid: string, before: InlineStyles | undefined): void {
  const node = document.querySelector<HTMLElement>(`[data-element="${nid}"]`);
  if (!node) return;
  // Put back what React believes it wrote, so it owns these again the moment
  // the op lands. Not `''` -- see `Session.styles`.
  node.style.transform = before?.transform ?? '';
  node.style.width = before?.width ?? '';
  node.style.height = before?.height ?? '';
  node.style.zIndex = before?.zIndex ?? '';
}

function same(a: Rect, b: Rect): boolean {
  return (
    Math.abs(a.x - b.x) < 0.01 &&
    Math.abs(a.y - b.y) < 0.01 &&
    Math.abs(a.w - b.w) < 0.01 &&
    Math.abs(a.h - b.h) < 0.01
  );
}
