/**
 * Node ids must never reach the reader.
 *
 * The model is given an id-annotated outline because ids are how it addresses
 * a node, and after reading forty lines of `[blt_9x6q5] …` it copies the
 * format into its reply. The system prompt asks it not to; this is what makes
 * sure, because a prompt is advisory.
 */

import { describe, expect, it } from 'vitest';

import { stripNodeIds } from '@/chat/prose';

describe('stripNodeIds', () => {
  it('removes a bracketed id and the space after it', () => {
    // Verbatim from a real turn.
    expect(
      stripNodeIds(
        '[blt_9x6q5] Built an end-to-end AI-based post-production system.'
      )
    ).toBe('Built an end-to-end AI-based post-production system.');
  });

  it('removes an id used mid-sentence', () => {
    expect(stripNodeIds('I rewrote blt_9x6q5 to lead with the outcome.')).toBe(
      'I rewrote to lead with the outcome.'
    );
  });

  it('handles every node kind', () => {
    const kinds = ['exp_7f3a2', 'edu_1a2b3', 'prj_4c5d6', 'blt_9c21x', 'skl_4d10p', 'sgp_ggggg'];
    for (const nid of kinds) {
      expect(stripNodeIds(`Updated [${nid}] for you.`)).toBe('Updated for you.');
    }
  });

  it('removes several ids in one message', () => {
    expect(
      stripNodeIds('Tightened [blt_aaaaa] and [blt_bbbbb] under [exp_11111].')
    ).toBe('Tightened and under.');
  });

  it('leaves ordinary prose alone', () => {
    const text = 'I shortened your first bullet at Northwind Systems.';
    expect(stripNodeIds(text)).toBe(text);
  });

  it('does not eat words that merely contain an underscore', () => {
    const text = 'The build_script and my_project were unchanged.';
    expect(stripNodeIds(text)).toBe(text);
  });

  it('does not eat a bracketed word that is not an id', () => {
    const text = 'I left the summary [unchanged] as you asked.';
    expect(stripNodeIds(text)).toBe(text);
  });

  it('holds back a half-arrived id while streaming', () => {
    // Deltas split anywhere, so without this the fragment flashes on screen
    // for a frame and is then retracted.
    expect(stripNodeIds('Rewrote [blt_9x', { streaming: true })).toBe('Rewrote');
    expect(stripNodeIds('Rewrote [bl', { streaming: true })).toBe('Rewrote');
    expect(stripNodeIds('Rewrote [', { streaming: true })).toBe('Rewrote');
  });

  it('keeps a trailing bracket once the message is complete', () => {
    // Not streaming: an unmatched bracket is just text the model wrote.
    expect(stripNodeIds('See the note [')).toBe('See the note [');
  });

  it('tidies the spacing a removal leaves behind', () => {
    expect(stripNodeIds('Rewrote [blt_aaaaa] , then stopped.')).toBe(
      'Rewrote, then stopped.'
    );
  });

  it('cleans the reply that prompted this, verbatim', () => {
    // qwen3, on the turn immediately after the system prompt was told not to.
    // The proof that the prompt rule cannot be the guarantee.
    expect(
      stripNodeIds(
        'job (**exp_7a8f5: System Developer @ GEO TV**) is **[blt_8fbc8]**: "Deployed"'
      )
    ).toBe('job (**System Developer @ GEO TV**) is: "Deployed"');
  });

  it('removes an id that is the whole content of a bold span', () => {
    // Deleting just the id would leave "****" behind.
    expect(stripNodeIds('The weakest is **[blt_8fbc8]** here.')).toBe(
      'The weakest is here.'
    );
  });

  it('removes the colon an id was using to label what follows', () => {
    expect(stripNodeIds('exp_7a8f5: System Developer')).toBe('System Developer');
  });

  it("leaves the model's own markdown emphasis alone", () => {
    // An earlier version collapsed every "**" on the way past, because "**"
    // backtracks into "*" followed by "*".
    expect(stripNodeIds('**Vagueness**: the phrase is too generic.')).toBe(
      '**Vagueness**: the phrase is too generic.'
    );
    expect(stripNodeIds('Use *emphasis* freely.')).toBe('Use *emphasis* freely.');
  });

  it('is safe on empty input', () => {
    expect(stripNodeIds('')).toBe('');
  });
});
