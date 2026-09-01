/**
 * The controls beside a page.
 *
 * The rule they express: deleting a page takes its frames with it, so a page
 * holding the only frame for part of the resume cannot go — the engine's
 * coverage gate would refuse the batch. What is asserted here is that the
 * *affordance* matches the rule, because a delete button that is permanently
 * disabled on every page of a normal resume reads as broken rather than as
 * "not applicable".
 */

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { PageNode, StudioDoc } from '@/contracts/doc';
import { PageCanvas } from '@/canvas/page-canvas';

function frame(nid: string, ref: string) {
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
    },
  };
}

function page(nid: string, elements: unknown[] = []): PageNode {
  return {
    nid,
    size: 'A4',
    orientation: 'portrait',
    background: null,
    elements: elements as PageNode['elements'],
  };
}

function docOf(...pages: PageNode[]): StudioDoc {
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
    pages,
    reading_order: null,
  } as unknown as StudioDoc;
}

function deletes(container: HTMLElement): HTMLButtonElement[] {
  return [...container.querySelectorAll<HTMLButtonElement>('.page-controls__delete')];
}

describe('the delete-page control', () => {
  it('appears on a page that can actually be deleted', () => {
    const doc = docOf(page('pag_aaaaa', [frame('frm_sum00', 'summary')]), page('pag_blank'));
    const { container } = render(<PageCanvas doc={doc} interactive commit={() => {}} />);

    const buttons = deletes(container);
    expect(buttons).toHaveLength(1);
    expect(buttons[0].disabled).toBe(false);
  });

  it('is absent where it could only ever be disabled', () => {
    // Both pages hold the only frame for something, which is every page of an
    // ordinary resume -- and was a dead ✕ on all of them.
    const doc = docOf(
      page('pag_aaaaa', [frame('frm_sum00', 'summary')]),
      page('pag_bbbbb', [frame('frm_exp00', 'experience')])
    );
    const { container } = render(<PageCanvas doc={doc} interactive commit={() => {}} />);

    expect(deletes(container)).toHaveLength(0);
  });

  it('is absent on a lone page, which a document always needs', () => {
    const doc = docOf(page('pag_aaaaa', [frame('frm_sum00', 'summary')]));
    const { container } = render(<PageCanvas doc={doc} interactive commit={() => {}} />);

    expect(deletes(container)).toHaveLength(0);
  });

  it('draws no page controls at all on the print surface', () => {
    const doc = docOf(page('pag_aaaaa', [frame('frm_sum00', 'summary')]), page('pag_blank'));
    const { container } = render(<PageCanvas doc={doc} />);

    expect(container.querySelectorAll('.page-controls')).toHaveLength(0);
  });
});
