/**
 * Rectangle arithmetic for dragging and resizing.
 *
 * All of it pure, so the interaction layer above can be thin pointer plumbing.
 * The resize cases are the ones worth having: an edge that should stay put and
 * does not is the difference between grabbing a corner and shoving the box.
 */

import { describe, expect, it } from 'vitest';

import {
  MIN_SIZE,
  ROTATION_SNAP,
  angleTo,
  bounds,
  clampToPage,
  contains,
  intersects,
  isRotatable,
  marquee,
  normaliseAngle,
  resize,
  rotationFor,
  translate,
} from '@/canvas/geometry';

const box = { x: 100, y: 100, w: 200, h: 100 };

describe('basics', () => {
  it('translates', () => {
    expect(translate(box, 10, -20)).toEqual({ x: 110, y: 80, w: 200, h: 100 });
  });

  it('bounds a group', () => {
    expect(bounds([box, { x: 50, y: 250, w: 100, h: 50 }])).toEqual({
      x: 50,
      y: 100,
      w: 250,
      h: 200,
    });
  });

  it('has no bounds for nothing', () => {
    expect(bounds([])).toBeNull();
  });

  it('hit-tests including the edges', () => {
    expect(contains(box, 150, 150)).toBe(true);
    expect(contains(box, 100, 100)).toBe(true);
    expect(contains(box, 301, 150)).toBe(false);
  });

  it('detects overlap for the marquee', () => {
    expect(intersects(box, { x: 250, y: 150, w: 100, h: 100 })).toBe(true);
    expect(intersects(box, { x: 400, y: 150, w: 100, h: 100 })).toBe(false);
  });

  it('builds a marquee dragged in any direction', () => {
    const expected = { x: 10, y: 20, w: 90, h: 80 };
    expect(marquee(10, 20, 100, 100)).toEqual(expected);
    expect(marquee(100, 100, 10, 20)).toEqual(expected);
  });

  it('keeps a rect inside the page without resizing it', () => {
    const page = { width: 595, height: 842 };
    expect(clampToPage({ x: -50, y: -50, w: 100, h: 100 }, page)).toMatchObject({
      x: 0,
      y: 0,
      w: 100,
      h: 100,
    });
    expect(clampToPage({ x: 900, y: 900, w: 100, h: 100 }, page)).toMatchObject({
      x: 495,
      y: 742,
    });
  });
});

describe('resize', () => {
  it('drags the east edge without moving the west one', () => {
    expect(resize(box, 'e', 50, 0)).toEqual({ x: 100, y: 100, w: 250, h: 100 });
  });

  it('drags the west edge and keeps the east one still', () => {
    // The opposite edge staying put is what makes it feel like grabbing this
    // edge rather than moving the whole box.
    const result = resize(box, 'w', 50, 0);
    expect(result).toEqual({ x: 150, y: 100, w: 150, h: 100 });
    expect(result.x + result.w).toBe(box.x + box.w);
  });

  it('drags the north edge and keeps the south one still', () => {
    const result = resize(box, 'n', 0, 40);
    expect(result).toEqual({ x: 100, y: 140, w: 200, h: 60 });
    expect(result.y + result.h).toBe(box.y + box.h);
  });

  it('resizes both axes from a corner', () => {
    expect(resize(box, 'se', 50, 25)).toEqual({ x: 100, y: 100, w: 250, h: 125 });
  });

  it('anchors the opposite corner when dragging north-west', () => {
    const result = resize(box, 'nw', 20, 10);
    expect(result.x + result.w).toBe(box.x + box.w);
    expect(result.y + result.h).toBe(box.y + box.h);
  });

  it('clamps rather than inverting when dragged past the far edge', () => {
    // A flipped rect has negative dimensions every consumer downstream would
    // have to defend against, for a gesture nobody means to make.
    const result = resize(box, 'e', -500, 0);
    expect(result.w).toBe(MIN_SIZE);
    expect(result.x).toBe(box.x);
  });

  it('clamps a west drag without letting the box run away', () => {
    const result = resize(box, 'w', 500, 0);
    expect(result.w).toBe(MIN_SIZE);
    expect(result.x + result.w).toBe(box.x + box.w);
  });

  it('grows from the centre when asked', () => {
    const result = resize(box, 'e', 25, 0, { fromCenter: true });
    expect(result.w).toBe(250);
    // Centre held: 25pt added to each side.
    expect(result.x + result.w / 2).toBe(box.x + box.w / 2);
  });

  it('keeps the aspect ratio', () => {
    const result = resize(box, 'se', 100, 0, { aspect: true });
    expect(result.w / result.h).toBeCloseTo(box.w / box.h, 5);
  });

  it('keeps the aspect ratio anchored at the far corner', () => {
    const result = resize(box, 'nw', -100, 0, { aspect: true });
    expect(result.w / result.h).toBeCloseTo(box.w / box.h, 5);
    expect(result.x + result.w).toBeCloseTo(box.x + box.w, 5);
    expect(result.y + result.h).toBeCloseTo(box.y + box.h, 5);
  });

  it('leaves the untouched axis alone', () => {
    // Dragging a side handle vertically must not change the height.
    expect(resize(box, 'e', 40, 999).h).toBe(box.h);
    expect(resize(box, 's', 999, 40).w).toBe(box.w);
  });

  it('respects a custom minimum', () => {
    expect(resize(box, 'e', -500, 0, { min: 40 }).w).toBe(40);
  });
});

describe('rotation', () => {
  const box = { x: 100, y: 100, w: 200, h: 100 }; // centre (200, 150)

  it('allows images and shapes but not frames', () => {
    // A rotated contentEditable breaks caret positioning in every browser, so
    // text frames stay upright until that is solved properly.
    expect(isRotatable('img_aaaaa')).toBe(true);
    expect(isRotatable('shp_aaaaa')).toBe(true);
    expect(isRotatable('frm_aaaaa')).toBe(false);
    expect(isRotatable('txb_aaaaa')).toBe(false);
  });

  it('measures zero straight up, like the handle’s resting position', () => {
    expect(normaliseAngle(angleTo(box, 200, 0))).toBeCloseTo(0, 4);
  });

  it('increases clockwise, like CSS rotate()', () => {
    // Getting this backwards makes the box spin against the pointer.
    expect(normaliseAngle(angleTo(box, 400, 150))).toBeCloseTo(90, 4);
    expect(normaliseAngle(angleTo(box, 200, 400))).toBeCloseTo(180, 4);
  });

  it('turns with the pointer rather than snapping the handle to it', () => {
    // Grabbing a handle must not itself move anything: a grab at 90 degrees
    // that has not moved yields no rotation at all.
    expect(rotationFor(0, box, { x: 400, y: 150 }, { x: 400, y: 150 })).toBeCloseTo(0, 4);
  });

  it('adds the pointer’s travel to the existing angle', () => {
    const result = rotationFor(30, box, { x: 200, y: 0 }, { x: 400, y: 150 });
    expect(result).toBeCloseTo(120, 4);
  });

  it('wraps rather than running past a full turn', () => {
    expect(rotationFor(350, box, { x: 200, y: 0 }, { x: 400, y: 150 })).toBeCloseTo(80, 4);
  });

  it('snaps to 15 degrees when asked', () => {
    const result = rotationFor(0, box, { x: 200, y: 0 }, { x: 260, y: 10 }, true);
    expect(result % ROTATION_SNAP).toBeCloseTo(0, 6);
  });

  it('normalises negatives into a positive turn', () => {
    expect(normaliseAngle(-90)).toBe(270);
    expect(normaliseAngle(450)).toBe(90);
  });
});
