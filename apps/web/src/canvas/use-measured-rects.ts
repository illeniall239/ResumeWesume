/**
 * Where the selected elements actually are, as opposed to where the document
 * says they are.
 *
 * For a frame with `autogrow: "height"` the stored height is **advisory** --
 * the server cannot measure text, so it writes an estimate and the browser
 * decides the real value. Drawing selection chrome from the stored rect
 * therefore produces a box that does not match the thing it is selecting: a
 * one-line text box stores 48pt and renders at 14, so the outline stands three
 * times too tall and reads as the element having expanded.
 *
 * Measuring is cheap here because it happens once per selection change, not
 * per frame of a drag -- a live gesture paints transforms directly and never
 * comes through React at all.
 */

'use client';

import { useLayoutEffect, useState } from 'react';

import type { Rect } from '@/contracts/doc';

import { pxToPt } from './units';

/** Read an element's on-screen box in page points. */
export function measureRect(nid: string, fallback: Rect): Rect {
  const node = document.querySelector<HTMLElement>(`[data-element="${nid}"]`);
  const page = node?.closest('.canvas-page');
  if (!node || !page) return fallback;

  const box = node.getBoundingClientRect();
  const origin = page.getBoundingClientRect();
  if (!box.width && !box.height) return fallback;

  return {
    // x/y/w come from the stored rect, which positions the element and is
    // therefore always right. Only the height is in question.
    x: fallback.x,
    y: fallback.y,
    w: fallback.w,
    h: pxToPt(box.height),
  };
}

/**
 * Measured rects for `selected`, recomputed whenever the selection or the
 * document changes.
 *
 * `useLayoutEffect` rather than `useEffect`: measuring after paint would show
 * one frame of the wrong-sized outline every time something is selected.
 */
export function useMeasuredRects(
  selected: readonly { nid: string; rect: Rect }[],
  signature: string
): Rect[] {
  const [rects, setRects] = useState<Rect[]>(() =>
    selected.map((element) => element.rect)
  );

  useLayoutEffect(() => {
    setRects(selected.map((element) => measureRect(element.nid, element.rect)));
    // `signature` stands in for the selection and document identity; the
    // element objects themselves are new on every render and would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  return rects.length === selected.length
    ? rects
    : selected.map((element) => element.rect);
}
