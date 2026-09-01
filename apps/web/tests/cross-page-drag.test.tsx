/**
 * Dragging content from one page to another.
 *
 * Three separate things stopped this, and each alone was enough: the sheet
 * clipped its own contents so the element vanished at the edge, the drag
 * clamped every rect inside the page it started on, and the gesture only ever
 * produced a `set_geometry` — nothing said which page the element now belongs
 * to. Geometry is page-relative, so the last one matters most: the same rect
 * means somewhere else the moment the parent changes.
 */

import { fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DocOp, PageNode, StudioDoc } from '@/contracts/doc';
import { PageCanvas } from '@/canvas/page-canvas';
import { useSelection } from '@/canvas/selection';

import { PAGE_ORIGIN, pageTop, pt, stubLayout } from './helpers/layout';

/** A4 in points, as `units.ts` reports it. */
const PAGE_HEIGHT = 841.89;

function frame(nid: string, ref: string, x: number, y: number) {
  return {
    nid,
    ref,
    rect: { x, y, w: 200, h: 60 },
    rotation: 0,
    autogrow: 'none' as const,
    visible: true,
    locked: false,
    pinned: false,
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

function page(nid: string, elements: unknown[]): PageNode {
  return {
    nid,
    size: 'A4',
    orientation: 'portrait',
    background: null,
    elements: elements as PageNode['elements'],
  };
}

const doc = {
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
  pages: [
    page('pag_aaaaa', [frame('frm_aaaaa', 'summary', 100, 100)]),
    page('pag_bbbbb', [frame('frm_bbbbb', 'skills', 100, 100)]),
  ],
  reading_order: null,
} as unknown as StudioDoc;

let restore: () => void;
let commit: ReturnType<typeof vi.fn>;

function draw() {
  commit = vi.fn();
  return render(<PageCanvas doc={doc} interactive commit={commit} />);
}

beforeEach(() => {
  restore = stubLayout();
  useSelection.setState({ selected: [], editing: null, dragging: false, guides: [] });
});
afterEach(() => restore());

/** A point inside the nth stacked sheet, in client pixels. */
function onPage(index: number, dx = 60, dy = 60) {
  return {
    clientX: PAGE_ORIGIN.x + dx,
    clientY: pageTop(index, pt(PAGE_HEIGHT)) + dy,
  };
}

function dragTo(node: Element, to: { clientX: number; clientY: number }) {
  const from = onPage(0, 120, 130);
  fireEvent.pointerDown(node, from);
  const canvas = node.closest('.canvas-page')!;
  fireEvent.pointerMove(canvas, to);
  fireEvent.pointerUp(canvas, to);
}

/** The batch a gesture produced, less the pin that rides with a first drag. */
function placement(call: unknown): DocOp[] {
  return (call as DocOp[]).filter((op) => op.op !== 'set_element_style');
}

describe('dragging across pages', () => {
  it('moves the element to the page it was dropped on', () => {
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;

    dragTo(element, onPage(1, 140, 200));

    const ops = placement(commit.mock.calls[0]?.[0]);
    expect(ops.map((op) => op.op)).toEqual(['move_node', 'set_geometry']);
    expect(ops[0]).toMatchObject({ nid: 'frm_aaaaa', parent: 'pag_bbbbb' });
  });

  it('measures the new position against the page it landed on', () => {
    // The whole reason a plain `set_geometry` was not enough: page two's
    // coordinates start again at zero, so keeping the old y would drop the
    // element a page's height below where it was let go.
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;

    dragTo(element, onPage(1, 140, 200));

    const [, geometry] = placement(commit.mock.calls[0][0]) as unknown as [
      DocOp,
      { y: number },
    ];
    expect(geometry.y).toBeGreaterThanOrEqual(0);
    expect(geometry.y).toBeLessThan(PAGE_HEIGHT);
  });

  it('stays put when the drag ends on its own page', () => {
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;

    dragTo(element, onPage(0, 200, 260));

    expect(placement(commit.mock.calls[0]?.[0]).map((op) => op.op)).toEqual([
      'set_geometry',
    ]);
  });

  it('carries a whole multi-selection to the new page', () => {
    const { container } = draw();
    useSelection.getState().selectMany(['frm_aaaaa']);
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;

    dragTo(element, onPage(1, 140, 200));

    const ops = commit.mock.calls[0][0] as DocOp[];
    const moves = ops.filter((op) => op.op === 'move_node');
    expect(moves).toHaveLength(1);
  });

  it('lifts the dragged element above the sheet below it', () => {
    // Pages are siblings and later ones paint on top, so without this the
    // element slides underneath page two while still under the pointer.
    const { container } = draw();
    const element = container.querySelector<HTMLElement>('[data-element="frm_aaaaa"]')!;

    fireEvent.pointerDown(element, onPage(0, 120, 130));
    fireEvent.pointerMove(element.closest('.canvas-page')!, onPage(1, 140, 200));

    expect(Number(element.style.zIndex)).toBeGreaterThan(100);
  });

  it('takes the frame out of the care of the reflow', () => {
    // Otherwise the move survives the round trip and is undone on the next
    // load: the reflow owns every frame it is not told to leave alone, and it
    // stacks them back into the column.
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;

    dragTo(element, onPage(1, 140, 200));

    const ops = commit.mock.calls[0][0] as DocOp[];
    expect(ops).toContainEqual(
      expect.objectContaining({
        op: 'set_element_style',
        nid: 'frm_aaaaa',
        patch: { pinned: true },
      })
    );
  });

  it('does not say so twice', () => {
    // A frame already pinned needs no second pin, or every drag would carry a
    // no-op op and burn a version saying what was already true.
    const doc2 = {
      ...doc,
      pages: [
        page('pag_aaaaa', [{ ...frame('frm_aaaaa', 'summary', 100, 100), pinned: true }]),
        page('pag_bbbbb', []),
      ],
    } as unknown as StudioDoc;
    commit = vi.fn();
    const { container } = render(<PageCanvas doc={doc2} interactive commit={commit} />);

    dragTo(container.querySelector('[data-element="frm_aaaaa"]')!, onPage(1, 140, 200));

    const ops = commit.mock.calls[0][0] as DocOp[];
    expect(ops.filter((op) => op.op === 'set_element_style')).toHaveLength(0);
  });
});
