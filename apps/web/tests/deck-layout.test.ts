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

describe('the mark in the studio bar', () => {
  // Read off the source, because nothing in this directory renders the studio
  // page: it is a client component over four stores and a canvas.
  const page = readFileSync(
    resolve(process.cwd(), 'app/studio/[canvasId]/page.tsx'),
    'utf8'
  );

  it('is the way back to the register', () => {
    // Every other control on that bar acts on the document. The mark is the
    // one thing there that is not about it, which is why it is what leaves.
    const brand = /<a([^>]*)>\s*<Wordmark/.exec(page);
    expect(brand).toBeTruthy();
    expect(brand![1]).toMatch(/href="\/"/);
  });

  it('says where it goes, starting with the words on screen', () => {
    // A label that drops the visible name breaks voice control: "click
    // resumewesume" would match nothing. Leading with it keeps both.
    const label = /aria-label="([^"]*)"[^>]*>\s*<Wordmark/.exec(page);
    expect(label).toBeTruthy();
    expect(label![1].toLowerCase()).toMatch(/^resumewesume/);
  });

  it('shows the press on the capsule, never on the letters', () => {
    // A wordmark that changes ink or gains an underline on hover stops being a
    // wordmark. The capsule can move, and it moves to the colour every other
    // filled control moves to.
    expect(declared('.rail__home', 'text-decoration')).toBe('none');
    expect(declared('.rail__home', 'color')).toBe('inherit');
    expect(declared('.rail__home:hover .wm__pill', 'background')).toBe('var(--brand-press)');
  });
});

describe('the register', () => {
  it('keeps room under the last row of résumés', () => {
    // The template strip below is pushed to the foot of the column by
    // `margin-top: auto`, which gives a generous gap while the list is short
    // and none at all once it wraps and fills the height -- so the strip's
    // rule came to rest flush against the bottom row of cards, reading as a
    // collision rather than as a division.
    const room = declared('.reg-docs', 'margin');
    expect(room).not.toBeNull();
    const bottom = room!.trim().split(/\s+/);
    expect(bottom[bottom.length - 1]).not.toBe('0');
  });

  it('never lets the grid be squashed below its rows', () => {
    // It is a flex child, so without this it shrinks under pressure and the
    // last row spills over whatever comes next.
    expect(declared('.reg-docs', 'flex')).toBe('none');
  });
});

describe('the delete on a register card', () => {
  it('is suppressed with pointer-events, never hidden outright', () => {
    // A transparent button still answers a click, so an invisible delete sat
    // over every card waiting for a stray press. Hiding it outright fixes that
    // and costs more: a `visibility: hidden` element cannot take focus, so
    // `:focus-visible` never matches and the control is unreachable without a
    // mouse.
    const rest = declared('.reg-doc__drop', 'pointer-events');
    expect(rest).toBe('none');
    expect(declared('.reg-doc__drop', 'visibility')).toBeNull();
    expect(declared('.reg-doc__drop', 'display')).not.toBe('none');
  });

  it('comes back for the pointer and for the keyboard alike', () => {
    // Matched on both halves rather than on the exact text: the selector is
    // two lines in the sheet, and pinning its whitespace would fail on a
    // reformat that changed nothing.
    const revealed = rules().find(
      (rule) =>
        rule.selector.includes('.reg-doc:hover .reg-doc__drop') &&
        rule.selector.includes('.reg-doc__drop:focus-visible')
    );
    expect(revealed).toBeTruthy();
    expect(revealed!.body).toMatch(/opacity:\s*1/);
    expect(revealed!.body).toMatch(/pointer-events:\s*auto/);
  });
});
