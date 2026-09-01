/**
 * Where the printed pages break.
 *
 * The arithmetic is the whole feature, so it is tested here rather than
 * through a rendered page: the DOM part is a measurement loop, and what has to
 * be right is that an unbreakable block moves whole instead of splitting —
 * the behaviour that made a two-page resume into three with an 87% empty first
 * page, invisibly, until the user opened the PDF.
 */

import { describe, expect, it } from 'vitest';

import { geometryFor, mmToPx, paginate, type MeasuredBlock } from '@/render/paginate';

/** Round numbers: a 100px page with 20px between sheets. */
const GEOMETRY = { contentHeight: 100, step: 120 };

function stack(heights: readonly (number | [number, true])[]): MeasuredBlock[] {
  let top = 0;
  return heights.map((entry, index) => {
    const [height, heading] = Array.isArray(entry) ? entry : [entry, false];
    const block: MeasuredBlock = {
      key: `b${index}`,
      top,
      height,
      ...(heading ? { heading: true } : {}),
    };
    top += height;
    return block;
  });
}

describe('paginate', () => {
  it('leaves a document that fits on one page alone', () => {
    const result = paginate(stack([30, 30, 30]), GEOMETRY);
    expect(result.pages).toBe(1);
    expect(result.breaks.size).toBe(0);
  });

  it('moves a block that would straddle the boundary onto the next sheet', () => {
    // Blocks at 0-40, 40-80, 80-120. The third crosses the 100px boundary, so
    // it moves whole rather than losing 20px to the next page.
    const result = paginate(stack([40, 40, 40]), GEOMETRY);
    expect(result.pages).toBe(2);
    expect([...result.breaks.keys()]).toEqual(['b2']);
    // Pushed from 80 to the next sheet's content top at 120.
    expect(result.breaks.get('b2')).toBe(40);
  });

  it('accounts for earlier pushes when placing later breaks', () => {
    // Without carrying the offset forward, the second break would be computed
    // against a position the block no longer occupies.
    const result = paginate(stack([60, 60, 60, 60]), GEOMETRY);
    expect(result.pages).toBe(4);
    expect([...result.breaks.keys()]).toEqual(['b1', 'b2', 'b3']);
  });

  it('takes a heading with the block it introduces', () => {
    // A heading alone at the foot of a page announces nothing.
    const blocks = stack([70, [10, true], 40]);
    const result = paginate(blocks, GEOMETRY);
    expect([...result.breaks.keys()]).toEqual(['b1']);
    expect(result.breaks.has('b2')).toBe(false);
  });

  it('gives up the heading rule rather than paginating worse', () => {
    // A 10px heading and a 95px block cannot share a 100px page. Moving the
    // heading anyway would strand it alone on page two and push the block to
    // page three — worse on both counts. Browsers drop `break-after: avoid`
    // here for the same reason.
    const result = paginate(stack([70, [10, true], 95]), GEOMETRY);
    expect(result.breaks.has('b1')).toBe(false);
    expect([...result.breaks.keys()]).toEqual(['b2']);
    expect(result.pages).toBe(2);
  });

  it('lets a block taller than a page overflow rather than hiding it', () => {
    // Rare, and what the browser does when break-inside cannot be honoured.
    // Clipping it would silently delete a bullet from someone's resume.
    const result = paginate(stack([150]), GEOMETRY);
    expect(result.breaks.size).toBe(0);
    expect(result.pages).toBe(2);
  });

  it('handles an empty document', () => {
    const result = paginate([], GEOMETRY);
    expect(result.pages).toBe(1);
    expect(result.breaks.size).toBe(0);
  });

  it('refuses to divide by a zero-height page', () => {
    expect(paginate(stack([50]), { contentHeight: 0, step: 0 }).pages).toBe(1);
  });

  it('a block landing exactly on the boundary does not break', () => {
    const result = paginate(stack([50, 50]), GEOMETRY);
    expect(result.breaks.size).toBe(0);
    expect(result.pages).toBe(1);
  });
});

describe('geometry', () => {
  it('converts millimetres at the 96dpi the CSS mm unit assumes', () => {
    expect(mmToPx(25.4)).toBeCloseTo(96, 5);
  });

  it('derives A4 content height from the export margins', () => {
    const geometry = geometryFor({ heightMm: 297, marginMm: 10, gapPx: 24 });
    // 297 - 2*10 = 277mm of usable height, matching render_pdf's margins.
    expect(geometry.contentHeight).toBeCloseTo(mmToPx(277), 5);
  });

  it('makes the step clear both margins and the gap between sheets', () => {
    const geometry = geometryFor({ heightMm: 297, marginMm: 10, gapPx: 24 });
    expect(geometry.step - geometry.contentHeight).toBeCloseTo(mmToPx(20) + 24, 5);
  });

  it('matches the real resume: 384mm of content over two A4 pages', () => {
    // Measured from the live studio after the margin fix.
    const geometry = geometryFor({ heightMm: 297, marginMm: 10, gapPx: 24 });
    const blocks = stack([mmToPx(200), mmToPx(184)]);
    expect(paginate(blocks, geometry).pages).toBe(2);
  });
});
