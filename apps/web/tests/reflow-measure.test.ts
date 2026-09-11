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

describe('the order the pass stacks in', () => {
  /**
   * A section move rewrites geometry, not the element array.
   *
   * `reflow` stacks the frames in the order it is handed them, and the DOM
   * hands them over in `page.elements` order -- which a section move does not
   * touch. So the pass restacked the arrangement the move had just replaced,
   * on the next paint, and the section sprang back. Asked to put Projects
   * between Education and Skills the server did exactly that; the sheet went
   * on reading Skills-then-Projects for another fifty seconds, until an
   * unrelated turn happened to repaginate.
   *
   * What a reader sees is `y`, so `y` is the order this has to report.
   */
  function at(nid: string, ref: string, y: number) {
    return { ...frame(nid, ref), rect: { x: 0, y, w: 100, h: 50 } };
  }

  it('reports frames in the order the document declares', () => {
    const doc = docWith(
      at('frm_skills', 'skills', 300),
      at('frm_projects', 'projects', 200),
      at('frm_education', 'education', 100)
    );

    const measured = measure(
      rendered('frm_skills', 'frm_projects', 'frm_education'),
      doc
    );

    expect(measured.map((f) => f.nid)).toEqual([
      'frm_education',
      'frm_projects',
      'frm_skills',
    ]);
  });

  it('keeps the pages in document order, whatever the y values are', () => {
    // `y` restarts at the top of every sheet, so sorting on it alone would
    // interleave page two into page one.
    const doc = {
      ...docWith(),
      pages: [
        {
          nid: 'pag_one',
          size: 'A4',
          orientation: 'portrait',
          background: null,
          elements: [at('frm_late', 'skills', 700)],
        },
        {
          nid: 'pag_two',
          size: 'A4',
          orientation: 'portrait',
          background: null,
          elements: [at('frm_early', 'projects', 30)],
        },
      ],
    } as unknown as StudioDoc;

    const measured = measure(rendered('frm_late', 'frm_early'), doc);

    expect(measured.map((f) => f.nid)).toEqual(['frm_late', 'frm_early']);
  });
});

describe('the order a turn leaves behind', () => {
  /**
   * The bug the op log caught, in one test.
   *
   * The server re-stacks a batch that moves or adds a section. The client
   * mirror does not, so mid-turn its document still has a newly added
   * section's frame where `cover_for` first put it -- the foot of the last
   * page. Ordering this pass by `rect.y` measured exactly that, stacked the
   * section last, and committed `set_geometry` back over the server's correct
   * answer:
   *
   *   v3  agent   set_section certifications 3 …   (server places it third)
   *   v4  layout  set_geometry frm_… y=415.1       (browser puts it last)
   *
   * So the order comes from the document's own declaration, which is what
   * both renderers already draw by, and cannot be stale.
   */
  function frameAt(nid: string, ref: string, y: number) {
    return { ...frame(nid, ref), rect: { x: 0, y, w: 100, h: 50 } };
  }

  it('uses the section order even when the geometry disagrees', () => {
    const base = docWith(
      frameAt('frm_edu', 'education', 100),
      // Where `cover_for` puts a brand-new section: the bottom.
      frameAt('frm_cert', 'cst_new', 900),
      frameAt('frm_skills', 'skills', 200)
    );
    const doc = {
      ...base,
      custom: [
        { nid: 'cst_new', key: 'certifications', label: 'Certifications', kind: 'stringList', text: null, items: [], strings: [] },
      ],
      sections: [
        { key: 'education', label: 'Education', visible: true, order: 0 },
        { key: 'certifications', label: 'Certifications', visible: true, order: 1 },
        { key: 'skills', label: 'Skills', visible: true, order: 2 },
      ],
    } as unknown as StudioDoc;

    const measured = measure(rendered('frm_edu', 'frm_cert', 'frm_skills'), doc);

    expect(measured.map((f) => f.nid)).toEqual(['frm_edu', 'frm_cert', 'frm_skills']);
  });

  it('puts a moved section where the order says, not where it sits', () => {
    const base = docWith(
      frameAt('frm_edu', 'education', 100),
      frameAt('frm_skills', 'skills', 200),
      frameAt('frm_proj', 'projects', 300)
    );
    const doc = {
      ...base,
      sections: [
        { key: 'education', label: 'Education', visible: true, order: 0 },
        { key: 'projects', label: 'Projects', visible: true, order: 1 },
        { key: 'skills', label: 'Skills', visible: true, order: 2 },
      ],
    } as unknown as StudioDoc;

    const measured = measure(rendered('frm_edu', 'frm_skills', 'frm_proj'), doc);

    expect(measured.map((f) => f.nid)).toEqual(['frm_edu', 'frm_proj', 'frm_skills']);
  });
});
