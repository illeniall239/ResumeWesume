/**
 * Reflow, when the page has more than one column.
 *
 * This is the pass that decides whether a multi-column layout is possible at
 * all. It used to run a single `y` cursor over every frame in document order
 * and rewrite it, ignoring `x` entirely — so a rail *placed* beside the main
 * column was dealt back underneath it the first time the document was opened
 * and measured.
 *
 * That is why the two-column template that used to exist was removed rather
 * than fixed: `globals.css` still carries the note that "the canvas gives a
 * section heading and its entries separate frames, so a grid spanning both had
 * nothing to span — the gallery card advertised a layout the editor could not
 * produce." The editor could not hold it because of this function.
 *
 * The rule now: a frame as wide as the content area spans the sheet and
 * nothing sits beside it; anything narrower belongs to the column its left
 * edge names, and each column fills and paginates on its own.
 */

import { describe, expect, it } from 'vitest';

import { reflow, type MeasuredFrame } from '@/canvas/reflow';

const TOP = 100;
const CONTENT_HEIGHT = 800;
const CONTENT_WIDTH = 400;
const GEOMETRY = { contentHeight: CONTENT_HEIGHT, top: TOP, contentWidth: CONTENT_WIDTH };

/** Left rail and main column, as `autolayout` places them. */
const RAIL_X = 100;
const RAIL_W = 140;
const MAIN_X = 260;
const MAIN_W = 240;

function frame(
  nid: string,
  x: number,
  w: number,
  height: number,
  page = 'pag_1'
): MeasuredFrame {
  return { nid, page, rect: { x, y: TOP, w, h: height }, height };
}

const at = (result: ReturnType<typeof reflow>, nid: string) =>
  result.placements.find((place) => place.nid === nid)!;

describe('two columns', () => {
  it('fills each column independently', () => {
    // The whole point. Stacked, `main2` would sit below the rail's total
    // height; in columns it follows `main1` and nothing else.
    const result = reflow(
      [
        frame('rail1', RAIL_X, RAIL_W, 120),
        frame('rail2', RAIL_X, RAIL_W, 90),
        frame('main1', MAIN_X, MAIN_W, 200),
        frame('main2', MAIN_X, MAIN_W, 160),
      ],
      GEOMETRY,
      ['pag_1']
    );

    expect(at(result, 'rail1').y).toBe(TOP);
    expect(at(result, 'rail2').y).toBe(TOP + 120);
    expect(at(result, 'main1').y).toBe(TOP);
    expect(at(result, 'main2').y).toBe(TOP + 200);
  });

  it('lets a short rail sit beside a long column', () => {
    // A rail of skills is a few centimetres; a column of jobs is most of the
    // page. Sharing one cursor is what put the rail below the jobs.
    const result = reflow(
      [frame('rail', RAIL_X, RAIL_W, 80), frame('main', MAIN_X, MAIN_W, 700)],
      GEOMETRY,
      ['pag_1']
    );

    expect(at(result, 'rail').y).toBe(TOP);
    expect(at(result, 'main').y).toBe(TOP);
    expect(result.pages).toBe(1);
  });

  it('paginates one column without disturbing the other', () => {
    const result = reflow(
      [
        frame('rail', RAIL_X, RAIL_W, 100),
        frame('main1', MAIN_X, MAIN_W, 600),
        frame('main2', MAIN_X, MAIN_W, 400),
      ],
      GEOMETRY,
      ['pag_1']
    );

    expect(at(result, 'rail').page).toBe(0);
    expect(at(result, 'main1').page).toBe(0);
    // 600 + 400 does not fit in 800, so the second goes over.
    expect(at(result, 'main2').page).toBe(1);
    expect(at(result, 'main2').y).toBe(TOP);
    expect(result.pages).toBe(2);
  });
});

describe('a frame that spans the sheet', () => {
  it('has nothing beside it, and everything after it below it', () => {
    // The header, in a sidebar layout: full width at the top, with both
    // columns starting underneath.
    const result = reflow(
      [
        frame('header', 100, CONTENT_WIDTH, 90),
        frame('rail', RAIL_X, RAIL_W, 60),
        frame('main', MAIN_X, MAIN_W, 300),
      ],
      GEOMETRY,
      ['pag_1']
    );

    expect(at(result, 'header').y).toBe(TOP);
    expect(at(result, 'rail').y).toBe(TOP + 90);
    expect(at(result, 'main').y).toBe(TOP + 90);
  });

  it('clears the tallest column before it, not the last one placed', () => {
    // A footer under a sidebar: the rail is short and the main column is long,
    // and the spanner has to clear both or it lands on top of the jobs.
    const result = reflow(
      [
        frame('rail', RAIL_X, RAIL_W, 100),
        frame('main', MAIN_X, MAIN_W, 300),
        frame('footer', 100, CONTENT_WIDTH, 40),
      ],
      GEOMETRY,
      ['pag_1']
    );

    expect(at(result, 'footer').y).toBe(TOP + 300);
  });

  it('resumes both columns below itself', () => {
    const result = reflow(
      [
        frame('rail1', RAIL_X, RAIL_W, 100),
        frame('main1', MAIN_X, MAIN_W, 300),
        frame('band', 100, CONTENT_WIDTH, 40),
        frame('rail2', RAIL_X, RAIL_W, 50),
        frame('main2', MAIN_X, MAIN_W, 60),
      ],
      GEOMETRY,
      ['pag_1']
    );

    expect(at(result, 'rail2').y).toBe(TOP + 340);
    expect(at(result, 'main2').y).toBe(TOP + 340);
  });
});

describe('what has not changed', () => {
  it('a single column is still one cursor', () => {
    // Every frame spans, so this is the arithmetic the pass always had. Worth
    // asserting outright: every existing document is one column, and a
    // regression here would move somebody's résumé rather than a new layout's.
    const result = reflow(
      [
        frame('a', 100, CONTENT_WIDTH, 200),
        frame('b', 100, CONTENT_WIDTH, 150),
        frame('c', 100, CONTENT_WIDTH, 100),
      ],
      GEOMETRY,
      ['pag_1']
    );

    expect(result.placements.map((place) => place.y)).toEqual([TOP, TOP + 200, TOP + 350]);
    expect(result.pages).toBe(1);
  });

  it('reports no change when the columns already sit where they belong', () => {
    const settled: MeasuredFrame[] = [
      { nid: 'rail', page: 'pag_1', rect: { x: RAIL_X, y: TOP, w: RAIL_W, h: 80 }, height: 80 },
      { nid: 'main', page: 'pag_1', rect: { x: MAIN_X, y: TOP, w: MAIN_W, h: 300 }, height: 300 },
    ];

    expect(reflow(settled, GEOMETRY, ['pag_1']).changed).toBe(false);
  });
});
