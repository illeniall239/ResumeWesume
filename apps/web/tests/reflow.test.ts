/**
 * Correcting a layout once the browser has measured it.
 *
 * Pure arithmetic over measurements, so it is tested without a DOM — which is
 * the point of splitting it out. On a freshly migrated document the correction
 * is drastic: a section estimated at 96pt renders at 712, and everything below
 * it is drawn on top of it until this runs.
 */

import { describe, expect, it } from 'vitest';

import { reflow, type MeasuredFrame } from '@/canvas/reflow';

/** A page 1000pt tall with 100pt margins: 800pt of usable height. */
// `contentWidth` matches the 400pt frames below, so every one of them spans the
// sheet -- which is what a single-column document is, and what these tests are
// about. The column cases live in reflow-columns.test.ts.
const GEOMETRY = { contentHeight: 800, top: 100, contentWidth: 400 };
const PAGES = ['pag_1', 'pag_2', 'pag_3'];

function frames(...specs: [string, number, number][]): MeasuredFrame[] {
  return specs.map(([nid, y, height]) => ({
    nid,
    page: 'pag_1',
    rect: { x: 100, y, w: 400, h: 96 },
    height,
  }));
}

const layout = (f: MeasuredFrame[]) =>
  reflow(f, GEOMETRY, PAGES).placements.map((p) => [p.nid, p.page, p.y]);

describe('reflow', () => {
  it('stacks frames using their measured heights', () => {
    expect(layout(frames(['a', 100, 200], ['b', 200, 300]))).toEqual([
      ['a', 0, 100],
      ['b', 0, 300],
    ]);
  });

  it('corrects the overlap a migrated document starts with', () => {
    // The real numbers from a migrated resume: frames placed 108pt apart on
    // estimated heights, while the experience section actually renders 712pt
    // tall — so education and skills were drawn on top of it.
    //
    // Summary ends at 196. Experience would then run to 908, past the 900pt
    // bottom, so it takes the next page whole rather than splitting. Education
    // would then run to 944 and moves again. Three sections, three pages: the
    // honest consequence of one section being nearly a full page by itself.
    const result = layout(
      frames(['summary', 104, 96], ['experience', 212, 712], ['education', 320, 132])
    );
    expect(result).toEqual([
      ['summary', 0, 100],
      ['experience', 1, 100],
      ['education', 2, 100],
    ]);
  });

  it('moves a frame whole rather than splitting it', () => {
    const result = layout(frames(['a', 100, 700], ['b', 800, 200]));
    expect(result).toEqual([
      ['a', 0, 100],
      ['b', 1, 100],
    ]);
  });

  it('does not push the first frame off an empty page', () => {
    // A frame taller than the page must not bounce forever looking for room.
    expect(layout(frames(['huge', 100, 2000]))).toEqual([['huge', 0, 100]]);
  });

  it('starts the frame after an overflowing one on a fresh page', () => {
    const result = layout(frames(['huge', 100, 900], ['next', 200, 100]));
    expect(result).toEqual([
      ['huge', 0, 100],
      ['next', 1, 100],
    ]);
  });

  it('fills a page exactly without spilling', () => {
    const result = layout(frames(['a', 100, 400], ['b', 200, 400], ['c', 300, 100]));
    expect(result[0]).toEqual(['a', 0, 100]);
    expect(result[1]).toEqual(['b', 0, 500]);
    expect(result[2]).toEqual(['c', 1, 100]);
  });

  it('counts the pages it needs', () => {
    expect(reflow(frames(['a', 100, 700], ['b', 100, 700]), GEOMETRY, PAGES).pages).toBe(2);
    expect(reflow(frames(['a', 100, 100]), GEOMETRY, PAGES).pages).toBe(1);
  });

  it('reports no change when the layout is already correct', () => {
    const already: MeasuredFrame[] = [
      { nid: 'a', page: 'pag_1', rect: { x: 100, y: 100, w: 400, h: 200 }, height: 200 },
      { nid: 'b', page: 'pag_1', rect: { x: 100, y: 300, w: 400, h: 150 }, height: 150 },
    ];
    expect(reflow(already, GEOMETRY, PAGES).changed).toBe(false);
  });

  it('ignores sub-point drift', () => {
    // Measurement noise must not bump the document version on every open.
    const noisy: MeasuredFrame[] = [
      { nid: 'a', page: 'pag_1', rect: { x: 100, y: 100, w: 400, h: 200 }, height: 200.3 },
    ];
    expect(reflow(noisy, GEOMETRY, PAGES).changed).toBe(false);
  });

  it('notices a frame that is on the wrong page', () => {
    const wrong: MeasuredFrame[] = [
      { nid: 'a', page: 'pag_2', rect: { x: 100, y: 100, w: 400, h: 200 }, height: 200 },
    ];
    expect(reflow(wrong, GEOMETRY, PAGES).changed).toBe(true);
  });

  it('preserves the order it was given', () => {
    // Document order is the order the sections already rendered in, so a
    // reflow must never silently rearrange somebody's resume.
    const result = layout(frames(['z', 100, 100], ['a', 200, 100], ['m', 300, 100]));
    expect(result.map((row) => row[0])).toEqual(['z', 'a', 'm']);
  });

  it('handles an empty document', () => {
    const result = reflow([], GEOMETRY, PAGES);
    expect(result.placements).toEqual([]);
    expect(result.pages).toBe(1);
    expect(result.changed).toBe(false);
  });
});
