/**
 * Wherever the agent works, the pen goes.
 *
 * The pen takes no position it was not given: `tool_args` names a validated
 * target before the edit is attempted, and `patch_applied` names what was
 * touched after. So the question this pins is narrow and load-bearing -- can
 * the client turn each tool's arguments into a place on the sheet?
 *
 * Four could not, and each stranded the pen in the margin for the whole call:
 * `set_personal_info` names a field with no node, `add_bullet` and
 * `reorder_bullets` name the parent they are working inside, and `set_section`
 * names a section key. The shapes below are the real ones, taken from
 * `studio/agent/tools.py`; `test_pen_can_follow.py` is the other half of this,
 * failing if a tool is added whose arguments carry none of them.
 */

import { describe, expect, it } from 'vitest';

import { targetOf } from '@/store/chat';

describe('turning a tool call into a place on the sheet', () => {
  it('reads a node id', () => {
    expect(targetOf({ nid: 'blt_aaaaa', text: 'x' })).toBe('blt_aaaaa');
  });

  it('reads a field on a node as the exact span', () => {
    // The same path `drafting` and `data-field` use, so a job title resolves
    // to the title rather than to the whole entry.
    expect(targetOf({ nid: 'exp_11111', field: 'title', value: 'x' })).toBe(
      'exp_11111.title'
    );
  });

  it('reads a field with no node as a personal field', () => {
    // `set_personal_info` is the only tool shaped this way, and the header
    // renders those as `personal.<name>`.
    expect(targetOf({ field: 'phone', value: '+1 555 0199' })).toBe('personal.phone');
  });

  it('reads the parent a line is being added inside', () => {
    // `add_bullet` names no node of its own, because the node it is about does
    // not exist yet -- but the change appears inside the parent.
    expect(targetOf({ parent: 'exp_11111', text: 'A new line.' })).toBe('exp_11111');
  });

  it('reads the parent whose children are being reordered', () => {
    expect(targetOf({ parent: 'exp_11111', order: ['blt_b', 'blt_a'] })).toBe(
      'exp_11111'
    );
  });

  it('reads a section key', () => {
    // `set_section` shows, hides or reorders a whole section, and the section
    // frame carries `data-section`.
    expect(targetOf({ key: 'education', visible: false })).toBe('education');
  });

  it('reads the section a read named', () => {
    expect(targetOf({ section: 'experience' })).toBe('experience');
  });

  it('reads the first of several boxes being arranged', () => {
    // The eye should be on one of them; the rest move with it.
    expect(targetOf({ preset: 'align_left', nids: ['txb_a', 'shp_b'] })).toBe('txb_a');
  });

  it('reads the page something is being placed on', () => {
    // Coarser than the rest, and the best there is: the box does not exist
    // yet, so there is nothing finer to point at until `touched` names it.
    expect(targetOf({ asset: 'ast_1', page: 'pag_aaaaa', where: 'top-right' })).toBe(
      'pag_aaaaa'
    );
  });

  it('says nothing when a call genuinely names nothing on the page', () => {
    // `add_skill` and its kind create what they are about, so there is no
    // target until `patch_applied` reports what was touched. Answering with a
    // guess would put the pen somewhere the work is not happening, on a sheet
    // whose whole premise is that every mark on it is evidence.
    expect(targetOf({ skill: 'Rust', group: 'technical' })).toBeNull();
    expect(targetOf({ asset: 'ast_1' })).toBeNull();
    expect(targetOf({ query: 'ledger', limit: 5 })).toBeNull();
    expect(targetOf(undefined)).toBeNull();
    expect(targetOf({})).toBeNull();
  });

  it('prefers the node over anything coarser', () => {
    // A call carrying both must land on the finer of the two.
    expect(targetOf({ nid: 'blt_aaaaa', parent: 'exp_11111', page: 'pag_a' })).toBe(
      'blt_aaaaa'
    );
    expect(targetOf({ parent: 'exp_11111', page: 'pag_a' })).toBe('exp_11111');
  });
});
