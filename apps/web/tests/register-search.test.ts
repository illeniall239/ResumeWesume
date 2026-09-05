/**
 * Finding a résumé by name in the register.
 *
 * The case that matters and is easy to miss: this app is about *résumés*, its
 * own titles carry accents, and the person typing has an ordinary keyboard.
 * A search that cannot get from "resume" to "Résumé" looks broken for a reason
 * that has nothing to do with the document.
 */

import { describe, expect, it } from 'vitest';

import { fold, search, titleOf } from '@/register/search';

const sheets = [
  { title: 'Rao Muhammad Hamza' },
  { title: 'Backend — Stripe' },
  { title: 'Résumé — final' },
  { title: 'Grad school statement' },
  { title: null },
];

const found = (query: string) => search(sheets, query).map(titleOf);

describe('searching the register', () => {
  it('finds a document by part of its name', () => {
    expect(found('stripe')).toEqual(['Backend — Stripe']);
  });

  it('does not care about case', () => {
    expect(found('HAMZA')).toEqual(['Rao Muhammad Hamza']);
    expect(found('hamza')).toEqual(['Rao Muhammad Hamza']);
  });

  it('gets from a plain keyboard to an accented title', () => {
    // The whole reason this is not a bare `includes`.
    expect(found('resume')).toEqual(['Résumé — final']);
    expect(found('Résumé')).toEqual(['Résumé — final']);
  });

  it('finds a document that was never named', () => {
    expect(found('untitled')).toEqual(['Untitled']);
  });

  it('matches in the middle of a name, not only at the start', () => {
    expect(found('school')).toEqual(['Grad school statement']);
  });

  it('ignores surrounding space, which a paste brings with it', () => {
    expect(found('  stripe  ')).toEqual(['Backend — Stripe']);
  });

  it('treats an empty box as no filter at all', () => {
    // Returning nothing for an empty query would empty the register the moment
    // somebody clicked into the search field.
    expect(search(sheets, '').length).toBe(sheets.length);
    expect(search(sheets, '   ').length).toBe(sheets.length);
  });

  it('keeps the order it was given', () => {
    // The list arrives sorted by last edited; searching filters it, and must
    // not also reorder it.
    expect(found('a')).toEqual([
      'Rao Muhammad Hamza',
      'Backend — Stripe',
      'Résumé — final',
      'Grad school statement',
    ]);
  });

  it('returns nothing when nothing matches', () => {
    expect(found('zzz')).toEqual([]);
  });

  it('does not mutate the list it was given', () => {
    const before = [...sheets];
    search(sheets, 'stripe');
    expect(sheets).toEqual(before);
  });
});

describe('folding', () => {
  it('strips diacritics rather than mapping them one by one', () => {
    // NFD splits an accented character into a letter plus a combining mark, so
    // there is no substitution table to fall behind.
    expect(fold('Résumé')).toBe('resume');
    expect(fold('Ångström')).toBe('angstrom');
    expect(fold('Łódź')).toBe('łodz');
  });

  it('collapses whitespace so a double space still matches', () => {
    expect(fold('Backend   —  Stripe')).toBe('backend — stripe');
  });
});

describe('the name a document is listed under', () => {
  it('falls back to Untitled, and searches under that name', () => {
    expect(titleOf({ title: null })).toBe('Untitled');
    expect(titleOf({ title: '   ' })).toBe('Untitled');
    expect(titleOf({ title: ' Kept ' })).toBe('Kept');
  });
});
