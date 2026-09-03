/**
 * Naming the sheets a reflow needs.
 *
 * The ids are derived from position so that a reflow torn down and re-run
 * proposes the same page rather than a second empty one. What that trades away
 * is uniqueness, and this is where it is bought back.
 */

import { describe, expect, it } from 'vitest';

import { mintPages, reflowSignature } from '@/canvas/use-reflow';
import type { StudioDoc } from '@/contracts/doc';

describe('minting page ids', () => {
  it('mints nothing when the pages already exist', () => {
    expect(mintPages(['pag_aaaaa', 'pag_bbbbb'], 2)).toEqual([]);
  });

  it('mints the shortfall', () => {
    expect(mintPages(['pag_aaaaa'], 3)).toHaveLength(2);
  });

  it('proposes the same ids when it runs again on the same document', () => {
    // The reason the ids are derived at all: a re-run before the ops land must
    // ask for the page it already asked for, not a second one.
    expect(mintPages(['pag_aaaaa'], 3)).toEqual(mintPages(['pag_aaaaa'], 3));
  });

  it('skips an id the document already holds', () => {
    // Delete the middle of three pages and the count says to mint position 2 —
    // whose derived id is the third page, still there. Reusing it put two
    // pages under one id on screen.
    const third = mintPages(['pag_aaaaa'], 3)[1];
    const minted = mintPages(['pag_aaaaa', third], 3);

    expect(minted).toHaveLength(1);
    expect(minted).not.toContain(third);
  });

  it('never returns an id twice', () => {
    const minted = mintPages(['pag_aaaaa'], 12);
    expect(new Set(minted).size).toBe(minted.length);
  });
});

describe('the signature that decides whether to measure again', () => {
  const doc = (pages: string[]): StudioDoc =>
    ({
      pages: pages.map((nid) => ({
        nid,
        size: 'a4',
        orientation: 'portrait',
        background: null,
        elements: [],
      })),
    }) as unknown as StudioDoc;

  it('changes when the document changes, not only when pages do', () => {
    // Keyed on page ids alone this ran once and never again: editing text does
    // not rename a page. An agent turn would rewrite a two-line bullet into
    // four, the frame kept the height measured for the old text, and the next
    // section was drawn over the top of it.
    expect(reflowSignature(doc(['pag_a']), 4)).not.toBe(
      reflowSignature(doc(['pag_a']), 5)
    );
  });

  it('still changes when a page is added', () => {
    expect(reflowSignature(doc(['pag_a']), 7)).not.toBe(
      reflowSignature(doc(['pag_a', 'pag_b']), 7)
    );
  });

  it('is stable for the same document at the same version', () => {
    expect(reflowSignature(doc(['pag_b', 'pag_a']), 3)).toBe(
      reflowSignature(doc(['pag_a', 'pag_b']), 3)
    );
  });
});
