import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import DocumentFlow from '@/render/document-flow';
import type { StudioDoc } from '@/contracts/doc';

const doc: StudioDoc = {
  schema_version: 1,
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
};

describe('DocumentFlow', () => {
  it('renders the resume content', () => {
    render(<DocumentFlow doc={doc} />);
    expect(screen.getByText('Alex Morgan')).toBeInTheDocument();
    expect(screen.getByText('Senior Engineer')).toBeInTheDocument();
    expect(screen.getByText('Rebuilt the ledger.')).toBeInTheDocument();
  });

  it('leads each entry with the job title, then the employer', () => {
    // ATS parsers key on the title before the company; this ordering is the
    // point of the layout, so it is asserted rather than assumed.
    const { container } = render(<DocumentFlow doc={doc} />);
    const text = container.textContent ?? '';
    expect(text.indexOf('Senior Engineer')).toBeLessThan(text.indexOf('Northwind'));
  });

  it('carries the print root Chromium waits for', () => {
    // Without this attribute the PDF export hangs until its navigation timeout
    // rather than failing fast.
    const { container } = render(<DocumentFlow doc={doc} />);
    expect(container.querySelector('[data-print-root]')).not.toBeNull();
  });

  it('gives every node an addressable id in the DOM', () => {
    const { container } = render(<DocumentFlow doc={doc} />);
    expect(container.querySelector('[data-nid="blt_aaaaa"]')).not.toBeNull();
    expect(container.querySelector('[data-nid="exp_11111"]')).not.toBeNull();
  });

  it('styles bullets from the node, with no parallel array', () => {
    const { container } = render(<DocumentFlow doc={doc} />);
    expect(container.querySelector('[data-nid="blt_aaaaa"]')?.className).toContain(
      'bullet--bullet'
    );
    expect(container.querySelector('[data-nid="blt_bbbbb"]')?.className).toContain(
      'bullet--plain'
    );
  });

  it('is read-only unless editing is enabled', () => {
    const { container } = render(<DocumentFlow doc={doc} />);
    expect(
      container.querySelector('[data-nid="blt_aaaaa"]')?.getAttribute('contenteditable')
    ).toBeNull();
  });

  it('emits an edit only when the text actually changed', () => {
    const onEditText = vi.fn();
    const { container } = render(
      <DocumentFlow doc={doc} editable onEditText={onEditText} />
    );
    const bullet = container.querySelector('[data-nid="blt_aaaaa"]') as HTMLElement;

    // Focus and blur with no change must not produce a write, or every stray
    // click would burn a document version.
    fireEvent.blur(bullet);
    expect(onEditText).not.toHaveBeenCalled();

    bullet.textContent = 'Cut settlement latency 96%.';
    fireEvent.blur(bullet);
    expect(onEditText).toHaveBeenCalledWith('blt_aaaaa', 'Cut settlement latency 96%.');
  });

  it('blocks editing on a node the agent is writing to', () => {
    const { container } = render(
      <DocumentFlow doc={doc} editable locked={new Set(['blt_aaaaa'])} />
    );
    expect(
      container.querySelector('[data-nid="blt_aaaaa"]')?.getAttribute('contenteditable')
    ).toBeNull();
    // Everything else stays editable: the lock is per node, not global.
    expect(
      container.querySelector('[data-nid="blt_bbbbb"]')?.getAttribute('contenteditable')
    ).toBe('plaintext-only');
  });

  it('marks changed nodes so an agent edit is legible', () => {
    const { container } = render(
      <DocumentFlow doc={doc} changed={new Set(['blt_bbbbb'])} />
    );
    expect(container.querySelector('[data-nid="blt_bbbbb"]')?.className).toContain(
      'node--changed'
    );
  });

  it('renders an empty document without crashing', () => {
    const empty: StudioDoc = { ...doc, summary: null, experience: [], skills: [] };
    const { container } = render(<DocumentFlow doc={empty} />);
    expect(container.querySelector('[data-print-root]')).not.toBeNull();
  });
});
