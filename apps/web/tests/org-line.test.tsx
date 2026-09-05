/**
 * The employer line, and the comma that made a hint read as a fact.
 *
 * An imported résumé with no location showed "L'Oréal Paris, Location" — a word
 * nobody typed, in the place a word belongs, punctuated as though it were the
 * résumé's own. The hint alone is muted and italic and reads as a prompt; the
 * comma beside it was ordinary text, and that is what made it look like an
 * address.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import DocumentFlow from '@/render/document-flow';
import type { StudioDoc } from '@/contracts/doc';

function withEntry(over: Record<string, unknown>): StudioDoc {
  return {
    schema_version: 2,
    template: 'plain',
  layout: 'stack',
    scaffold: false,
    unverified: [],
    personal: { name: 'Rao', title: '', email: '', phone: '', location: '',
                website: null, linkedin: null, github: null },
    summary: null,
    experience: [
      {
        nid: 'exp_11111',
        title: 'Business Planning Analyst',
        company: "L'Oréal Paris",
        location: '',
        years: '2022',
        bullets: [],
        ...over,
      },
    ],
    education: [], projects: [], skills: [], custom: [], sections: [],
    blocks: [], pages: [], reading_order: null,
  } as unknown as StudioDoc;
}

describe('the employer line', () => {
  it('never puts the word "Location" in an exported résumé', () => {
    // The print path passes neither `editable` nor `placeholders`.
    const { container } = render(<DocumentFlow doc={withEntry({})} />);

    expect(container.textContent).not.toContain('Location');
    // And no orphaned comma where a location would have been.
    expect(container.querySelector('.entry__org')?.textContent?.trim()).toBe(
      "L'Oréal Paris"
    );
  });

  it('joins two real values with a real comma', () => {
    const { container } = render(
      <DocumentFlow doc={withEntry({ location: 'Paris' })} editable placeholders />
    );

    const org = container.querySelector('.entry__org');
    expect(org?.textContent).toBe("L'Oréal Paris, Paris");
    // Real content, so no hint styling anywhere on that line.
    expect(org?.querySelector('.hint')).toBeNull();
  });

  it('marks the comma as a hint when it joins one', () => {
    const { container } = render(<DocumentFlow doc={withEntry({})} editable placeholders />);

    const org = container.querySelector('.entry__org');
    // The separator is still drawn -- without it the hint would sit flush
    // against the employer -- but it is styled as the hint it belongs to.
    expect(org?.querySelector('.hint')?.textContent).toBe(', ');
  });

  it('still offers somewhere to click for a missing location', () => {
    const { container } = render(<DocumentFlow doc={withEntry({})} editable placeholders />);

    const location = container.querySelector('[data-field="exp_11111.location"]');
    expect(location).toBeTruthy();
    expect(location?.getAttribute('data-placeholder')).toBe('Location');
    // The hint is drawn by CSS, never present as text an edit could commit.
    expect(location?.textContent).toBe('');
  });
});

/**
 * Whether those hints are drawn at rest is a separate question from whether
 * they exist, and `flow--prompting` is the answer to it. jsdom applies no
 * stylesheet, so what is checked here is the switch the stylesheet reads; the
 * drawing itself is checked in a browser.
 */
function withSkills(label: string): StudioDoc {
  return {
    ...withEntry({}),
    experience: [],
    sections: [{ key: 'skills', label: 'Skills', order: 0 }],
    skills: [
      {
        nid: 'skg_11111',
        key: 'technical',
        label,
        items: [{ nid: 'skl_11111', text: 'Python', source: 'original' }],
      },
    ],
  } as unknown as StudioDoc;
}

/**
 * The colon after a skills group label is the same defect as the comma: real
 * punctuation welded to an empty field, which makes the hint beside it read as
 * the résumé's own word.
 */
describe('the skills group label', () => {
  it('uses a real colon after a real label', () => {
    const { container } = render(
      <DocumentFlow doc={withSkills('Technical Skills')} editable placeholders />
    );

    const row = container.querySelector('.skills__row');
    expect(row?.textContent).toContain('Technical Skills:');
    expect(row?.querySelector('.hint')).toBeNull();
  });

  it('marks the colon as a hint when the label is empty', () => {
    const { container } = render(
      <DocumentFlow doc={withSkills('')} editable placeholders />
    );

    expect(container.querySelector('.skills__row .hint')?.textContent).toBe(':');
  });

  it('leaves no orphaned colon in an export', () => {
    const { container } = render(<DocumentFlow doc={withSkills('')} />);

    expect(container.querySelector('.skills__row')?.textContent).not.toContain(':');
    expect(container.textContent).not.toContain('Group');
  });
});

describe('prompting', () => {
  it('marks a document that is still a form', () => {
    const { container } = render(
      <DocumentFlow doc={withEntry({})} editable placeholders prompting />
    );

    expect(container.querySelector('.flow')?.className).toContain('flow--prompting');
  });

  it('does not mark a résumé that came from an upload', () => {
    // What import produces: hints exist and are reachable, but nothing is
    // drawn until the pointer or the caret is in that part of the document.
    const { container } = render(
      <DocumentFlow doc={withEntry({})} editable placeholders />
    );

    const flow = container.querySelector('.flow');
    expect(flow?.className).not.toContain('flow--prompting');
    // The field is still there to be revealed.
    expect(container.querySelector('[data-field="exp_11111.location"]')).toBeTruthy();
  });

  it('never marks the export', () => {
    const { container } = render(<DocumentFlow doc={withEntry({})} />);

    expect(container.querySelector('.flow')?.className).not.toContain('flow--prompting');
  });
});

function withCustom(): StudioDoc {
  return {
    ...withEntry({}),
    experience: [],
    custom: [
      {
        nid: 'cus_11111',
        key: 'PUBLICATIONS',
        label: 'PUBLICATIONS',
        kind: 'text',
        text: { nid: 'blt_99999', text: 'Kowalski, A. (2024).', style: 'plain' },
        items: [],
        strings: [],
      },
    ],
    education: [
      { nid: 'edu_11111', institution: 'LUMS', degree: 'BSc', years: '', detail: null },
    ],
    sections: [
      { key: 'experience', label: 'Experience', visible: true, order: 0 },
      { key: 'PUBLICATIONS', label: 'PUBLICATIONS', visible: true, order: 1 },
      { key: 'education', label: 'Education', visible: true, order: 2 },
    ],
  } as unknown as StudioDoc;
}

/**
 * A section the app has no schema for is named by the résumé's own heading and
 * keeps the place the résumé gave it. Rendering these after the modelled
 * sections would reshuffle somebody's document on the way in.
 */
describe('sections we have no schema for', () => {
  it('renders in the place the source put it', () => {
    const { container } = render(<DocumentFlow doc={withCustom()} />);

    const headings = [...container.querySelectorAll('.section__title')].map(
      (h) => h.textContent
    );
    expect(headings).toContain('PUBLICATIONS');
    expect(headings.indexOf('PUBLICATIONS')).toBeLessThan(
      headings.indexOf('Education')
    );
  });

  it('carries its content', () => {
    const { container } = render(<DocumentFlow doc={withCustom()} />);

    expect(container.textContent).toContain('Kowalski, A. (2024).');
  });

  it('renders one that the section order never mentions', () => {
    // A custom section added after import has no `sectionMeta` row of its own.
    const doc = withCustom();
    const orphaned = {
      ...doc,
      sections: [{ key: 'education', label: 'Education', visible: true, order: 0 }],
    } as unknown as StudioDoc;
    const { container } = render(<DocumentFlow doc={orphaned} />);

    expect(container.textContent).toContain('Kowalski, A. (2024).');
  });

  it('renders it exactly once', () => {
    // Placed by the order *and* appended by the tail would duplicate it.
    const { container } = render(<DocumentFlow doc={withCustom()} />);

    const headings = [...container.querySelectorAll('.section__title')]
      .map((h) => h.textContent)
      .filter((text) => text === 'PUBLICATIONS');
    expect(headings).toHaveLength(1);
  });
});
