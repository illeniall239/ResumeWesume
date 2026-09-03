/**
 * How the canvas is being looked at.
 *
 * A separate store from the document, for the same reason selection is: zoom is
 * a property of *this* viewer at *this* moment, not of the resume. It must
 * never reach the content hash, never bump a version and never enter the undo
 * stack — nobody wants Ctrl+Z to change the magnification.
 *
 * The number is load-bearing beyond appearance. Every gesture converts screen
 * pixels into document points, so a drag divides its deltas by it, and the snap
 * solver divides its threshold by it — a fixed 6pt tolerance is glue at 50% and
 * useless at 200%. That arithmetic was written from the start; this is the
 * control that finally makes it mean something.
 */

'use client';

import { create } from 'zustand';

/**
 * The steps, not a continuous range.
 *
 * A ladder lands on round numbers people recognise and makes the buttons
 * predictable; free-running percentages give you 93% and no way back to 100.
 */
export const ZOOM_STEPS = [0.4, 0.5, 0.75, 1, 1.25, 1.5, 2] as const;

export const DEFAULT_ZOOM = 1;

export interface ViewState {
  zoom: number;
  zoomIn: () => void;
  zoomOut: () => void;
  setZoom: (zoom: number) => void;
  reset: () => void;
}

/** The index of the step at or nearest below `zoom`. */
function stepIndex(zoom: number): number {
  const exact = ZOOM_STEPS.findIndex((step) => Math.abs(step - zoom) < 0.001);
  if (exact >= 0) return exact;
  // An off-ladder value (set directly) snaps to the nearest step, so the next
  // button press still goes somewhere sensible.
  let nearest = 0;
  for (let index = 1; index < ZOOM_STEPS.length; index += 1) {
    if (Math.abs(ZOOM_STEPS[index] - zoom) < Math.abs(ZOOM_STEPS[nearest] - zoom)) {
      nearest = index;
    }
  }
  return nearest;
}

export const useView = create<ViewState>((set, get) => ({
  zoom: DEFAULT_ZOOM,

  zoomIn() {
    const next = Math.min(stepIndex(get().zoom) + 1, ZOOM_STEPS.length - 1);
    set({ zoom: ZOOM_STEPS[next] });
  },

  zoomOut() {
    const next = Math.max(stepIndex(get().zoom) - 1, 0);
    set({ zoom: ZOOM_STEPS[next] });
  },

  setZoom(zoom) {
    // Clamped rather than trusted: a stray value would make every pointer
    // conversion wrong, and a zero would divide by it.
    const first = ZOOM_STEPS[0];
    const last = ZOOM_STEPS[ZOOM_STEPS.length - 1];
    set({ zoom: Math.min(Math.max(zoom, first), last) });
  },

  reset() {
    set({ zoom: DEFAULT_ZOOM });
  },
}));

/**
 * The largest step at which a sheet of `sheetPx` fits inside `panePx`.
 *
 * Used once, when the studio opens on a pane narrower than a sheet. An A4 page
 * is about 794px and a phone is 390, so opening at 1.0 there shows less than
 * half the résumé -- and the first thing the document is supposed to say is
 * what it is. Returns the smallest step rather than a free ratio when even that
 * overflows: the ladder exists so the zoom control stays predictable, and a
 * fitted 0.43 would strand the buttons off it.
 */
export function fitZoom(panePx: number, sheetPx: number): number {
  if (panePx <= 0 || sheetPx <= 0) return DEFAULT_ZOOM;
  const ratio = panePx / sheetPx;
  const fitting = ZOOM_STEPS.filter((step) => step <= ratio);
  return fitting.length ? fitting[fitting.length - 1] : ZOOM_STEPS[0];
}

/** Whether another press in this direction would do anything. */
export function canZoom(zoom: number, direction: -1 | 1): boolean {
  const index = stepIndex(zoom);
  return direction > 0 ? index < ZOOM_STEPS.length - 1 : index > 0;
}
