/**
 * The zoom control.
 *
 * Zoom is view state, never document state: it must not reach the content
 * hash, bump a version or enter the undo stack. What is asserted here is the
 * ladder — because the number is not decoration. Every gesture divides screen
 * pixels by it to reach document points, and the snap solver divides its
 * threshold by it, so an off-ladder or zero value would make every drag on the
 * canvas subtly wrong.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { canZoom, DEFAULT_ZOOM, useView, ZOOM_STEPS } from '@/canvas/view';

beforeEach(() => useView.setState({ zoom: DEFAULT_ZOOM }));

describe('zooming', () => {
  it('starts at 100%', () => {
    expect(useView.getState().zoom).toBe(1);
  });

  it('steps up and back down to where it started', () => {
    useView.getState().zoomIn();
    expect(useView.getState().zoom).toBeGreaterThan(1);
    useView.getState().zoomOut();
    expect(useView.getState().zoom).toBe(1);
  });

  it('lands only on the steps', () => {
    for (let index = 0; index < 20; index += 1) useView.getState().zoomIn();
    expect(ZOOM_STEPS).toContain(useView.getState().zoom);
  });

  it('stops at the ends rather than running away', () => {
    for (let index = 0; index < 20; index += 1) useView.getState().zoomOut();
    expect(useView.getState().zoom).toBe(ZOOM_STEPS[0]);

    for (let index = 0; index < 20; index += 1) useView.getState().zoomIn();
    expect(useView.getState().zoom).toBe(ZOOM_STEPS[ZOOM_STEPS.length - 1]);
  });

  it('says when a button would do nothing', () => {
    expect(canZoom(ZOOM_STEPS[0], -1)).toBe(false);
    expect(canZoom(ZOOM_STEPS[0], 1)).toBe(true);
    expect(canZoom(ZOOM_STEPS[ZOOM_STEPS.length - 1], 1)).toBe(false);
  });

  it('never accepts a zoom that would break the pointer arithmetic', () => {
    // Every conversion divides by this, so a zero is a division by zero and a
    // negative silently inverts every drag.
    useView.getState().setZoom(0);
    expect(useView.getState().zoom).toBeGreaterThan(0);

    useView.getState().setZoom(-4);
    expect(useView.getState().zoom).toBeGreaterThan(0);

    useView.getState().setZoom(1000);
    expect(useView.getState().zoom).toBe(ZOOM_STEPS[ZOOM_STEPS.length - 1]);
  });

  it('steps sensibly from a value that is not on the ladder', () => {
    useView.setState({ zoom: 1.1 });
    useView.getState().zoomIn();
    expect(ZOOM_STEPS).toContain(useView.getState().zoom);
    expect(useView.getState().zoom).toBeGreaterThan(1.1);
  });

  it('resets to 100%', () => {
    useView.getState().zoomIn();
    useView.getState().reset();
    expect(useView.getState().zoom).toBe(1);
  });
});
