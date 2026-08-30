import { describe, expect, it } from 'vitest';

import { applyOp, applyOps } from '@/doc/apply';
import type { StudioDoc } from '@/contracts/doc';

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
