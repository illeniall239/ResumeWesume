/**
 * Alignment guides and snapping.
 *
 * A solver, so it is tested like one: a table of positions in, a correction
 * and a guide out. The zoom case is the one that decides whether this feels
 * like Canva or like glue — a threshold in document points is a different
 * distance under the pointer at every zoom level.
 */

import { describe, expect, it } from 'vitest';

import { buildCandidates, snap } from '@/canvas/snapping';

const PAGE = { width: 600, height: 800, margin: 30 };

const others = [
  { nid: 'a', rect: { x: 100, y: 100, w: 200, h: 50 } }, // x: 100/200/300, y: 100/125/150
  { nid: 'b', rect: { x: 400, y: 300, w: 100, h: 100 } }, // x: 400/450/500, y: 300/350/400
];

const candidates = buildCandidates(others, PAGE);
const moving = (x: number, y: number) => ({ x, y, w: 80, h: 40 });

describe('snap', () => {
  it('catches a left edge on a neighbour', () => {
    const result = snap(moving(104, 500), candidates);
    expect(result.dx).toBe(-4);
    expect(result.guides.some((g) => g.axis === 'x' && g.at === 100)).toBe(true);
  });

  it('leaves a box alone when nothing is near', () => {
    const result = snap(moving(250, 600), candidates);
    expect(result.dx).toBe(0);
    expect(result.dy).toBe(0);
    expect(result.guides).toEqual([]);
  });

  it('solves the axes independently', () => {
    // Catches x on a neighbour's edge and y on nothing.
    const result = snap(moving(297, 600), candidates);
    expect(result.dx).toBe(3);
    expect(result.dy).toBe(0);
  });

  it('snaps both axes at once when both are near', () => {
    const result = snap(moving(102, 148), candidates);
    expect(result.dx).toBe(-2);
    expect(result.dy).toBe(2);
    expect(result.guides).toHaveLength(2);
  });

  it('snaps a centre to a centre', () => {
    // Moving box is 80 wide, so its centre is at x+40. Target centre is 200.
    const result = snap(moving(158, 600), candidates);
    expect(result.dx).toBe(2);
    expect(result.guides[0].at).toBe(200);
  });

  it('catches the page centre on an otherwise empty area', () => {
    // Page centre is 300; the moving box's centre lands at 298.
    const result = snap(moving(258, 700), candidates);
    expect(result.dx).toBe(2);
  });

  it('catches the page margin', () => {
    const result = snap(moving(33, 700), candidates);
    expect(result.dx).toBe(-3);
    expect(result.guides[0].owners).toContain('page');
  });

  it('prefers an edge over a centre at the same distance', () => {
    // Element `a` has an edge at 300 and element `b` a centre at... construct
    // the tie directly: a probe 2pt from both an edge and a centre.
    const tied = buildCandidates(
      [
        { nid: 'edge', rect: { x: 200, y: 0, w: 100, h: 10 } }, // edge at 200
        { nid: 'ctr', rect: { x: 156, y: 0, w: 88, h: 10 } }, // centre at 200
      ],
      PAGE
    );
    const result = snap({ x: 198, y: 500, w: 80, h: 40 }, tied);
    expect(result.dx).toBe(2);
    // The edge wins the tie, so the guide names the element that owns it.
    expect(result.guides[0].owners).toContain('edge');
  });

  it('takes the nearer of two candidates', () => {
    const result = snap(moving(103, 500), candidates);
    expect(result.dx).toBe(-3); // 100 is 3 away; 200 is far
  });

  it('can be turned off entirely', () => {
    // A snap the user cannot defeat is a bug, so a held modifier disables it.
    const result = snap(moving(101, 101), candidates, { enabled: false });
    expect(result).toEqual({ dx: 0, dy: 0, guides: [] });
  });
});

describe('threshold and zoom', () => {
  it('respects the threshold', () => {
    expect(snap(moving(110, 500), candidates, { threshold: 6 }).dx).toBe(0);
    expect(snap(moving(110, 500), candidates, { threshold: 20 }).dx).toBe(-10);
  });

  it('scales the threshold by zoom so it feels the same at any scale', () => {
    // 6 screen px at 0.5 zoom is 12 document points, so a 10pt gap catches;
    // at 2x it is 3 points, so the same gap does not. Without this, snapping
    // is glue when zoomed out and useless when zoomed in.
    const far = moving(110, 500);
    expect(snap(far, candidates, { threshold: 6, zoom: 0.5 }).dx).toBe(-10);
    expect(snap(far, candidates, { threshold: 6, zoom: 2 }).dx).toBe(0);
  });
});

describe('guides', () => {
  it('extends the guide across the moving box and the element it aligns to', () => {
    // The extension is the informational content: it says "these two line up",
    // not merely "a line appeared".
    const result = snap({ x: 104, y: 400, w: 80, h: 40 }, candidates);
    const guide = result.guides.find((g) => g.axis === 'x');
    expect(guide).toBeDefined();
    expect(guide!.from).toBeLessThanOrEqual(100); // top of element `a`
    expect(guide!.to).toBeGreaterThanOrEqual(440); // bottom of the moving box
  });

  it('names every element sharing the line', () => {
    const stacked = buildCandidates(
      [
        { nid: 'a', rect: { x: 100, y: 100, w: 50, h: 20 } },
        { nid: 'b', rect: { x: 100, y: 300, w: 80, h: 20 } },
      ],
      PAGE
    );
    const guide = snap(moving(102, 600), stacked).guides.find((g) => g.axis === 'x');
    expect(guide!.owners).toEqual(expect.arrayContaining(['a', 'b']));
  });
});

describe('buildCandidates', () => {
  it('offers edges and a centre for every element', () => {
    const single = buildCandidates([{ nid: 'a', rect: { x: 10, y: 20, w: 100, h: 40 } }], PAGE);
    expect(single.x.map((c) => c.at)).toEqual(expect.arrayContaining([10, 60, 110]));
    expect(single.y.map((c) => c.at)).toEqual(expect.arrayContaining([20, 40, 60]));
  });

  it('offers the page margins and centre even with no elements', () => {
    const empty = buildCandidates([], PAGE);
    expect(empty.x.map((c) => c.at)).toEqual(expect.arrayContaining([30, 300, 570]));
    expect(empty.y.map((c) => c.at)).toEqual(expect.arrayContaining([30, 400, 770]));
  });

  it('merges elements that share a line into one candidate', () => {
    const aligned = buildCandidates(
      [
        { nid: 'a', rect: { x: 100, y: 0, w: 50, h: 10 } },
        { nid: 'b', rect: { x: 100, y: 50, w: 90, h: 10 } },
      ],
      PAGE
    );
    const line = aligned.x.find((c) => c.at === 100);
    expect(line!.owners).toEqual(['a', 'b']);
  });
});
