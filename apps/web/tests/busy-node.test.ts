import { afterEach, describe, expect, it } from 'vitest';

import { editingNode } from '@/store/chat';

/**
 * What the agent is told the user is editing.
 *
 * The gate that refuses the agent on a line the user has the caret in reads
 * this. It used to read a store flag set on focus and cleared on blur -- and a
 * line unmounted while focused (the skills list does it on any reorder) never
 * fires its blur, so the flag stuck on a line that was gone, and every later
 * turn was refused on a line nobody was editing. Reading the live DOM cannot
 * stick: if the caret is not in an editable node right now, nothing is busy.
 */

function editable(attrs: Record<string, string>): HTMLElement {
  const el = document.createElement('div');
  el.setAttribute('contenteditable', 'plaintext-only');
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  document.body.appendChild(el);
  el.focus();
  return el;
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('editingNode', () => {
  it('is empty when nothing is focused', () => {
    expect(editingNode()).toEqual([]);
  });

  it('is the nid of a focused text node', () => {
    editable({ 'data-nid': 'blt_9c21x' });
    expect(editingNode()).toEqual(['blt_9c21x']);
  });

  it('is the bare nid of a focused field, not the field path', () => {
    // The gate keys on the node, not `nid.field`.
    editable({ 'data-field': 'personal.phone' });
    expect(editingNode()).toEqual(['personal']);
  });

  it('is empty when focus is on something that is not editable', () => {
    // The composer, a button, or the body after a line was re-rendered out
    // from under the caret -- the case that used to stick.
    const button = document.createElement('button');
    document.body.appendChild(button);
    button.focus();
    expect(editingNode()).toEqual([]);
  });

  it('goes empty the moment the focused node leaves the document', () => {
    // The exact bug: focus a line, then remove it (a re-render). The old
    // flag would still say "editing"; the DOM says nobody is.
    const line = editable({ 'data-nid': 'skl_ppppp' });
    expect(editingNode()).toEqual(['skl_ppppp']);
    line.remove();
    expect(editingNode()).toEqual([]);
  });
});
