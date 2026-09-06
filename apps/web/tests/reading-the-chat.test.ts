/**
 * Which key presses belong to the sheet, and which belong to whoever is
 * reading the conversation beside it.
 *
 * The studio's shortcuts hang off the window, because a selected box is not a
 * focusable element and there is nothing else to hang them on. They deferred
 * to `contentEditable` and `textarea`, which covers the caret and nothing
 * else — and reading is not typing. A click on a reply focuses nothing, so
 * Ctrl+A there ran the sheet's own select-all: every element on page one
 * selected, and the text selection thrown away. Ctrl+C then copied nothing,
 * and the conversation read as text that could not be copied at all.
 */

import { describe, expect, it } from 'vitest';

import { meantForTheSheet } from '@/canvas/keys';

/** A detached tree shaped like the studio: a conversation beside a sheet. */
function studio() {
  const root = document.createElement('div');
  root.innerHTML = `
    <main class="deck">
      <aside>
        <div class="schedule">
          <div class="revision">
            <div class="revision__prose"><p>Tightened the second bullet.</p></div>
            <details><pre>The bullet is the vague one.</pre></details>
          </div>
        </div>
        <div class="composer"><textarea></textarea></div>
      </aside>
      <div class="deck__board">
        <div class="canvas-page">
          <div class="element"><span class="editable" contenteditable="true">Alex</span></div>
        </div>
      </div>
    </main>`;
  const find = (selector: string) => root.querySelector<HTMLElement>(selector)!;
  return {
    reply: find('.revision__prose p'),
    reasoning: find('details pre'),
    composer: find('textarea'),
    sheet: find('.canvas-page'),
    field: find('.editable'),
  };
}

describe('a key pressed while reading the conversation', () => {
  it('is not the sheet\'s, even though nothing there has focus', () => {
    // The whole defect. Clicking prose focuses nothing, so `target` is the
    // body and every guard written against focus says "yes, act on the sheet".
    // Where the selection is anchored is the fact that actually answers it.
    const { reply } = studio();
    expect(meantForTheSheet(document.body, reply.firstChild)).toBe(false);
  });

  it('is not the sheet\'s inside a fold either', () => {
    // Reasoning and tool calls are the parts most worth copying: they are the
    // record of what the assistant did, and the only place it exists as text.
    const { reasoning } = studio();
    expect(meantForTheSheet(document.body, reasoning.firstChild)).toBe(false);
  });

  it('still belongs to the sheet when the selection is on the document', () => {
    // Ctrl+A on the résumé selects the page's elements, and must go on doing
    // so — this narrows the shortcuts, it does not remove them.
    const { sheet } = studio();
    expect(meantForTheSheet(sheet, sheet)).toBe(true);
  });

  it('belongs to the sheet when nothing is selected at all', () => {
    // The ordinary case: someone clicked a box and reached for a shortcut.
    expect(meantForTheSheet(document.body, null)).toBe(true);
  });
});

describe('the caret, which was already deferred to', () => {
  it('keeps a field on the résumé to itself', () => {
    const { field } = studio();
    // jsdom does not derive `isContentEditable` from the attribute.
    Object.defineProperty(field, 'isContentEditable', { value: true });
    expect(meantForTheSheet(field, field)).toBe(false);
  });

  it('keeps the composer to itself', () => {
    const { composer } = studio();
    expect(meantForTheSheet(composer, composer)).toBe(false);
  });
});

describe('an anchor that is not an element', () => {
  it('is read through its parent rather than dropped', () => {
    // A selection anchor is almost always a text node, and a text node has no
    // `closest`. Reading it as "not in the conversation" would put the bug
    // straight back for the only case that actually occurs.
    const { reply } = studio();
    const text = reply.firstChild!;
    expect(text.nodeType).toBe(Node.TEXT_NODE);
    expect(meantForTheSheet(document.body, text)).toBe(false);
  });

  it('is harmless when it is detached from the page', () => {
    expect(meantForTheSheet(document.body, document.createTextNode('x'))).toBe(true);
  });
});
