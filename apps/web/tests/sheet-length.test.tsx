/**
 * How long the résumé is.
 *
 * The one fact about a résumé everybody is told to care about, which this app
 * knew and never said. A second page is a decision, and finding out about it at
 * export — or from the person reading it — is finding out too late.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SheetLength, lengthOf } from '@/canvas/sheet-length';
import type { StudioDoc } from '@/contracts/doc';

const doc = (pages: number) =>
  ({
    schema_version: 1,
    pages: Array.from({ length: pages }, (_, index) => ({ nid: `pag_${index}` })),
  }) as unknown as StudioDoc;

describe('what it says', () => {
  it('answers the question rather than counting, on one page', () => {
    // "1 page" is a count. On one page it is not a count, it is the thing
    // people are actually asking about.
    expect(lengthOf(1)).toBe('Fits on one page');
  });

  it('counts once there is more than one', () => {
    expect(lengthOf(2)).toBe('2 pages');
    expect(lengthOf(4)).toBe('4 pages');
  });

  it('says nothing about a document with no pages', () => {
    // Before the first reflow there are none, and "0 pages" is a fact about
    // the loading state rather than about the résumé.
    expect(lengthOf(0)).toBe('');
  });
});

describe('in the strip', () => {
  it('shows the length', () => {
    render(<SheetLength doc={doc(1)} />);
    expect(screen.getByText('Fits on one page')).toBeInTheDocument();
  });

  it('renders nothing at all before there is a document', () => {
    const { container } = render(<SheetLength doc={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('stays quiet at two pages and flags at three', () => {
    // Two pages is ordinary for anyone senior. Three is the point at which it
    // has stopped being a fact and started being something to look at.
    const { container: two } = render(<SheetLength doc={doc(2)} />);
    expect(two.querySelector('.length--long')).toBeNull();

    const { container: three } = render(<SheetLength doc={doc(3)} />);
    expect(three.querySelector('.length--long')).not.toBeNull();
  });

  it('reads as a sentence to a screen reader', () => {
    render(<SheetLength doc={doc(2)} />);
    expect(screen.getByLabelText('This résumé is 2 pages long')).toBeInTheDocument();
  });
});
