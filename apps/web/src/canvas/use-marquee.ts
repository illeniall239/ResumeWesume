/**
 * Rubber-band selection on empty paper.
 *
 * Separate from `use-drag` because it is a different gesture with different
 * rules: it starts on the page rather than an element, it moves nothing, and
 * it commits no ops. Folding the two together would mean every pointer event
 * asking which of two modes it was in.
 *
 * Like a drag, it paints its own rectangle rather than routing pointer moves
 * through React — the same reason, and it keeps the page from re-rendering
 * sixty times while the band is being pulled.
 */

'use client';

import { useCallback, useRef } from 'react';

import type { Rect } from '@/contracts/doc';

import { intersects, marquee } from './geometry';
import { useSelection } from './selection';
import { ptToPx, pxToPt } from './units';

interface Band {
  startX: number;
  startY: number;
  /** Page origin in client coordinates, so pointer positions become page ones. */
  originX: number;
  originY: number;
  additive: boolean;
  base: string[];
  node: HTMLElement | null;
}

export function useMarquee(
  elements: () => { nid: string; rect: Rect }[],
  /** Current zoom: pointer distances are screen pixels, rects are points. */
  zoom = 1
) {
  const band = useRef<Band | null>(null);

  const begin = useCallback((event: React.PointerEvent) => {
    // Only on bare paper: a press that lands on an element is a drag.
    if (event.target !== event.currentTarget) return;

    const page = event.currentTarget as HTMLElement;
    const box = page.getBoundingClientRect();
    page.setPointerCapture?.(event.pointerId);

    const node = document.createElement('div');
    node.className = 'marquee';
    page.appendChild(node);

    band.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: box.left,
      originY: box.top,
      additive: event.shiftKey,
      base: event.shiftKey ? useSelection.getState().selected : [],
      node,
    };
  }, []);

  const move = useCallback(
    (event: React.PointerEvent) => {
      const live = band.current;
      if (!live?.node) return;

      // Divided by zoom before converting: the pointer moves in screen pixels
      // and the document is in points, so a band drawn at 200% would otherwise
      // select twice the area it encloses.
      const scale = zoom > 0 ? zoom : 1;
      const rect = marquee(
        pxToPt((live.startX - live.originX) / scale),
        pxToPt((live.startY - live.originY) / scale),
        pxToPt((event.clientX - live.originX) / scale),
        pxToPt((event.clientY - live.originY) / scale)
      );

      live.node.style.left = `${ptToPx(rect.x)}px`;
      live.node.style.top = `${ptToPx(rect.y)}px`;
      live.node.style.width = `${ptToPx(rect.w)}px`;
      live.node.style.height = `${ptToPx(rect.h)}px`;

      // Touching, not enclosing: a band that demands full containment makes
      // selecting a full-width frame nearly impossible on a page where every
      // frame is full width.
      const hit = elements()
        .filter((element) => intersects(rect, element.rect))
        .map((element) => element.nid);
      useSelection.getState().selectMany([...live.base, ...hit]);
    },
    [elements, zoom]
  );

  const end = useCallback((event: React.PointerEvent) => {
    const live = band.current;
    band.current = null;
    if (!live) return;
    (event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
    live.node?.remove();
  }, []);

  return { begin, move, end, active: () => band.current !== null };
}
