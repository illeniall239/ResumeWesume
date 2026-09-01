/**
 * Minting a node id in the browser.
 *
 * Ids are normally minted server-side, and that remains the rule for content:
 * a bullet or a job gets its id from the engine. But an element the user
 * *creates* — a duplicated shape, a new text box — has to be addressable the
 * instant it appears, before any round trip, or the optimistic render has
 * nothing to key on and the gesture that follows it has nothing to target.
 *
 * The format is the engine's, because the engine checks it: a three-letter
 * kind prefix and five characters of Crockford-ish base32. An id that does not
 * parse is rejected by `kind_of` long before anything looks at what it names.
 */

/** Crockford-ish base32, as in `studio/doc/nodes.py`. */
const ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz';
const SUFFIX_LENGTH = 5;

/** A fresh id of the same kind as an existing one. */
export function mintLike(nid: string): string {
  const prefix = nid.split('_', 1)[0] || 'shp';
  return mint(prefix);
}

export function mint(prefix: string): string {
  let suffix = '';
  for (let index = 0; index < SUFFIX_LENGTH; index += 1) {
    suffix += ALPHABET[Math.floor(Math.random() * ALPHABET.length)];
  }
  return `${prefix}_${suffix}`;
}
