import { describe, expect, it } from 'vitest';

import { applyOp, applyOps } from '@/doc/apply';
import type { DocOp, StudioDoc } from '@/contracts/doc';

const BULLET_A = 'blt_aaaaa';
const BULLET_B = 'blt_bbbbb';
const EXP = 'exp_11111';
const GROUP = 'sgp_ggggg';

function doc(): StudioDoc {
  return {
    schema_version: 1,
    personal: {
      name: 'Alex Morgan',
      title: 'Engineer',
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
        nid: EXP,
        title: 'Senior Engineer',
        company: 'Northwind',
        location: 'Austin, TX',
        years: '2021 - Present',
        bullets: [
          { nid: BULLET_A, text: 'Rebuilt the ledger.', style: 'bullet' },
          { nid: BULLET_B, text: 'Led the migration.', style: 'bullet' },
        ],
      },
    ],
    education: [],
    projects: [],
    skills: [
      {
        nid: GROUP,
        key: 'technical',
        label: 'Technical Skills',
        items: [
          { nid: 'skl_ppppp', text: 'Python', source: 'original' },
          { nid: 'skl_ggggo', text: 'Go', source: 'original' },
        ],
      },
    ],
    custom: [],
    sections: [{ key: 'summary', label: 'Summary', visible: true, order: 0 }],
    // No pages: a flowing document, which is what these op tests are about.
    blocks: [],
    pages: [],
    reading_order: null,
  };
}

describe('client op mirror', () => {
  it('does not mutate its input', () => {
    // The store swaps in the returned object; mutating in place would defeat
    // React's change detection and the document would stop updating.
    const original = doc();
    const snapshot = JSON.stringify(original);
    applyOp(original, { op: 'set_text', nid: BULLET_A, value: 'Changed.' });
    expect(JSON.stringify(original)).toBe(snapshot);
  });

  it('sets bullet text', () => {
    const next = applyOp(doc(), { op: 'set_text', nid: BULLET_A, value: 'Cut latency 96%.' });
    expect(next.experience[0].bullets[0].text).toBe('Cut latency 96%.');
  });

  it('sets the summary', () => {
    const next = applyOp(doc(), { op: 'set_text', nid: 'sum_00001', value: 'New summary.' });
    expect(next.summary?.text).toBe('New summary.');
  });

  it('sets a skill', () => {
    const next = applyOp(doc(), { op: 'set_text', nid: 'skl_ppppp', value: 'Python 3' });
    expect(next.skills[0].items[0].text).toBe('Python 3');
  });

  it('sets a personal field', () => {
    const next = applyOp(doc(), {
      op: 'set_field',
      target: 'personal.email',
      value: 'new@example.com',
    });
    expect(next.personal.email).toBe('new@example.com');
  });

  it('sets an entry field', () => {
    const next = applyOp(doc(), {
      op: 'set_field',
      target: `${EXP}.years`,
      value: '2020 - Present',
    });
    expect(next.experience[0].years).toBe('2020 - Present');
  });

  it('removes a bullet', () => {
    const next = applyOp(doc(), { op: 'remove_node', nid: BULLET_A });
    expect(next.experience[0].bullets.map((b) => b.nid)).toEqual([BULLET_B]);
  });

  it('removes a skill', () => {
    const next = applyOp(doc(), { op: 'remove_node', nid: 'skl_ppppp' });
    expect(next.skills[0].items.map((i) => i.text)).toEqual(['Go']);
  });

  it('inserts a bullet', () => {
    const next = applyOp(doc(), {
      op: 'insert_node',
      parent: EXP,
      index: -1,
      node: { nid: 'blt_ccccc', text: 'New.', style: 'bullet' },
    });
    expect(next.experience[0].bullets).toHaveLength(3);
    expect(next.experience[0].bullets[2].text).toBe('New.');
  });

  it('reorders bullets', () => {
    const next = applyOp(doc(), {
      op: 'reorder',
      parent: EXP,
      order: [BULLET_B, BULLET_A],
    });
    expect(next.experience[0].bullets.map((b) => b.nid)).toEqual([BULLET_B, BULLET_A]);
  });

  it('mirrors the server salvage rule on reorder', () => {
    // Unknown ids dropped, omitted ones appended, so nothing is ever lost.
    const next = applyOp(doc(), {
      op: 'reorder',
      parent: EXP,
      order: ['blt_ghost', BULLET_B],
    });
    expect(next.experience[0].bullets.map((b) => b.nid)).toEqual([BULLET_B, BULLET_A]);
  });

  it('sets bullet style', () => {
    const next = applyOp(doc(), { op: 'set_style', nid: BULLET_A, style: 'plain' });
    expect(next.experience[0].bullets[0].style).toBe('plain');
  });

  it('sets section visibility', () => {
    const next = applyOp(doc(), { op: 'set_section', key: 'summary', visible: false });
    expect(next.sections[0].visible).toBe(false);
  });

  it('returns the original when an op cannot be mirrored', () => {
    // Signals "nothing changed" so the caller waits for the reconciling
    // refetch instead of showing a half-applied document.
    const original = doc();
    expect(applyOp(original, { op: 'set_text', nid: 'blt_ghost', value: 'x' })).toBe(
      original
    );
    expect(applyOp(original, { op: 'remove_node', nid: 'blt_ghost' })).toBe(original);
    expect(
      applyOp(original, { op: 'insert_node', parent: 'nowhere', index: 0, node: {} })
    ).toBe(original);
  });

  it('applies a batch in order', () => {
    const next = applyOps(doc(), [
      { op: 'set_text', nid: BULLET_A, value: 'First.' },
      { op: 'set_text', nid: BULLET_B, value: 'Second.' },
      { op: 'remove_node', nid: 'skl_ggggo' },
    ]);
    expect(next.experience[0].bullets[0].text).toBe('First.');
    expect(next.experience[0].bullets[1].text).toBe('Second.');
    expect(next.skills[0].items.map((i) => i.text)).toEqual(['Python']);
  });
});

/** The same document, with a page and one frame on it. */
function pagedDoc(): StudioDoc {
  const base = doc();
  base.schema_version = 2;
  base.blocks = [];
  base.pages = [
    {
      nid: 'pag_aaaaa',
      size: 'A4',
      orientation: 'portrait',
      background: null,
      elements: [
        {
          nid: 'frm_aaaaa',
          ref: 'experience',
          rect: { x: 28, y: 28, w: 538, h: 200 },
          rotation: 0,
          autogrow: 'height',
          visible: true,
          locked: false,
          style: {
            align: 'left',
            font_scale: 1,
            color: null,
            background: null,
            padding: 0,
            radius: 0,
            opacity: 1,
          },
        },
      ],
    },
  ] as StudioDoc['pages'];
  return base;
}

describe('setting a field', () => {
  it('writes an attribute of an entry', () => {
    const doc = pagedDoc();
    const next = applyOps(doc, [
      { op: 'set_field', target: `${doc.experience[0].nid}.company`, value: 'Contoso' },
    ] as unknown as DocOp[]);

    expect(next.experience[0].company).toBe('Contoso');
  });

  it('reaches any addressable node, not only the three entry lists', () => {
    // The server resolves this through its own index, so a mirror that knew
    // only about experience/education/projects left a skill group's label
    // editing on the server and not on screen.
    const doc = pagedDoc();
    const group = doc.skills[0];
    const next = applyOps(doc, [
      { op: 'set_field', target: `${group.nid}.label`, value: 'Languages' },
    ] as unknown as DocOp[]);

    expect(next.skills[0].label).toBe('Languages');
  });

  it('writes personal details', () => {
    const doc = pagedDoc();
    const next = applyOps(doc, [
      { op: 'set_field', target: 'personal.email', value: 'a@new.com' },
    ] as unknown as DocOp[]);

    expect(next.personal.email).toBe('a@new.com');
  });
});

describe('inserting a node the server would have completed', () => {
  // This mirror exists to predict the server's answer, and the server fills
  // container defaults during validation. Splicing a caller's object in raw
  // let a page without `elements` reach the next op as an undefined it
  // iterated -- a TypeError that took the whole editor down mid-reflow.

  it('gives an inserted page an elements array', () => {
    const doc = pagedDoc();
    const next = applyOps(doc, [
      { op: 'insert_node', parent: 'pages', index: -1, node: { nid: 'pag_new01', size: 'A4' } },
    ] as unknown as DocOp[]);

    expect(next.pages).toHaveLength(2);
    expect(next.pages[1].elements).toEqual([]);
  });

  it('survives a second op that walks the page it just made', () => {
    // The actual crash: the reflow inserts a page, then moves a frame onto it.
    const doc = pagedDoc();
    const frame = doc.pages[0].elements[0].nid;
    const next = applyOps(doc, [
      { op: 'insert_node', parent: 'pages', index: -1, node: { nid: 'pag_new01', size: 'A4' } },
      { op: 'move_node', nid: frame, parent: 'pag_new01', index: -1 },
    ] as unknown as DocOp[]);

    expect(next.pages[1].elements.map((e) => e.nid)).toEqual([frame]);
    expect(next.pages[0].elements).toHaveLength(0);
  });

  it('leaves a complete node exactly as given', () => {
    const doc = pagedDoc();
    const node = { nid: 'pag_new01', size: 'A4', orientation: 'portrait', background: null, elements: [] };
    const next = applyOps(doc, [
      { op: 'insert_node', parent: 'pages', index: -1, node },
    ] as unknown as DocOp[]);

    expect(next.pages[1]).toMatchObject(node);
  });

  it('ignores an insert whose id is already taken', () => {
    // The server rejects this op and applies the rest of the batch, so the
    // mirror has to agree or it renders a document the server refused. It
    // showed up as React's duplicate-key warning: the ops that add a page are
    // derived from a measurement, and replaying one batch put two pages
    // sharing `pag_27enw` on screen.
    const doc = pagedDoc();
    const insert = {
      op: 'insert_node',
      parent: 'pages',
      index: -1,
      node: { nid: 'pag_new01', size: 'A4' },
    };

    const next = applyOps(doc, [insert, insert] as unknown as DocOp[]);

    expect(next.pages.map((page) => page.nid)).toEqual([doc.pages[0].nid, 'pag_new01']);
  });

  it('ignores a re-sent insert that the server already applied', () => {
    const doc = pagedDoc();
    const once = applyOps(doc, [
      { op: 'insert_node', parent: 'pages', index: -1, node: { nid: 'pag_new01', size: 'A4' } },
    ] as unknown as DocOp[]);

    const twice = applyOps(once, [
      { op: 'insert_node', parent: 'pages', index: -1, node: { nid: 'pag_new01', size: 'A4' } },
    ] as unknown as DocOp[]);

    expect(twice.pages).toHaveLength(2);
  });

  it('still refuses a duplicate that is not a page', () => {
    const doc = pagedDoc();
    const existing = doc.experience[0].bullets[0].nid;
    const next = applyOps(doc, [
      { op: 'insert_node', parent: doc.experience[0].nid, index: -1, node: { nid: existing, text: 'again' } },
    ] as unknown as DocOp[]);

    expect(next.experience[0].bullets).toHaveLength(doc.experience[0].bullets.length);
  });

  it('reads a page that somehow has no elements without throwing', () => {
    // Belt and braces: a crash here takes the editor down for something the
    // next server response would have corrected on its own.
    const doc = pagedDoc();
    (doc.pages[0] as { elements?: unknown }).elements = undefined;

    expect(() =>
      applyOps(doc, [{ op: 'set_text', nid: 'sum_00001', value: 'hi' }] as unknown as DocOp[])
    ).not.toThrow();
  });
});
