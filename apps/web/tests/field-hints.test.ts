/**
 * What an empty field says, checked against the stylesheet rather than a render.
 *
 * jsdom generates no pseudo-elements, so every test in this directory passed
 * while the page drew each hint twice -- `::before` supplying the words in the
 * field's own bold type, `::after` supplying them again in grey italic. A new
 * résumé read "Your nameYour name", "Job titleJob title", the whole way down,
 * and nothing failed. So these read the sheet, which is the layer the mistake
 * lives on.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'app/globals.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  ''
);

/** Every `selector { ... }` in the sheet, in source order. Flat rules only. */
function rules(): { selector: string; body: string }[] {
  return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
    selector: match[1].trim(),
    body: match[2],
  }));
}

describe('the hint in an empty field', () => {
  it('is drawn once, by one pseudo-element', () => {
    // The defect exactly: two rules, each generating the placeholder on a
    // different pseudo-element of the same node, so both were drawn.
    const sources = rules().filter((rule) => /attr\(data-placeholder\)/.test(rule.body));
    expect(sources).toHaveLength(1);
  });

  it('is drawn in ::after, because the bullet glyph owns ::before', () => {
    // `.bullet--bullet::before` is the dot, and it is absolutely positioned.
    // A hint landing on that same pseudo-element inherits the position and is
    // drawn out in the left margin instead of in the line.
    const [source] = rules().filter((rule) => /attr\(data-placeholder\)/.test(rule.body));
    expect(source.selector).toMatch(/::after/);
    expect(css).not.toMatch(/\.editable:empty[^{,]*::before/);
  });

  it('stands down while the assistant is writing in the field', () => {
    // The typing caret is also an `::after`. Without this the two met on one
    // pseudo-element and the hint's words came out with the caret's 1px width
    // and red fill -- a red sliver where a placeholder should be.
    const touching = rules().filter(
      (rule) => rule.selector.includes('.editable:empty') && rule.selector.includes('::after')
    );
    const gates = touching.filter((rule) => /content:/.test(rule.body));
    expect(gates.length).toBeGreaterThan(0);
    for (const gate of gates) {
      expect(gate.selector).toContain(':not(.node--drafting)');
    }
  });
});

describe('an empty field that is a whole line', () => {
  it('is never turned into an inline-block', () => {
    // A blank `span` inside a line of other text needs help to be clickable.
    // A block-level field does not, and taking it stops it being a line: two
    // empty bullets sat side by side, 30px apart, reading as a pair of stray
    // dots rather than as two rows waiting to be typed in.
    const inlined = rules().filter((rule) => /display:\s*inline-block/.test(rule.body));
    for (const rule of inlined) {
      if (!rule.selector.includes('.editable:empty')) continue;
      expect(rule.selector).not.toMatch(/\bli\b|\bh1\b|\bdiv\b/);
    }
  });

  it('keeps a floor so the row can still be aimed at', () => {
    // With no text and no hint an empty bullet collapses to nothing, and
    // clearing one left it unreachable -- there was no longer anything to
    // click back into.
    const floor = rules().find((rule) => rule.selector === '.bullet.editable:empty');
    expect(floor).toBeTruthy();
    expect(floor!.body).toMatch(/min-height:/);
  });
});
