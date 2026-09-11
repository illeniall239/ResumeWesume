import { describe, expect, it } from 'vitest';

import { addLineAfter, removeLine } from '@/doc/lines';
import { applyOps } from '@/doc/apply';
import type { StudioDoc } from '@/contracts/doc';

/**
 * A skill can be removed and added by hand, the way a bullet can.
 *
 * The comma between skills is a separator the renderer draws between items,
 * not a character in any editable field -- so you cannot delete it directly.
 * Removing a skill is how its comma goes, and there was no manual path to that:
 * `ownerOf` knew about bullets and text boxes but not skills, so backspace on
 * an emptied skill did nothing and the only way to drop one was the agent.
 */

const doc = (): StudioDoc =>
  ({
    schema_version: 1, template: 'plain', layout: 'stack', scaffold: false, unverified: [],
    personal: { name: '', title: '', email: '', phone: '', location: '',
      website: null, linkedin: null, github: null, photo: null },
    summary: null, experience: [], education: [], projects: [],
    skills: [
      { nid: 'sgp_aaaaa', key: 'technical', label: 'Technical Skills', items: [
        { nid: 'skl_aaaaa', text: 'Python', source: 'original' },
        { nid: 'skl_bbbbb', text: 'Excel', source: 'original' },
        { nid: 'skl_ccccc', text: 'SQL', source: 'original' },
      ] },
    ],
    custom: [], sections: [], blocks: [], pages: [], reading_order: null,
  }) as StudioDoc;

const skills = (d: StudioDoc) => d.skills[0].items.map((s) => s.text);
const ids = (d: StudioDoc) => d.skills[0].items.map((s) => s.nid);

describe('removing a skill by hand', () => {
  it('drops the item, and its separator comma goes with it', () => {
    const cut = removeLine(doc(), 'skl_bbbbb');
    expect(cut).not.toBeNull();
    const after = applyOps(doc(), cut!.ops);
    expect(skills(after)).toEqual(['Python', 'SQL']);
  });

  it('puts the caret on a neighbour', () => {
    const cut = removeLine(doc(), 'skl_bbbbb');
    expect(cut!.focus).toBe('skl_aaaaa');
  });

  it('keeps the last skill rather than emptying the group', () => {
    const one = { ...doc(), skills: [{ ...doc().skills[0], items: [doc().skills[0].items[0]] }] };
    expect(removeLine(one, 'skl_aaaaa')).toBeNull();
  });
});

describe('adding a skill by hand', () => {
  it('inserts an empty skill after the current one, as a skill node not a bullet', () => {
    const made = addLineAfter(doc(), 'skl_aaaaa');
    expect(made).not.toBeNull();
    expect(made!.nid.startsWith('skl_')).toBe(true);

    const after = applyOps(doc(), made!.ops);
    // Landed directly after Python, empty, ready to type into.
    expect(ids(after)[1]).toBe(made!.nid);
    expect(after.skills[0].items[1].text).toBe('');
    // The source marks it as the user's own, not something the model invented.
    expect((after.skills[0].items[1] as { source: string }).source).toBe('user');
  });
});
