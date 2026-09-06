/**
 * The corners of the two floating panels.
 *
 * Read off the stylesheet rather than a render, for the same reason
 * `deck-layout.test.ts` is: jsdom parses CSS and lays nothing out, so a panel
 * whose children draw straight through its rounded corners reports every one
 * of them as present, visible and styled.
 *
 * The pairing is what these are really about. A radius without `overflow`
 * looks worse than no radius at all — the border turns and the head, the rows
 * and their hover fills carry on square, so the panel reads as rounded with
 * four patches missing.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'app/board.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  ''
);

/** The declared value of `property` in the rule whose selector is exactly `selector`. */
function declared(selector: string, property: string): string | null {
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (match[1].trim() !== selector) continue;
    const hit = new RegExp(`(?:^|;)\\s*${property}\\s*:([^;]+)`).exec(match[2]);
    if (hit) return hit[1].trim();
  }
  return null;
}

/** Every panel that floats over the app, and must therefore have a corner. */
const PANELS = ['.settings', '.picker__panel'];

describe('a floating panel', () => {
  it.each(PANELS)('%s is rounded', (selector) => {
    expect(declared(selector, 'border-radius')).toBe('var(--radius-panel)');
  });

  it.each(PANELS)('%s clips what it holds to that corner', (selector) => {
    // Load-bearing, not decoration: the settings head, its rows and the
    // picker's hover fills all run the full width of the panel.
    expect(declared(selector, 'overflow')).toBe('hidden');
  });

  it('is rounded by more than a button is', () => {
    // Radius reads relative to the edge it turns. The 4px that rounds a 32px
    // control is close to invisible on a 580px dialog, which is why a panel
    // needs a step of its own rather than borrowing the control's.
    const panel = parseFloat(declared(':root', '--radius-panel')!);
    const control = parseFloat(declared(':root', '--radius')!);
    expect(panel).toBeGreaterThan(control);
    // And stops short of the soft end: this is a dense settings panel, and a
    // larger corner makes a promise that rows of hairline rules do not keep.
    expect(panel).toBeLessThanOrEqual(10);
  });
});

describe('the two panels', () => {
  it('turn the same corner as each other', () => {
    // One opens the other -- Manage providers is the last row in the picker --
    // so a difference between them is visible one click apart.
    const [first, ...rest] = PANELS.map((selector) => declared(selector, 'border-radius'));
    expect(rest.every((value) => value === first)).toBe(true);
  });

  it('take it from the token rather than a literal', () => {
    // Two copies of 8px drift the moment either is touched, and the drift is
    // exactly the thing nobody files a bug about.
    expect(css).not.toMatch(/border-radius:\s*8px/);
  });
});
