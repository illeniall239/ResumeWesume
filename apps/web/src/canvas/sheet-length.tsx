/**
 * How long the résumé is, said plainly.
 *
 * The one fact about a résumé that everybody is told to care about and that the
 * app knew and never mentioned. It is not decoration: a second page is a
 * decision, and finding out about it at export — or worse, from the person
 * reading it — is finding out too late.
 *
 * Read off `doc.pages`, which is the truth after the reflow pass: the browser
 * measures the rendered text and spills what does not fit onto another sheet,
 * so this counts pages the document actually has rather than estimating from
 * the content.
 */

'use client';

import type { StudioDoc } from '@/contracts/doc';

/** What to say about a résumé of this many pages. */
export function lengthOf(pages: number): string {
  if (pages <= 0) return '';
  // "Fits on one page" rather than "1 page", because on one page it is not a
  // count, it is the thing people are actually asking about.
  if (pages === 1) return 'Fits on one page';
  return `${pages} pages`;
}

export function SheetLength({ doc }: { doc: StudioDoc | null }) {
  const pages = doc?.pages?.length ?? 0;
  const text = lengthOf(pages);
  if (!text) return null;

  return (
    <span
      className={`length${pages > 2 ? ' length--long' : ''}`}
      // Stated, never announced: it changes as you type, and a live region
      // would interrupt a screen reader mid-sentence to say the page count
      // again. Somebody who wants it can read it.
      aria-label={`This résumé is ${pages} ${pages === 1 ? 'page' : 'pages'} long`}
    >
      {text}
    </span>
  );
}

export default SheetLength;
