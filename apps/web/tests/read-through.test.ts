/**
 * Reading a node the revision block can name.
 *
 * A row shows what a node says. Two shapes had nothing to say for themselves
 * and rendered as "Removed" for something that had just been created: a frame,
 * which is a window onto content rather than content, and a text block, whose
 * words live in its lines.
 */

import { describe, expect, it } from 'vitest';

import type { StudioDoc } from '@/contracts/doc';
import { textOf } from '@/doc/read';

const doc = {
  summary: { nid: 'sum_00001', text: 'Backend engineer.', style: 'plain' },
  experience: [],
  education: [],
  projects: [],
  skills: [],
  custom: [],
  sections: [],
  blocks: [
    {
      nid: 'txb_foot',
      role: 'caption',
      lines: [{ nid: 'sum_foot', text: 'Made with ResumeWesume', style: 'plain' }],
    },
  ],
  pages: [
    {
      nid: 'pag_aaaaa',
      size: 'A4',
      orientation: 'portrait',
      background: null,
      elements: [
        { nid: 'frm_foot', ref: 'txb_foot', rect: { x: 0, y: 0, w: 200, h: 18 } },
      ],
    },
  ],
} as unknown as StudioDoc;

describe('what a node says', () => {
  it('a text block says what its lines say', () => {
    // `role` came first in the field walk, so a footer read as "caption".
    expect(textOf(doc, 'txb_foot')).toBe('Made with ResumeWesume');
  });

  it('a frame says what it renders', () => {
    // A frame carries no readable field of its own, and the empty string is
    // what the revision row draws "Removed" from.
    expect(textOf(doc, 'frm_foot')).toBe('Made with ResumeWesume');
  });

  it('an ordinary node is unaffected', () => {
    expect(textOf(doc, 'sum_00001')).toBe('Backend engineer.');
  });

  it('a node that is genuinely gone still reads as nothing', () => {
    // Which is what "Removed" is for, and it must keep working.
    expect(textOf(doc, 'blt_deleted')).toBe('');
  });
});
