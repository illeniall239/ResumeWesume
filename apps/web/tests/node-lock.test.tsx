import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';

import DocumentFlow from '@/render/document-flow';
import { useStudio } from '@/store/studio';
import type { StudioDoc } from '@/contracts/doc';

/**
 * A node is closed for editing while the assistant writes into it.
 *
 * The receiving half of this was complete and unreachable: the server declared
 * a `node_lock` event and never sent one, so `locked` was always empty and the
 * `.node--locked` styling never appeared outside a test. The server now emits
 * it as a draft names a place and releases it when the call settles.
 *
 * What these pin is the granularity. A draft is keyed by `nid.field` for a
 * field and by the node id for a text node, and the lock has to be addressable
 * the same way — otherwise closing one field of a job closes the whole job,
 * and the person loses three editable fields to protect one.
 */

const doc: StudioDoc = {
  schema_version: 1,
  template: 'plain',
  layout: 'stack',
  scaffold: false,
  unverified: [],
  personal: {
    name: 'Alex Morgan', title: 'Engineer', email: '', phone: '', location: '',
    website: null, linkedin: null, github: null, photo: null,
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
        { nid: 'blt_bbbbb', text: 'Led the migration.', style: 'bullet' },
      ],
    },
  ],
  education: [],
  projects: [],
  skills: [],
  custom: [],
  sections: [],
  blocks: [],
  pages: [],
  reading_order: null,
};

function flow(locked: string[]) {
  return render(<DocumentFlow doc={doc} editable locked={new Set(locked)} />).container;
}

const editable = (node: Element | null) => node?.getAttribute('contenteditable');

describe('a node the assistant is writing to', () => {
  it('cannot be typed into, and says so in its class', () => {
    const container = flow(['blt_aaaaa']);
    const node = container.querySelector('[data-nid="blt_aaaaa"]');

    expect(editable(node)).toBeNull();
    expect(node?.className).toContain('node--locked');
  });

  it('leaves every other node alone', () => {
    const container = flow(['blt_aaaaa']);

    expect(editable(container.querySelector('[data-nid="blt_bbbbb"]'))).toBe(
      'plaintext-only'
    );
  });
});

describe('a field the assistant is writing to', () => {
  it('is closed by the path the draft uses', () => {
    // `exp_11111.company`, exactly as `Drafting.target` carries it.
    const container = flow(['exp_11111.company']);
    const node = container.querySelector('[data-field="exp_11111.company"]');

    expect(editable(node)).toBeNull();
    expect(node?.className).toContain('node--locked');
  });

  it('does not close the rest of the entry with it', () => {
    const container = flow(['exp_11111.company']);

    expect(editable(container.querySelector('[data-field="exp_11111.title"]'))).toBe(
      'plaintext-only'
    );
    expect(editable(container.querySelector('[data-nid="blt_aaaaa"]'))).toBe(
      'plaintext-only'
    );
  });

  it('still closes the whole entry when the entry itself is named', () => {
    const container = flow(['exp_11111']);

    expect(editable(container.querySelector('[data-field="exp_11111.company"]'))).toBeNull();
  });
});

describe('when the turn ends', () => {
  it('the pen comes off the sheet', () => {
    // The backstop for a stream that dies mid-draft: the server releases every
    // node as its call settles, but nothing can release one if the connection
    // is gone. A node stuck locked reads as a broken editor.
    useStudio.setState({ locked: new Set(['blt_aaaaa']), drafts: new Map() });

    useStudio.getState().clearDrafts();

    expect(useStudio.getState().locked.size).toBe(0);
  });
});
