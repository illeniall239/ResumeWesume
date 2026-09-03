/**
 * Showing an edit as it is written.
 *
 * A tool call arrives as a stream of JSON fragments, and by the time it
 * balances the edit lands all at once. Everything needed to show it arriving
 * was on the wire a second earlier, so the words appear as they are typed.
 *
 * A draft is a picture, never a change: nothing is in the document, no op has
 * been compiled, and a call that never balances leaves the page as it was.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import DocumentFlow from '@/render/document-flow';
import type { StudioDoc } from '@/contracts/doc';

const doc: StudioDoc = {
  schema_version: 1,
  template: 'plain',
  scaffold: false,
  unverified: [],
  personal: {
    name: 'Alex Morgan',
    title: 'Senior Backend Engineer',
    email: 'alex@example.com',
    phone: '+1-555-0142',
    location: 'Austin, TX',
    website: null,
    linkedin: null,
    github: null,
  },
  summary: { nid: 'sum_00001', text: 'Backend engineer.', style: 'plain' },
  experience: [
    {
      nid: 'exp_11111',
      title: 'Senior Engineer',
      company: 'Northwind',
      location: 'Austin, TX',
      years: '2021 - Present',
      bullets: [
        { nid: 'blt_aaaaa', text: 'Rebuilt the ledger.', style: 'bullet' },
        { nid: 'blt_bbbbb', text: 'Led the migration.', style: 'plain' },
      ],
    },
  ],
  education: [],
  projects: [],
  skills: [
    {
      nid: 'sgp_ggggg',
      key: 'technical',
      label: 'Technical Skills',
      items: [
        { nid: 'skl_ppppp', text: 'Python', source: 'original' },
        { nid: 'skl_ggggo', text: 'Go', source: 'original' },
      ],
    },
  ],
  custom: [],
  sections: [],
  blocks: [],
  pages: [],
  reading_order: null,
};

describe('a draft in flight', () => {
  it('is shown in place of the stored text', () => {
    render(
      <DocumentFlow
        doc={doc}
        drafts={new Map([['blt_aaaaa', 'Cut settlement laten']])}
      />
    );

    expect(screen.getByText('Cut settlement laten')).toBeTruthy();
    expect(screen.queryByText('Rebuilt the ledger.')).toBeNull();
  });

  it('leaves every other line alone', () => {
    render(
      <DocumentFlow
        doc={doc}
        drafts={new Map([['blt_aaaaa', 'Cut settlement laten']])}
      />
    );

    expect(screen.getByText('Backend engineer.')).toBeTruthy();
  });

  it('reaches a field as well as a node', () => {
    // `set_personal_info` writes `personal.title`, not a node id.
    render(
      <DocumentFlow doc={doc} drafts={new Map([['personal.title', 'AI Engine']])} />
    );

    expect(screen.getByText('AI Engine')).toBeTruthy();
  });

  it('never appears when nothing is being written', () => {
    render(<DocumentFlow doc={doc} />);

    expect(screen.getByText('Rebuilt the ledger.')).toBeTruthy();
  });

  it('cannot reach the printed page', () => {
    // The same renderer draws the PDF. A half-written sentence must never be
    // exported, which is why drafts are a prop rather than read from the store:
    // the print path simply does not pass one.
    const { container } = render(<DocumentFlow doc={doc} />);

    expect(container.querySelectorAll('.node--drafting')).toHaveLength(0);
    expect(screen.getByText('Rebuilt the ledger.')).toBeTruthy();
  });

  it('marks the line it is writing, so the caret can be drawn', () => {
    const { container } = render(
      <DocumentFlow doc={doc} drafts={new Map([['blt_aaaaa', 'Cut set']])} />
    );

    expect(container.querySelectorAll('.node--drafting')).toHaveLength(1);
  });
});
