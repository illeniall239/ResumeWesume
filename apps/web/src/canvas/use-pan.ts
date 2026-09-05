/**
 * Dragging the canvas to move around it.
 *
 * The plane scrolls, which is already panning of a sort — but reaching for a
 * scrollbar to move between versions of a résumé is the wrong gesture for a
 * surface you are meant to move *around*. Every canvas in every tool answers
 * space-drag or middle-drag, and a canvas that does not feels stuck.
 *
 * Deliberately narrow about what starts a pan, because everything else on this
 * surface already claims the pointer: a board is a live editor with text you
 * select, boxes you drag, and a marquee. So a pan begins only on
 *
 *   - the middle button, which nothing else here uses, or
 *   - the left button with space held, which is the convention, or
 *   - the left button on the background *between* boards, where there is
 *     nothing else it could mean.
 *
 * It never begins on a board. Panning by dragging the sheet would fight the
 * text selection and the element drag, and losing those to gain a gesture the
 * scrollbar already offers is a bad trade.
 */

'use client';

import { useEffect, useRef, useState } from 'react';

/** Which mouse button the event carries. 1 is the middle one. */
const MIDDLE = 1;

export function usePan(pane: React.RefObject<HTMLElement | null>) {
  /** Space is held, so a left-drag anywhere means pan. */
  const [ready, setReady] = useState(false);
  /** A pan is actually in progress. */
  const [panning, setPanning] = useState(false);
  const from = useRef<{ x: number; y: number; left: number; top: number } | null>(null);

  // Space, on the window rather than the pane: the pane is not focusable, and
  // requiring a click into it first would make the shortcut fail exactly when
  // somebody has just arrived at the page.
  useEffect(() => {
    function down(event: KeyboardEvent) {
      if (event.code !== 'Space') return;
      const target = event.target as HTMLElement | null;
      // Never while typing. Space is a space there, and stealing it would put
      // the caret in a résumé that refuses to accept words.
      if (target?.isContentEditable || target instanceof HTMLTextAreaElement) return;
      if (target instanceof HTMLInputElement) return;
      // Stops the page scrolling a screen at a time underneath the drag.
      event.preventDefault();
      setReady(true);
    }
    function up(event: KeyboardEvent) {
      if (event.code === 'Space') setReady(false);
    }
    // Released state is also restored on blur: a space held while the window
    // loses focus never sends its keyup, and the cursor would stay a grab hand
    // over a canvas that no longer pans.
    function blur() {
      setReady(false);
    }
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    window.addEventListener('blur', blur);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
      window.removeEventListener('blur', blur);
    };
  }, []);

  useEffect(() => {
    const node = pane.current;
    if (!node) return;

    function start(event: PointerEvent) {
      const node = pane.current;
      if (!node) return;
      const target = event.target as HTMLElement | null;

      const middle = event.button === MIDDLE;
      // The background between boards: the pane itself, or the plane laid out
      // inside it. Anything within a board belongs to the board.
      const empty =
        event.button === 0 &&
        !ready &&
        (target === node || target?.classList.contains('plane'));

      if (!middle && !(ready && event.button === 0) && !empty) return;

      event.preventDefault();
      node.setPointerCapture(event.pointerId);
      from.current = {
        x: event.clientX,
        y: event.clientY,
        left: node.scrollLeft,
        top: node.scrollTop,
      };
      setPanning(true);
    }

    function move(event: PointerEvent) {
      const node = pane.current;
      if (!node || !from.current) return;
      // Away from the start point, not from the last event: accumulating
      // deltas drifts, and clamping at an edge would eat the movement that
      // should bring it back.
      node.scrollLeft = from.current.left - (event.clientX - from.current.x);
      node.scrollTop = from.current.top - (event.clientY - from.current.y);
    }

    function stop(event: PointerEvent) {
      const node = pane.current;
      if (!from.current) return;
      from.current = null;
      setPanning(false);
      if (node?.hasPointerCapture(event.pointerId)) {
        node.releasePointerCapture(event.pointerId);
      }
    }

    node.addEventListener('pointerdown', start);
    node.addEventListener('pointermove', move);
    node.addEventListener('pointerup', stop);
    node.addEventListener('pointercancel', stop);
    return () => {
      node.removeEventListener('pointerdown', start);
      node.removeEventListener('pointermove', move);
      node.removeEventListener('pointerup', stop);
      node.removeEventListener('pointercancel', stop);
    };
  }, [pane, ready]);

  // `ready` is the offer, `panning` is the act. The cursor says which.
  return { ready, panning };
}
