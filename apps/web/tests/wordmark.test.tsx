/**
 * The wordmark: the sticker lockup.
 *
 * `resume` set plain, `wesume` reversed out of a coral capsule. RESUME and
 * WESUME are one character apart, and the whole name is the joke of that
 * repetition — so the mark is the repetition, said out loud.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import Wordmark from '@/ui/wordmark';

const css = readFileSync(resolve(process.cwd(), 'app/board.css'), 'utf8');

/** The declared value of `property` in the rule whose selector is exactly `selector`. */
function declared(selector: string, property: string): string | null {
  const bare = css.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const match of bare.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (match[1].trim() !== selector) continue;
    const hit = new RegExp(`(?:^|;)\\s*${property}\\s*:([^;]+)`).exec(match[2]);
    if (hit) return hit[1].trim();
  }
  return null;
}

/**
 * Where the ink of `wesume` sits inside the line box its padding is measured
 * from, in em. Measured off a 400px render with the padding zeroed and the
 * corners squared, where one pixel is 0.0025em. These are the face's ascent,
 * descent and x-height, so they move with it -- these are DM Sans's, and they
 * are not the ones Plex had. Re-measure the same way whenever the face
 * changes; the padding below is derived from them and nothing else.
 */
const LINE_BOX_ABOVE = 0.3225;
const LINE_BOX_BELOW = 0.1475;

describe('the mark', () => {
  it('is two runs of text and nothing drawn', () => {
    // Set, not drawn: it has to be selectable, searchable and readable by a
    // screen reader, and an SVG of the name is none of those.
    const { container } = render(<Wordmark />);
    expect(container.querySelector('.wm__word')?.textContent).toBe('resume');
    expect(container.querySelector('.wm__pill')?.textContent).toBe('wesume');
    expect(container.querySelector('svg')).toBeNull();
    expect(container.textContent).toBe('resumewesume');
  });

  it('scales whole from one measurement', () => {
    // Everything else is in `em`, so the register's 15px and the studio's 12px
    // are one prop apart. A mark needing a second measurement per placement
    // would drift between the two bars.
    const { container } = render(<Wordmark size={12} />);
    const mark = container.querySelector<HTMLElement>('.wm')!;
    expect(mark.style.fontSize).toBe('12px');

    for (const property of ['padding', 'border-radius']) {
      const value = declared('.wm__pill', property);
      expect(value).not.toBeNull();
      if (property === 'padding') expect(value).toMatch(/em/);
    }
    expect(declared('.wm', 'gap')).toMatch(/em$/);
    expect(declared('.wm', 'letter-spacing')).toMatch(/em$/);
  });

  it('keeps the plain half in the ink of whatever it sits on', () => {
    // The register's white bar and the studio's. Only the capsule carries a
    // colour, and it carries the shared one rather than a copy: the mark and
    // the filled control are the same coral, and a literal here would let them
    // drift a shade apart without anything failing.
    expect(declared('.wm', 'color')).toBe('inherit');
    expect(declared('.wm__word', 'color')).toBe('inherit');
    expect(declared('.wm__pill', 'background')).toBe('var(--brand)');
    expect(declared('.wm__pill', 'color')).toBe('#ffffff');
    expect(declared(':root', '--brand')).toBe('#ff5c39');
  });

  it('is set in the face the app is written in, not one of its own', () => {
    // A family loaded for twelve letters makes the name a guest on its own
    // page: the bar the mark sits in is set in the UI face, and a wordmark in
    // anything else reads as pasted on. What makes this a mark is the capsule,
    // not the letterforms -- so the letters are just the app talking, bold.
    expect(declared('.wm', 'font-family')).toMatch(/--font-ui/);
    expect(declared('.wm', 'font-weight')).toBe('700');
  });

  it('sits the word in the middle of its capsule rather than in the middle of its line box', () => {
    // `wesume` has no ascender and no descender, so within the line box the
    // padding is measured from, its ink already sits far down from the top and
    // close to the bottom -- read off a 400px render with the padding zeroed.
    // Equal padding inherits that gap and lands the word low in the capsule;
    // the bottom has to carry the whole of it.
    const padding = declared('.wm__pill', 'padding')!.split(/\s+/);
    expect(padding).toHaveLength(3);
    const [top, , bottom] = padding.map(parseFloat);
    expect(bottom - top).toBeCloseTo(LINE_BOX_ABOVE - LINE_BOX_BELOW, 3);
  });

  it('is bigger than the word it holds', () => {
    // What separates a sticker from a highlight. The capsule has to stand off
    // the letters on every side, and the top is the side that runs out first:
    // it is the one the line box has already spent most of.
    const [top, side, bottom] = declared('.wm__pill', 'padding')!
      .split(/\s+/)
      .map(parseFloat);
    expect(top + LINE_BOX_ABOVE).toBeGreaterThan(0.4);
    expect(bottom + LINE_BOX_BELOW).toBeGreaterThan(0.4);
    expect(side).toBeGreaterThanOrEqual(top + LINE_BOX_ABOVE);
  });

  it('hangs both halves off one baseline', () => {
    // The capsule is taller than the plain word by its own padding, so a flex
    // line centring the two boxes lifts the reversed half clear of the line
    // the other half sits on -- one word riding above the other.
    expect(declared('.wm', 'align-items')).toBe('baseline');
  });

  it('is a capsule at every size', () => {
    // A fixed radius large enough to round any height this is set at, rather
    // than a percentage that would go elliptical on a wide box.
    const radius = declared('.wm__pill', 'border-radius')!;
    expect(parseFloat(radius)).toBeGreaterThanOrEqual(999);
  });
});

describe('the favicon', () => {
  const icon = readFileSync(resolve(process.cwd(), 'app/icon.svg'), 'utf8');

  it('is the same sticker, compressed to the letter that changes', () => {
    // RESUME and WESUME differ only in their first character, so that letter
    // is the whole name reduced to the part that changes -- and it is the w,
    // because the w is the half wearing the colour.
    expect(icon).toContain('#FF5C39');
    expect(icon).not.toContain('#12110F');
  });

  it('draws the letter rather than setting it', () => {
    // A favicon renders before any font has loaded and in contexts that have
    // none, so a glyph would fall back to whatever the system happens to hold.
    expect(icon).not.toMatch(/font-family|<text/);
    expect(icon).toMatch(/<path/);
  });
});
