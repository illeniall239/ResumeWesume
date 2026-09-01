/**
 * Page delete and reorder.
 *
 * The rule under test is the one that keeps a click honest: deleting a page
 * takes its elements with it, so a page holding the only frame for your work
 * history cannot go. The engine would refuse that batch anyway -- these tests
 * exist so the button is disabled *before* the user clicks it, with a reason.
 */

import { describe, expect, it } from 'vitest';

import type { PageNode, StudioDoc } from '@/contracts/doc';
import {
  canDeletePage,
  canMovePage,
  deletePage,
  movePage,
  removeElements,
} from '@/canvas/pages';

function frame(nid: string, ref: string) {
  return {
    nid,
    ref,
    rect: { x: 0, y: 0, w: 100, h: 50 },
    rotation: 0,
    autogrow: 'height' as const,
    visible: true,
    locked: false,
    style: {
      align: 'left' as const,
      font_scale: 1,
      color: null,
      background: null,
      padding: 0,
      radius: 0,
      opacity: 1,
    },
  };
}

function shape(nid: string) {
  return {
    nid,
    shape: 'rect' as const,
    rect: { x: 0, y: 0, w: 50, h: 50 },
    rotation: 0,
    fill: '#eee',
    stroke: null,
    stroke_width: 0,
    visible: true,
    locked: false,
  };
}

function page(nid: string, elements: unknown[] = []): PageNode {
  return {
    nid,
    size: 'A4',
    orientation: 'portrait',
    background: null,
    elements: elements as PageNode['elements'],
  };
}

function docOf(...pages: PageNode[]): StudioDoc {
  return { pages, blocks: [] } as unknown as StudioDoc;
}

describe('deleting a page', () => {
  it('refuses to leave a document with no pages', () => {
    expect(canDeletePage(docOf(page('pag_a')), 'pag_a')).toMatchObject({ allowed: false });
  });

  it('allows a blank page', () => {
    const doc = docOf(page('pag_a', [frame('frm_1', 'experience')]), page('pag_b'));
    expect(canDeletePage(doc, 'pag_b').allowed).toBe(true);
  });

  it('allows a page holding only decoration', () => {
    const doc = docOf(
      page('pag_a', [frame('frm_1', 'experience')]),
      page('pag_b', [shape('shp_1')])
    );
    expect(canDeletePage(doc, 'pag_b').allowed).toBe(true);
  });

  it('refuses a page holding the only frame for a section', () => {
    // The engine's coverage gate would reject this batch; the point of
    // checking here is that the button never looks clickable.
    const doc = docOf(page('pag_a', [frame('frm_1', 'experience')]), page('pag_b'));
    const verdict = canDeletePage(doc, 'pag_a');
    expect(verdict.allowed).toBe(false);
    expect(verdict.reason).toMatch(/only copy/i);
  });

  it('allows it when another page still renders that content', () => {
    const doc = docOf(
      page('pag_a', [frame('frm_1', 'experience')]),
      page('pag_b', [frame('frm_2', 'experience')])
    );
    expect(canDeletePage(doc, 'pag_a').allowed).toBe(true);
  });

  it('allows a page whose only frame is a free text block', () => {
    // The block is removed in the same batch, so nothing is stranded.
    const doc = docOf(
      page('pag_a', [frame('frm_1', 'experience')]),
      page('pag_b', [frame('frm_2', 'txb_aaaaa')])
    );
    expect(canDeletePage(doc, 'pag_b').allowed).toBe(true);
  });

  it('takes free text blocks with it', () => {
    // Words left behind would still reach the ATS export while being invisible
    // and unreachable in the editor -- worse than losing them.
    const doc = docOf(page('pag_a'), page('pag_b', [frame('frm_2', 'txb_aaaaa')]));
    const ops = deletePage(doc, 'pag_b');
    expect(ops).toEqual([
      { op: 'remove_node', nid: 'txb_aaaaa' },
      { op: 'remove_node', nid: 'pag_b' },
    ]);
  });

  it('removes the block before the page that renders it', () => {
    const doc = docOf(page('pag_a'), page('pag_b', [frame('frm_2', 'txb_aaaaa')]));
    const ops = deletePage(doc, 'pag_b') as { nid: string }[];
    expect(ops.findIndex((op) => op.nid === 'txb_aaaaa')).toBeLessThan(
      ops.findIndex((op) => op.nid === 'pag_b')
    );
  });

  it('leaves decoration to die with the page', () => {
    // A shape is a child of the page, so removing the page removes it. Listing
    // it separately would double-remove and trip the duplicate gate.
    const doc = docOf(page('pag_a'), page('pag_b', [shape('shp_1')]));
    expect(deletePage(doc, 'pag_b')).toEqual([{ op: 'remove_node', nid: 'pag_b' }]);
  });
});

describe('reordering pages', () => {
  const doc = docOf(page('pag_a'), page('pag_b'), page('pag_c'));

  it('swaps a page with the one before it', () => {
    expect(movePage(doc, 'pag_b', -1)).toEqual([
      { op: 'reorder', parent: 'pages', order: ['pag_b', 'pag_a', 'pag_c'] },
    ]);
  });

  it('swaps a page with the one after it', () => {
    expect(movePage(doc, 'pag_b', 1)).toEqual([
      { op: 'reorder', parent: 'pages', order: ['pag_a', 'pag_c', 'pag_b'] },
    ]);
  });

  it('does nothing at the ends', () => {
    expect(movePage(doc, 'pag_a', -1)).toEqual([]);
    expect(movePage(doc, 'pag_c', 1)).toEqual([]);
  });

  it('knows when a move is available', () => {
    expect(canMovePage(doc, 'pag_a', -1)).toBe(false);
    expect(canMovePage(doc, 'pag_a', 1)).toBe(true);
    expect(canMovePage(doc, 'pag_c', 1)).toBe(false);
  });

  it('names every page in the new order, since reorder is a full list', () => {
    const [op] = movePage(doc, 'pag_a', 1) as unknown as [{ order: string[] }];
    expect(op.order).toHaveLength(3);
    expect(new Set(op.order).size).toBe(3);
  });
});

describe('removing elements', () => {
  // The rule: a frame bound to a section is a *view* of content that lives in
  // the resume, so removing it deletes nothing. A frame bound to a `txb_` block
  // is the only home those words have -- leave the block behind and the
  // coverage gate calls it stranded and rolls the whole batch back, which is
  // why a hand-placed text box could not be deleted at all.

  it('takes the block with a hand-placed text box', () => {
    const doc = docOf(page('pag_aaaaa', [frame('frm_text0', 'txb_words')]));

    expect(removeElements(doc, ['frm_text0'])).toEqual([
      { op: 'remove_node', nid: 'txb_words' },
      { op: 'remove_node', nid: 'frm_text0' },
    ]);
  });

  it('leaves resume content alone', () => {
    // Removing the box a job was pulled into puts the job back in the flow.
    const doc = docOf(page('pag_aaaaa', [frame('frm_job00', 'exp_11111')]));

    expect(removeElements(doc, ['frm_job00'])).toEqual([
      { op: 'remove_node', nid: 'frm_job00' },
    ]);
  });

  it('keeps a block another frame still renders', () => {
    const doc = docOf(
      page('pag_aaaaa', [frame('frm_text0', 'txb_words')]),
      page('pag_bbbbb', [frame('frm_text1', 'txb_words')])
    );

    expect(removeElements(doc, ['frm_text0'])).toEqual([
      { op: 'remove_node', nid: 'frm_text0' },
    ]);
  });

  it('takes the block when every frame showing it goes at once', () => {
    const doc = docOf(
      page('pag_aaaaa', [frame('frm_text0', 'txb_words'), frame('frm_text1', 'txb_words')])
    );

    expect(removeElements(doc, ['frm_text0', 'frm_text1'])).toEqual([
      { op: 'remove_node', nid: 'txb_words' },
      { op: 'remove_node', nid: 'frm_text0' },
      { op: 'remove_node', nid: 'frm_text1' },
    ]);
  });

  it('names each block once however many frames named it', () => {
    const doc = docOf(
      page('pag_aaaaa', [frame('frm_text0', 'txb_words'), frame('frm_text1', 'txb_words')])
    );
    const ops = removeElements(doc, ['frm_text0', 'frm_text1']);

    expect(ops.filter((op) => (op as { nid: string }).nid === 'txb_words')).toHaveLength(1);
  });

  it('deletes a page through the same rule', () => {
    const doc = docOf(
      page('pag_aaaaa', [frame('frm_sect0', 'experience')]),
      page('pag_bbbbb', [frame('frm_text0', 'txb_words'), shape('shp_aaaaa')])
    );

    expect(deletePage(doc, 'pag_bbbbb')).toEqual([
      { op: 'remove_node', nid: 'txb_words' },
      { op: 'remove_node', nid: 'pag_bbbbb' },
    ]);
  });
});
