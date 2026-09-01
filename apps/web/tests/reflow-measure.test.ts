/**
 * What the reflow pass is allowed to move.
 *
 * The pass exists to correct the *autolayout column*: the server places frames
 * without being able to measure text, so the browser measures and stacks them.
 * A box the user placed by hand was never part of that column, and sweeping it
 * in overwrote the `y` they dragged it to — so a text box would not stay where
 * it was put.
 */

import { describe, expect, it } from 'vitest';

import type { PageNode, StudioDoc } from '@/contracts/doc';
import { measure, reflowSignature } from '@/canvas/use-reflow';

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

function docWith(...elements: unknown[]): StudioDoc {
  const page: PageNode = {
    nid: 'pag_aaaaa',
    size: 'A4',
    orientation: 'portrait',
    background: null,
    elements: elements as PageNode['elements'],
  };
  return {
    schema_version: 2,
    personal: { name: '', title: '', email: '', phone: '', location: '' },
    summary: null,
    experience: [],
    education: [],
    projects: [],
    skills: [],
    custom: [],
    sections: [],
    blocks: [],
    pages: [page],
    reading_order: null,
  } as unknown as StudioDoc;
}

/** A canvas holding one rendered node per frame, as the real one would. */
function rendered(...nids: string[]): HTMLElement {
  const root = document.createElement('div');
  for (const nid of nids) {
    const node = document.createElement('div');
    node.className = 'element element--frame';
    node.dataset.element = nid;
    root.appendChild(node);
  }
  return root;
}

describe('measuring for the reflow', () => {
  it('measures the frames of the autolayout column', () => {
    const doc = docWith(frame('frm_sect0', 'experience'));
    expect(measure(rendered('frm_sect0'), doc).map((f) => f.nid)).toEqual(['frm_sect0']);
  });

  it('leaves a hand-placed text box out of the stack', () => {
    const doc = docWith(frame('frm_sect0', 'experience'), frame('frm_text0', 'txb_words'));

    const measured = measure(rendered('frm_sect0', 'frm_text0'), doc);

    expect(measured.map((f) => f.nid)).toEqual(['frm_sect0']);
  });
});

describe('what re-arms the reflow', () => {
  // Re-running the pass is a full repagination: it restacks every frame and
  // moves them between pages. So what re-arms it decides whether an unrelated
  // action quietly rearranges the document.

  const pages = (...nids: string[]): StudioDoc =>
    ({
      ...docWith(),
      pages: nids.map((nid) => ({
        nid,
        size: 'A4',
        orientation: 'portrait',
        background: null,
        elements: [],
      })),
    }) as StudioDoc;

  it('does not re-arm when pages are only reordered', () => {
    // The bug: moving a page up emitted one clean `reorder`, and the reflow
    // followed it with four `move_node`s that put the header after education.
    expect(reflowSignature(pages('pag_a', 'pag_b', 'pag_c'))).toBe(
      reflowSignature(pages('pag_b', 'pag_a', 'pag_c'))
    );
  });

  it('re-arms when a page is added', () => {
    expect(reflowSignature(pages('pag_a', 'pag_b'))).not.toBe(
      reflowSignature(pages('pag_a', 'pag_b', 'pag_c'))
    );
  });

  it('re-arms when a page is deleted', () => {
    // Content on a deleted page genuinely has to be restacked.
    expect(reflowSignature(pages('pag_a', 'pag_b'))).not.toBe(
      reflowSignature(pages('pag_a'))
    );
  });
});
