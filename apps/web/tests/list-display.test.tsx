import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import DocumentFlow from '@/render/document-flow';
import type { ListDisplay, SkillItem, StudioDoc } from '@/contracts/doc';

/**
 * The shape of a run of short items.
 *
 * Whether a skills group is one comma-separated line or a bulleted list used to
 * be decided here and only here, by looking at the words: an item carrying a
 * comma or running past 48 characters stacked the group, everything else was
 * joined. Nothing in the document could say otherwise, so "list my technical
 * skills as bullets" had no answer, and the only way to get a list was to write
 * an item long enough to trip the test.
 *
 * The heuristic is still right as a default and is what `auto` now means. What
 * these cover is that a document which does say gets what it asked for — and
 * that a certifications list, which is the same widget and had no stacked form
 * at all, is drawn by the same rule.
 */

const SHORT: SkillItem[] = [
  { nid: 'skl_ppppp', text: 'Python', source: 'original' },
  { nid: 'skl_gggga', text: 'Go', source: 'original' },
];

const LONG: SkillItem[] = [
  { nid: 'skl_ccccc', text: 'IBM Data Analysis with Python, Professional', source: 'original' },
  { nid: 'skl_ddddd', text: 'AWS Solutions Architect, Associate', source: 'original' },
];

function docWith(options: {
  skills?: { items: SkillItem[]; display?: ListDisplay };
  certs?: { items: SkillItem[]; display?: ListDisplay };
}): StudioDoc {
  return {
    schema_version: 1,
    template: 'plain',
    layout: 'stack',
    scaffold: false,
    unverified: [],
    personal: {
      name: 'Alex Morgan', title: '', email: '', phone: '', location: '',
      website: null, linkedin: null, github: null, photo: null,
    },
    summary: null,
    experience: [],
    education: [],
    projects: [],
    skills: options.skills
      ? [{
          nid: 'sgp_aaaaa',
          key: 'technical',
          label: 'Technical Skills',
          items: options.skills.items,
          display: options.skills.display,
        }]
      : [],
    custom: options.certs
      ? [{
          nid: 'cst_aaaaa',
          key: 'certifications',
          label: 'Certifications',
          kind: 'stringList',
          text: null,
          items: [],
          strings: options.certs.items,
          display: options.certs.display,
        }]
      : [],
    sections: [],
    blocks: [],
    pages: [],
    reading_order: null,
  };
}

/** A skill drawn as a list item, rather than a run inside a line. */
function listItem(nid: string): HTMLElement | null {
  return document.querySelector(`li[data-nid="${nid}"]`);
}

describe('how a run of short items is set', () => {
  it('joins short skills into a line when the document says nothing', () => {
    render(<DocumentFlow doc={docWith({ skills: { items: SHORT } })} />);

    expect(listItem('skl_ppppp')).toBeNull();
    expect(screen.getByText('Python')).toBeInTheDocument();
  });

  it('stacks short skills when the document asks for a list', () => {
    // The reported request, and what had no answer at all: these items are
    // short and carry no commas, so every heuristic there was said "line".
    render(<DocumentFlow doc={docWith({ skills: { items: SHORT, display: 'list' } })} />);

    expect(listItem('skl_ppppp')).not.toBeNull();
    expect(listItem('skl_gggga')).not.toBeNull();
  });

  it('joins long skills into a line when the document insists', () => {
    // The author overrules the default in both directions. Guessing better
    // than the person whose résumé it is was the whole problem.
    render(<DocumentFlow doc={docWith({ skills: { items: LONG, display: 'inline' } })} />);

    expect(listItem('skl_ccccc')).toBeNull();
  });

  it('still stacks credentials nobody has said anything about', () => {
    render(<DocumentFlow doc={docWith({ skills: { items: LONG } })} />);

    expect(listItem('skl_ccccc')).not.toBeNull();
  });
});

describe('a certifications section', () => {
  it('stacks its lines instead of joining them with commas', () => {
    // It had no stacked form: `strings` were always comma-joined, which is
    // exactly the run of text the stacking rule exists to prevent — one
    // credential ending is indistinguishable from the comma inside another.
    render(<DocumentFlow doc={docWith({ certs: { items: LONG } })} />);

    expect(listItem('skl_ccccc')).not.toBeNull();
  });

  it('takes the same instruction as a skills group', () => {
    render(<DocumentFlow doc={docWith({ certs: { items: SHORT, display: 'list' } })} />);

    expect(listItem('skl_ppppp')).not.toBeNull();
  });

  it('joins them when asked to', () => {
    render(<DocumentFlow doc={docWith({ certs: { items: LONG, display: 'inline' } })} />);

    expect(listItem('skl_ccccc')).toBeNull();
    expect(screen.getByText(/AWS Solutions Architect/)).toBeInTheDocument();
  });
});
