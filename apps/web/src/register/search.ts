/**
 * Finding a résumé by name.
 *
 * A plain `includes` is nearly right and wrong in one way that matters here:
 * this app is about *résumés*, its own titles carry accents, and the person
 * typing has an ordinary keyboard. "resume" not finding "Résumé — final" is
 * the search looking broken for a reason that has nothing to do with the
 * document.
 *
 * So both sides are folded to a comparable form -- case dropped, diacritics
 * removed, runs of whitespace collapsed -- and then matched as a substring.
 * Substring rather than fuzzy: a register holds a handful of documents named
 * by their owner, and a fuzzy match on ten items mostly returns all ten.
 */

/**
 * Strip a string to what two people would agree is "the same word".
 *
 * `NFD` splits an accented character into its letter plus a combining mark,
 * which the range below then removes -- so "é" becomes "e" without a table of
 * substitutions to keep up to date.
 */
export function fold(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
}

/** The name a document is listed under, including the one it has by default. */
export function titleOf(sheet: { title?: string | null }): string {
  return sheet.title?.trim() || 'Untitled';
}

/**
 * The documents matching `query`, in the order they were given.
 *
 * An empty query is not a filter: it returns everything rather than nothing,
 * which is what an empty search box means.
 */
export function search<T extends { title?: string | null }>(
  documents: readonly T[],
  query: string
): T[] {
  const wanted = fold(query);
  if (!wanted) return [...documents];
  return documents.filter((sheet) => fold(titleOf(sheet)).includes(wanted));
}
