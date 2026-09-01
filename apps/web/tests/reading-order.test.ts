/**
 * The order elements are written into the DOM.
 *
 * This is the module that decides whether a designed PDF can be read by
 * anything other than a human. Chromium's text layer follows DOM order, so
 * getting this wrong does not look wrong on screen — it looks perfect, and the
 * extracted text is interleaved nonsense. Which is exactly the reputation
 * placed layouts have, and the failure this exists to avoid.
 */

import { describe, expect, it } from 'vitest';

import type { AnyElement, PageNode } from '@/contracts/doc';
import { documentReadingOrder, readingOrder } from '@/canvas/reading-order';

function frame(nid: string, x: number, y: number, w = 200, h = 60): AnyElement {
  return {
    nid,
    ref: nid,
    rect: { x, y, w, h },
    rotation: 0,
    autogrow: 'height',
    visible: true,
    locked: false,
    style: {
      align: 'left',
      font_scale: 1,
      color: null,
      background: null,
      padding: 0,
      radius: 0,
      opacity: 1,
    },
  } as AnyElement;
}

function page(...elements: AnyElement[]): PageNode {
  return {
    nid: 'pag_aaaaa',
    size: 'A4',
    orientation: 'portrait',
    background: null,
    elements,
  };
}

const order = (p: PageNode) => readingOrder(p).map((element) => element.nid);

describe('readingOrder', () => {
  it('reads a single column top to bottom', () => {
    const p = page(frame('c', 30, 500), frame('a', 30, 100), frame('b', 30, 300));
    expect(order(p)).toEqual(['a', 'b', 'c']);
  });

  it('reads two columns one after the other, not interleaved', () => {
    // The failure this module exists for: sorted by y alone this comes out
    // left, right, left, right — and extracts as gibberish.
    const p = page(
      frame('left-top', 30, 100, 150),
      frame('right-top', 300, 110, 240),
      frame('left-bottom', 30, 300, 150),
      frame('right-bottom', 300, 320, 240)
    );
    expect(order(p)).toEqual(['left-top', 'left-bottom', 'right-top', 'right-bottom']);
  });

  it('puts the narrower left column before the wider main column', () => {
    const p = page(frame('main', 220, 100, 320), frame('sidebar', 30, 100, 160));
    expect(order(p)).toEqual(['sidebar', 'main']);
  });

  it('keeps paint order out of it', () => {
    // Declared back-to-front; reading order is decided by geometry alone.
    const p = page(frame('bottom', 30, 400), frame('top', 30, 100));
    expect(order(p)).toEqual(['top', 'bottom']);
  });

  it('groups elements that overlap horizontally even when edges differ', () => {
    // A heading inset slightly from the column beneath it is still that column.
    const p = page(frame('body', 30, 200, 200), frame('heading', 40, 100, 180));
    expect(order(p)).toEqual(['heading', 'body']);
  });

  it('treats barely-touching elements as separate columns', () => {
    // 10pt of overlap on a 200pt frame is a coincidence, not a column.
    const p = page(frame('right', 190, 100, 200), frame('left', 0, 300, 200));
    expect(order(p)).toEqual(['left', 'right']);
  });

  it('is stable for elements at the same height', () => {
    const p = page(frame('b', 300, 100, 100), frame('a', 30, 100, 100));
    expect(order(p)).toEqual(['a', 'b']);
  });

  it('handles an empty page', () => {
    expect(order(page())).toEqual([]);
  });
});

describe('documentReadingOrder', () => {
  const first = page(frame('p1a', 30, 100), frame('p1b', 30, 300));
  const second: PageNode = { ...page(frame('p2a', 30, 100)), nid: 'pag_bbbbb' };

  it('reads pages in order', () => {
    expect(documentReadingOrder([first, second]).map((e) => e.nid)).toEqual([
      'p1a',
      'p1b',
      'p2a',
    ]);
  });

  it('an explicit override wins', () => {
    const result = documentReadingOrder([first, second], ['p2a', 'p1b', 'p1a']);
    expect(result.map((e) => e.nid)).toEqual(['p2a', 'p1b', 'p1a']);
  });

  it('an override that has drifted does not delete anything', () => {
    // Same salvage rule as the engine's reorder: unknown ids are dropped and
    // omissions are appended, because a stale override must never silently
    // remove an element from the text layer.
    const result = documentReadingOrder([first, second], ['p2a', 'frm_ghost']);
    expect(result.map((e) => e.nid)).toEqual(['p2a', 'p1a', 'p1b']);
  });

  it('a repeated id in the override is not emitted twice', () => {
    const result = documentReadingOrder([first, second], ['p1b', 'p1b']);
    expect(result.map((e) => e.nid)).toEqual(['p1b', 'p1a', 'p2a']);
  });

  it('an empty override falls back to the derived order', () => {
    expect(documentReadingOrder([first], []).map((e) => e.nid)).toEqual(['p1a', 'p1b']);
  });
});
