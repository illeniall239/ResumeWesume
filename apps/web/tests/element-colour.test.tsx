/**
 * Colour on a placed box.
 *
 * `ElementStyle` has carried `color` and `background` since it was written and
 * the canvas drew neither, so a box the assistant was told to make red came
 * back looking exactly as it did before while every layer underneath reported
 * success. `align` sat in the same state until a footer that could not be
 * pushed to the right edge gave it away.
 *
 * The subtlety worth pinning is which property carries it. `.flow` in
 * globals.css declares `color: var(--ink)` outright, so a colour merely
 * inherited from the box is overridden the instant DocumentFlow renders inside
 * it -- the box goes red and the words stay black. Rebinding the document's
 * own ink token is what actually reaches the text, and it takes the section
 * rules and heading underlines with it, which is what a person asking for a
 * red section means.
 */

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { PageNode, StudioDoc } from '@/contracts/doc';
import { PageCanvas } from '@/canvas/page-canvas';

function frame(nid: string, ref: string, style: Record<string, unknown> = {}) {
  return {
    nid,
    ref,
    rect: { x: 0, y: 0, w: 400, h: 60 },
    rotation: 0,
    autogrow: 'height' as const,
    visible: true,
    locked: false,
    style: {
      align: 'left' as const,
      font_scale: 1,
      color: null,
      background: null,
      padding: 0,
      radius: 0,
      opacity: 1,
      ...style,
    },
  };
}

function docOf(...elements: unknown[]): StudioDoc {
  const page: PageNode = {
    nid: 'pag_aaaaa',
    size: 'A4',
    orientation: 'portrait',
    background: null,
    elements: elements as PageNode['elements'],
  };
  return {
    schema_version: 2,
    personal: { name: 'Alex', title: '', email: '', phone: '', location: '' },
    summary: { nid: 'sum_00001', text: 'Engineer.', style: 'plain' },
    experience: [],
    education: [],
    projects: [],
    skills: [],
    custom: [],
    sections: [],
    blocks: [],
    pages: [page],
    reading_order: null,
  } as unknown as StudioDoc;
}

const box = (container: HTMLElement) =>
  container.querySelector('[data-element="frm_sum00"]') as HTMLElement;

describe('colour on a box', () => {
  it('is not painted when nothing asked for one', () => {
    const { container } = render(<PageCanvas doc={docOf(frame('frm_sum00', 'summary'))} />);

    expect(box(container).style.color).toBe('');
    expect(box(container).style.background).toBe('');
  });

  it('rebinds the document ink, which is what reaches the words', () => {
    // Not merely `color`. `.flow` sets `color: var(--ink)` of its own, so the
    // token is the only one of the two that survives into the résumé's markup.
    const { container } = render(
      <PageCanvas doc={docOf(frame('frm_sum00', 'summary', { color: '#b91c1c' }))} />
    );

    expect(box(container).style.getPropertyValue('--ink')).toBe('#b91c1c');
    expect(box(container).style.color).toBe('rgb(185, 28, 28)');
  });

  it('paints a background where one was asked for', () => {
    const { container } = render(
      <PageCanvas doc={docOf(frame('frm_sum00', 'summary', { background: 'beige' }))} />
    );

    expect(box(container).style.background).toBe('beige');
  });

  it('leaves the ink alone when only a background was asked for', () => {
    // A band behind a section is not a request to recolour its text.
    const { container } = render(
      <PageCanvas doc={docOf(frame('frm_sum00', 'summary', { background: 'beige' }))} />
    );

    expect(box(container).style.getPropertyValue('--ink')).toBe('');
  });

  it('colours one box without reaching the one beside it', () => {
    const { container } = render(
      <PageCanvas
        doc={docOf(
          frame('frm_sum00', 'summary', { color: 'navy' }),
          frame('frm_exp00', 'experience')
        )}
      />
    );

    const other = container.querySelector('[data-element="frm_exp00"]') as HTMLElement;
    expect(box(container).style.getPropertyValue('--ink')).toBe('navy');
    expect(other.style.getPropertyValue('--ink')).toBe('');
  });
});
