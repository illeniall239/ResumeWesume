import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import DocumentFlow from '@/render/document-flow';
import type { StudioDoc } from '@/contracts/doc';

const doc: StudioDoc = {
  schema_version: 1,
  template: 'plain',
  layout: 'stack',
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
    github: null, photo: null,
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

  it('lets the summary be edited, not just bullets', () => {
    // Reported as "some text I am not able to edit like summary": only bullets
    // and hand-placed lines were ever editable, so the rest of the resume was
    // reachable through the assistant and nowhere else.
    const onEditText = vi.fn();
    const { container } = render(
      <DocumentFlow doc={doc} editable onEditText={onEditText} />
    );
    const summary = container.querySelector('[data-nid="sum_00001"]') as HTMLElement;

    expect(summary.getAttribute('contenteditable')).toBe('plaintext-only');
    summary.textContent = 'Backend engineer with a payments bias.';
    fireEvent.blur(summary);

    expect(onEditText).toHaveBeenCalledWith(
      'sum_00001',
      'Backend engineer with a payments bias.'
    );
  });

  it('edits an entry heading as a field, not as text', () => {
    // A company is an attribute of the job, not a text node, so it commits
    // `set_field` against `nid.attribute`. Routing it through `set_text` would
    // be rejected: there is no node to address.
    const onEditField = vi.fn();
    const { container } = render(
      <DocumentFlow doc={doc} editable onEditField={onEditField} />
    );
    const company = container.querySelector(
      '[data-field="exp_11111.company"]'
    ) as HTMLElement;

    company.textContent = 'Contoso';
    fireEvent.blur(company);

    expect(onEditField).toHaveBeenCalledWith('exp_11111.company', 'Contoso');
  });

  it.each([
    ['exp_11111.title', 'Job title'],
    ['exp_11111.years', 'Dates'],
    ['exp_11111.location', 'Location'],
    ['personal.name', 'Name'],
    ['personal.email', 'Email'],
    ['sgp_ggggg.label', 'Skill group'],
  ])('makes %s editable', (target) => {
    const { container } = render(<DocumentFlow doc={doc} editable />);
    expect(
      container.querySelector(`[data-field="${target}"]`)?.getAttribute('contenteditable')
    ).toBe('plaintext-only');
  });

  it('edits one skill without retyping the row', () => {
    // The row used to be one joined string, so correcting "Pyhton" meant
    // retyping every skill beside it.
    const onEditText = vi.fn();
    const { container } = render(
      <DocumentFlow doc={doc} editable onEditText={onEditText} />
    );
    const skill = container.querySelector('[data-nid="skl_ggggo"]') as HTMLElement;

    skill.textContent = 'Golang';
    fireEvent.blur(skill);

    expect(onEditText).toHaveBeenCalledWith('skl_ggggo', 'Golang');
  });

  it('never renders a placeholder as content', () => {
    // A placeholder in the DOM is text, and a blur reads text -- so a click in
    // and out of an empty field would commit the hint as the value.
    const blank = { ...doc, personal: { ...doc.personal, title: '' } };
    const onEditField = vi.fn();
    const { container } = render(
      <DocumentFlow doc={blank} editable onEditField={onEditField} />
    );
    const title = container.querySelector('[data-field="personal.title"]') as HTMLElement;

    expect(title.textContent).toBe('');
    expect(title.getAttribute('data-placeholder')).toBeTruthy();
    fireEvent.blur(title);
    expect(onEditField).not.toHaveBeenCalled();
  });

  it('emits no empty editor affordances into the export', () => {
    // A blank employer needs somewhere to click while editing, and nothing at
    // all in the PDF -- an always-rendered row is a blank line where a company
    // would have been.
    const blank = {
      ...doc,
      experience: [{ ...doc.experience[0], company: '', location: '', years: '' }],
    };

    const printed = render(<DocumentFlow doc={blank} />);
    expect(printed.container.querySelector('.entry__org')).toBeNull();
    expect(printed.container.querySelector('.entry__meta')).toBeNull();

    const editing = render(<DocumentFlow doc={blank} editable />);
    expect(editing.container.querySelector('.entry__org')).not.toBeNull();
    expect(editing.container.querySelector('.entry__meta')).not.toBeNull();
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

  it('joins short skills into one dense line', () => {
    // The shape an ATS parses most reliably, and the reason stacking is
    // conditional rather than the default.
    const { container } = render(<DocumentFlow doc={doc} />);
    const row = container.querySelector('.skills__row');
    expect(row?.textContent).toContain('Python, Go');
    expect(container.querySelector('.skills__list')).toBeNull();
  });

  it('stacks entries that contain their own commas', () => {
    // A real certifications section. Comma-joining these makes the boundary
    // between two credentials indistinguishable from the comma inside one.
    const certs: StudioDoc = {
      ...doc,
      skills: [
        {
          nid: 'sgp_certs',
          key: 'certifications',
          label: 'Certifications',
          items: [
            {
              nid: 'skl_c1',
              text: 'IBM - Python for Data Science, AI & Development - coursera.org/verify/4FY',
              source: 'original',
            },
            {
              nid: 'skl_c2',
              text: 'Databases and SQL for Data Science with Python - coursera.org/verify/CKE',
              source: 'original',
            },
          ],
        },
      ],
    };
    const { container } = render(<DocumentFlow doc={certs} />);
    const items = container.querySelectorAll('.skills__list li');
    expect(items).toHaveLength(2);
    expect(items[0].getAttribute('data-nid')).toBe('skl_c1');
    expect(items[1].textContent).toContain('Databases and SQL');
  });
});
