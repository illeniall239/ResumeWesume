/**
 * Printing the sheet.
 *
 * Most people print a CV, and the only way to do it was to export a PDF, find
 * it, open it and print that -- or press Ctrl+P in the studio and get the
 * chrome instead of the résumé.
 *
 * The geometry assertions are on the text of the stylesheet rather than on a
 * render, for the same reason `deck-layout` is: jsdom parses CSS and lays out
 * nothing, so the bug this pins -- a printed résumé silently scaled to 90.5%
 * -- is invisible to every other test here.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import PrintButton from '@/export/print-button';

const css = readFileSync(resolve(process.cwd(), 'app/globals.css'), 'utf8');

/** The body of `@media print`, comments stripped. */
function printBlock(): string {
  const bare = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const at = bare.indexOf('@media print');
  if (at < 0) return '';
  let depth = 0;
  for (let index = bare.indexOf('{', at); index < bare.length; index += 1) {
    if (bare[index] === '{') depth += 1;
    else if (bare[index] === '}') {
      depth -= 1;
      if (depth === 0) return bare.slice(at, index);
    }
  }
  return bare.slice(at);
}

describe('what the paper contributes', () => {
  it('adds no margin of its own', () => {
    // Every page box already carries one: a canvas page is a full 210x297mm
    // sheet whose frames sit 10mm in, and a flowing page has that 10mm as
    // padding. Letting the browser add another put a 210mm box in a 190mm
    // printable area, so it was scaled to fit -- a 21pt name printed at
    // 19.08pt, and the margins measured 19mm rather than 10mm.
    const page = /@page\s*\{([^}]*)\}/.exec(printBlock());
    expect(page).not.toBeNull();
    expect(page![1]).toMatch(/margin:\s*0/);
    expect(page![1]).toMatch(/size:\s*A4/);
  });

  it('leaves a flowing page its own padding', () => {
    // With the page margin at zero this is the only thing between the words
    // and the edge of the paper. It used to be zeroed here, because the
    // browser was supplying the margin instead.
    const rule = /\.page\s*\{([^}]*)\}/.exec(printBlock());
    expect(rule).not.toBeNull();
    expect(rule![1]).toMatch(/padding:\s*var\(--page-margin\)/);
  });
});

describe('the Print button', () => {
  const onError = vi.fn();
  let printed: string[] = [];

  beforeEach(() => {
    printed = [];
    onError.mockClear();
    // jsdom loads no iframe document, so the frame is driven by hand: this is
    // the contract the component depends on, not a simulation of a browser.
    vi.spyOn(HTMLIFrameElement.prototype, 'contentWindow', 'get').mockReturnValue({
      focus: vi.fn(),
      print: vi.fn(() => printed.push('printed')),
      addEventListener: vi.fn(),
    } as unknown as Window);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.querySelectorAll('iframe').forEach((node) => node.remove());
  });

  const frame = () => document.querySelector<HTMLIFrameElement>('iframe[aria-hidden="true"]');

  it('prints the print route, not the studio', () => {
    // That route is exactly what the PDF export renders, so the page out of a
    // printer and the file out of Export are the same document rather than two
    // renderings kept in step by hand.
    render(<PrintButton documentId="doc-1" onError={onError} />);
    fireEvent.click(screen.getByRole('button', { name: 'Print' }));

    expect(frame()).not.toBeNull();
    expect(frame()!.getAttribute('src')).toBe('/print/doc-1');
  });

  it('asks the frame to print once it has loaded', () => {
    render(<PrintButton documentId="doc-1" onError={onError} />);
    fireEvent.click(screen.getByRole('button', { name: 'Print' }));
    fireEvent.load(frame()!);

    expect(printed).toHaveLength(1);
    // Only the `null` that clears whatever was on screen before.
    expect(onError.mock.calls).toEqual([[null]]);
  });

  it('goes back to idle rather than sticking on Preparing', () => {
    // A browser that reports nothing back once the dialog opens used to leave
    // the button disabled until a timeout fired, and then show "took too long"
    // for a print that had worked.
    render(<PrintButton documentId="doc-1" onError={onError} />);
    const button = screen.getByRole('button', { name: 'Print' });
    fireEvent.click(button);
    expect(screen.getByRole('button', { name: 'Preparing…' })).toBeDisabled();

    fireEvent.load(frame()!);
    expect(screen.getByRole('button', { name: 'Print' })).toBeEnabled();
    expect(onError.mock.calls).toEqual([[null]]);
  });

  it('leaves the frame up while the dialog would still be open', () => {
    // Pulling it out from under an open dialog cancels the job.
    render(<PrintButton documentId="doc-1" onError={onError} />);
    fireEvent.click(screen.getByRole('button', { name: 'Print' }));
    fireEvent.load(frame()!);
    expect(frame()).not.toBeNull();
  });

  it('reports a frame that will not load', () => {
    render(<PrintButton documentId="doc-1" onError={onError} />);
    fireEvent.click(screen.getByRole('button', { name: 'Print' }));
    fireEvent.error(frame()!);

    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/could not be prepared/));
    expect(frame()).toBeNull();
  });

  it('cannot be pressed while it is already preparing one', () => {
    render(<PrintButton documentId="doc-1" onError={onError} />);
    const button = screen.getByRole('button', { name: 'Print' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(document.querySelectorAll('iframe[aria-hidden="true"]')).toHaveLength(1);
  });

  it('is not offered before a document has loaded', () => {
    render(<PrintButton documentId="doc-1" disabled onError={onError} />);
    expect(screen.getByRole('button', { name: 'Print' })).toBeDisabled();
  });

  it('takes its frame with it when the studio unmounts', () => {
    const view = render(<PrintButton documentId="doc-1" onError={onError} />);
    fireEvent.click(screen.getByRole('button', { name: 'Print' }));
    view.unmount();
    expect(frame()).toBeNull();
  });
});
