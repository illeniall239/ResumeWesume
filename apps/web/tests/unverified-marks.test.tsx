/**
 * Marking a claim only the job posting vouches for.
 *
 * The renderer had the class and the store had the set, and between them the
 * mark reached exactly one kind of node: the summary. Every bullet, every
 * skill and every custom entry built its `Editable` without passing
 * `unverified` through — which is precisely the set of nodes an agent writes,
 * so the one feature that needs this could never have shown anything.
 *
 * Hence a test per node kind rather than one representative: the bug was that
 * they were each wired separately.
 */

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DocumentFlow } from '@/render/document-flow';
import type { StudioDoc } from '@/contracts/doc';

const DOC = {
  schema_version: 1,
  scaffold: false,
  unverified: ['skl_rst05', 'blt_aaaaa', 'sum_00001'],
  personal: { name: 'Rao Muhammad Hamza', email: 'h@example.com' },
  summary: { nid: 'sum_00001', text: 'Backend engineer.', style: 'plain' },
  experience: [
    {
      nid: 'exp_11111',
      title: 'Senior Engineer',
      company: 'Northwind',
      years: '2021 - Present',
      bullets: [
        { nid: 'blt_aaaaa', text: 'Ran the incident rota.', style: 'bullet' },
        { nid: 'blt_bbbbb', text: 'Rebuilt the ledger.', style: 'bullet' },
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
        { nid: 'skl_ppppp', text: 'Python' },
        { nid: 'skl_rst05', text: 'Rust' },
      ],
    },
  ],
  custom: [],
  sections: ['summary', 'experience', 'skills'],
  pages: [],
} as unknown as StudioDoc;

function marked(unverified: string[]) {
  const { container } = render(
    <DocumentFlow doc={DOC} unverified={new Set(unverified)} />
  );
  return [...container.querySelectorAll('.node--unverified')].map((node) =>
    node.textContent?.trim()
  );
}

describe('what carries the mark', () => {
  it('marks a skill', () => {
    // The node kind this feature exists for: `add_skill(evidence="jd")` puts a
    // word on the page that only the advert vouches for.
    expect(marked(['skl_rst05'])).toEqual(['Rust']);
  });

  it('marks a bullet', () => {
    expect(marked(['blt_aaaaa'])).toEqual(['Ran the incident rota.']);
  });

  it('marks the summary', () => {
    expect(marked(['sum_00001'])).toEqual(['Backend engineer.']);
  });

  it('marks several at once, across kinds', () => {
    expect(marked(['skl_rst05', 'blt_aaaaa'])).toHaveLength(2);
  });

  it('marks nothing when nothing is unverified', () => {
    expect(marked([])).toEqual([]);
  });

  it('leaves the lines beside a marked one alone', () => {
    // The mark is a statement about one claim. Python and the other bullet were
    // never in doubt, and a mark that spread would say they were.
    const { container } = render(
      <DocumentFlow doc={DOC} unverified={new Set(['skl_rst05'])} />
    );
    const python = [...container.querySelectorAll('[data-nid="skl_ppppp"]')][0];
    const other = [...container.querySelectorAll('[data-nid="blt_bbbbb"]')][0];
    expect(python.className).not.toContain('node--unverified');
    expect(other.className).not.toContain('node--unverified');
  });
});
