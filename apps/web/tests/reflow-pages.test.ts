/**
 * Naming the sheets a reflow needs.
 *
 * The ids are derived from position so that a reflow torn down and re-run
 * proposes the same page rather than a second empty one. What that trades away
 * is uniqueness, and this is where it is bought back.
 */

import { describe, expect, it } from 'vitest';

import { mintPages } from '@/canvas/use-reflow';

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
