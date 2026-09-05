/**
 * Two rules about the studio's frame, read straight off the stylesheet.
 *
 * These are here because the failure they describe is invisible to every other
 * test in this directory. jsdom parses CSS but does not lay anything out, so a
 * pane that is 52px taller than the space it sits in still reports its children
 * as rendered, visible, and clickable -- which is exactly what happened when
 * the document's bar moved above both panes: `.schedule` kept `height: 100vh`,
 * overran the column by the height of the bar, and `aside`'s clip took Attach
 * image and Send off the bottom of the composer. Every test passed.
 *
 * So the checks are on the text of the stylesheet rather than on a render.
 * Blunt, but it is the layer the mistake lives on.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

// From the package root, which is where vitest is rooted.
const css = readFileSync(resolve(process.cwd(), 'app/board.css'), 'utf8');

/** Every `selector { ... }` in the sheet, comments stripped, in source order. */
function rules(): { selector: string; body: string }[] {
  const bare = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const found: { selector: string; body: string }[] = [];
  for (const match of bare.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    found.push({ selector: match[1].trim(), body: match[2] });
  }
  return found;
}

/** The declared value of `property` in the rule whose selector is exactly `selector`. */
function declared(selector: string, property: string): string | null {
  for (const rule of rules()) {
    if (rule.selector !== selector) continue;
    const hit = new RegExp(`(?:^|;)\\s*${property}\\s*:([^;]+)`).exec(rule.body);
    if (hit) return hit[1].trim();
  }
  return null;
}

describe('the studio frame', () => {
  it('gives the window height to the deck alone', () => {
    // A pane inside the deck asking for `100vh` is claiming the whole window
    // while sitting under a bar that has already taken part of it. `.deck` is
    // the only thing here whose box actually is the window.
    const claimants = rules()
      .filter((rule) => /height:\s*100vh/.test(rule.body))
      .map((rule) => rule.selector)
      .filter((selector) => selector !== '.deck');

    expect(claimants).toEqual([]);
  });

  it('splits the top bar on the same columns as the panes below it', () => {
    // The rule between the halves of the bar is the rule between the
    // conversation and the sheet, continued upward. If the two templates drift
    // apart the bar still looks fine on its own and is visibly crooked against
    // the column edge under it -- which is the sort of thing nobody files.
    const bar = declared('.rail--top', 'grid-template-columns');
    const panes = declared('.deck__panes', 'grid-template-columns');

    expect(panes).not.toBeNull();
    expect(bar).toBe(panes);
  });
});
