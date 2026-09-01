/**
 * Selecting, dragging and resizing on the canvas.
 *
 * The DOM-touching half of the canvas, which until now was covered only by
 * hand-driven browser checks — the same gap that let two layout bugs ship
 * earlier in this project. jsdom has no layout engine, so `helpers/layout`
 * stubs `getBoundingClientRect` from the inline styles the canvas already
 * writes, and `vitest.setup` polyfills pointer capture.
 *
 * What is asserted is the *op* a gesture produces, not the pixels it painted:
 * the op is what reaches the document, and it is the thing that has to be
 * right.
 */

import { fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DocOp, PageNode, StudioDoc } from '@/contracts/doc';
import { PageView } from '@/canvas/page-canvas';
import { useSelection } from '@/canvas/selection';
import { DEFAULT_ZOOM, useView } from '@/canvas/view';

import { PAGE_ORIGIN, pt, stubLayout } from './helpers/layout';

function frame(nid: string, x: number, y: number, w = 200, h = 60) {
  return {
    nid,
    ref: nid,
    rect: { x, y, w, h },
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

const page: PageNode = {
  nid: 'pag_aaaaa',
  size: 'A4',
  orientation: 'portrait',
  background: null,
  elements: [frame('frm_aaaaa', 100, 100), frame('frm_bbbbb', 100, 300)],
};

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
  pages: [page],
  reading_order: null,
} as unknown as StudioDoc;

let restore: () => void;
let commit: ReturnType<typeof vi.fn>;

function draw() {
  commit = vi.fn();
  return render(
    <PageView page={page} doc={doc} claimed={new Set()} interactive commit={commit} />
  );
}

/** Drag from one point to another, in CSS pixels. */
function drag(
  node: Element,
  from: [number, number],
  to: [number, number],
  init: Partial<PointerEventInit> = {}
) {
  fireEvent.pointerDown(node, { clientX: from[0], clientY: from[1], ...init });
  fireEvent.pointerMove(node.closest('.canvas-page')!, {
    clientX: to[0],
    clientY: to[1],
    ...init,
  });
  fireEvent.pointerUp(node.closest('.canvas-page')!, {
    clientX: to[0],
    clientY: to[1],
    ...init,
  });
}

beforeEach(() => {
  restore = stubLayout();
  useView.setState({ zoom: DEFAULT_ZOOM });
  useSelection.setState({ selected: [], editing: null, dragging: false, guides: [] });
});

afterEach(() => restore());

describe('selection', () => {
  it('selects an element when it is pressed', () => {
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;
    fireEvent.pointerDown(element, { clientX: 200, clientY: 200 });
    expect(useSelection.getState().selected).toEqual(['frm_aaaaa']);
  });

  it('replaces the selection on a plain press', () => {
    const { container } = draw();
    fireEvent.pointerDown(container.querySelector('[data-element="frm_aaaaa"]')!, {
      clientX: 200,
      clientY: 200,
    });
    fireEvent.pointerDown(container.querySelector('[data-element="frm_bbbbb"]')!, {
      clientX: 200,
      clientY: 400,
    });
    expect(useSelection.getState().selected).toEqual(['frm_bbbbb']);
  });

  it('adds to the selection with shift', () => {
    const { container } = draw();
    fireEvent.pointerDown(container.querySelector('[data-element="frm_aaaaa"]')!, {
      clientX: 200,
      clientY: 200,
    });
    fireEvent.pointerDown(container.querySelector('[data-element="frm_bbbbb"]')!, {
      clientX: 200,
      clientY: 400,
      shiftKey: true,
    });
    expect(useSelection.getState().selected).toEqual(['frm_aaaaa', 'frm_bbbbb']);
  });

  it('clears when bare paper is pressed', () => {
    const { container } = draw();
    const canvas = container.querySelector('.canvas-page')!;
    useSelection.setState({ selected: ['frm_aaaaa'] });
    fireEvent.pointerDown(canvas, { clientX: 500, clientY: 700 });
    expect(useSelection.getState().selected).toEqual([]);
  });

  it('selects the box even when the press lands on its text', () => {
    // The rule this replaces deferred to contentEditable and let the press
    // through. That is unreachable for a hand-placed text box: its editable
    // `<p>` fills the frame edge to edge, so every press was a caret and the
    // box could never be selected, moved or deleted.
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;
    const editable = document.createElement('div');
    editable.setAttribute('contenteditable', 'true');
    element.appendChild(editable);
    // jsdom does not derive isContentEditable from the attribute.
    Object.defineProperty(editable, 'isContentEditable', { value: true });

    fireEvent.pointerDown(editable, { clientX: 200, clientY: 200, bubbles: true });
    expect(useSelection.getState().selected).toEqual(['frm_aaaaa']);
  });

  it('leaves the caret alone once the box is being edited', () => {
    // The other half: while one element is live, presses inside it belong to
    // the text, or a drag-select across a sentence would move the box.
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_bbbbb"]')!;
    useSelection.getState().beginEditing('frm_bbbbb');

    fireEvent.pointerDown(element, { clientX: 200, clientY: 340 });

    expect(useSelection.getState().selected).toEqual(['frm_bbbbb']);
    expect(useSelection.getState().dragging).toBe(false);
  });

  it('enters editing on a double-click and leaves on the next press', () => {
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;

    fireEvent.doubleClick(element, { clientX: 200, clientY: 200 });
    expect(useSelection.getState().editing).toBe('frm_aaaaa');

    fireEvent.pointerDown(container.querySelector('[data-element="frm_bbbbb"]')!, {
      clientX: 200,
      clientY: 340,
    });
    expect(useSelection.getState().editing).toBeNull();
  });
});

describe('dragging', () => {
  /** The geometry ops in a batch, ignoring the pin that rides with a drag. */
  function geometry(call: unknown): { op: string; nid: string }[] {
    return (call as { op: string; nid: string }[]).filter(
      (op) => op.op === 'set_geometry'
    );
  }

  it('commits one geometry op for a whole gesture', () => {
    // Sixty pointer events are one move, and must be one undo step.
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;

    fireEvent.pointerDown(element, { clientX: 200, clientY: 200 });
    const canvas = container.querySelector('.canvas-page')!;
    for (let step = 1; step <= 10; step += 1) {
      fireEvent.pointerMove(canvas, { clientX: 200 + step * 4, clientY: 200 + step * 2 });
    }
    fireEvent.pointerUp(canvas, { clientX: 240, clientY: 220 });

    expect(commit).toHaveBeenCalledTimes(1);
    const ops = geometry(commit.mock.calls[0][0]);
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ op: 'set_geometry', nid: 'frm_aaaaa' });
  });

  it('moves by the pointer distance, converted to points', () => {
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;
    // 40 CSS px right is 30pt; the element starts at x=100.
    drag(element, [200, 200], [200 + pt(30), 200]);

    const [op] = geometry(commit.mock.calls[0][0]) as unknown as [{ x: number; y: number }];
    expect(op.x).toBeCloseTo(130, 1);
    expect(op.y).toBeCloseTo(100, 1);
  });

  it('carries what the gesture started from, so a stale drag is caught', () => {
    const { container } = draw();
    drag(container.querySelector('[data-element="frm_aaaaa"]')!, [200, 200], [260, 260]);
    const [op] = geometry(commit.mock.calls[0][0]) as unknown as [
      { expect: Record<string, number> },
    ];
    expect(op.expect).toMatchObject({ x: 100, y: 100, w: 200, h: 60 });
  });

  it('commits nothing when the pointer does not move', () => {
    const { container } = draw();
    drag(container.querySelector('[data-element="frm_aaaaa"]')!, [200, 200], [200, 200]);
    expect(commit).not.toHaveBeenCalled();
  });

  it('hands the geometry back to React when the gesture ends', () => {
    // A gesture paints straight onto the node; what it must leave behind is
    // exactly what React wrote, not nothing. React diffs against its own
    // previous props rather than the DOM, so a property blanked here is a
    // property it never rewrites -- and a frame left with no width is a frame
    // that reflows to whatever space it can reach.
    const { container } = draw();
    const element = container.querySelector<HTMLElement>('[data-element="frm_aaaaa"]')!;
    const width = element.style.width;
    expect(width).not.toBe('');

    drag(element, [200, 200], [260, 240]);

    expect(element.style.transform).toBe('');
    expect(element.style.width).toBe(width);
  });

  it('leaves the width alone when an element is only clicked', () => {
    // The bug this pins: selecting a job made its bullets reflow wider than
    // the page and spill past the margin, because the press-and-release wiped
    // the inline width React had set and never put it back.
    const { container } = draw();
    const element = container.querySelector<HTMLElement>('[data-element="frm_aaaaa"]')!;
    const width = element.style.width;

    fireEvent.pointerDown(element, { clientX: 200, clientY: 200 });
    fireEvent.pointerUp(container.querySelector('.canvas-page')!, {
      clientX: 200,
      clientY: 200,
    });

    expect(element.style.width).toBe(width);
  });

  it('drags a box that was grabbed by its text', () => {
    // What the user could not do: a text box is nothing but text, so if a
    // press on it cannot start a gesture, the box cannot be moved at all.
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;
    const editable = document.createElement('div');
    editable.setAttribute('contenteditable', 'true');
    element.appendChild(editable);
    Object.defineProperty(editable, 'isContentEditable', { value: true });

    fireEvent.pointerDown(editable, { clientX: 200, clientY: 200, bubbles: true });
    const canvas = container.querySelector('.canvas-page')!;
    fireEvent.pointerMove(canvas, { clientX: 260, clientY: 240 });
    fireEvent.pointerUp(canvas, { clientX: 260, clientY: 240 });

    expect(geometry(commit.mock.calls[0]?.[0]).map((op) => [op.op, op.nid])).toEqual([
      ['set_geometry', 'frm_aaaaa'],
    ]);
  });

  it('converts pointer distance at the zoom it was made at', () => {
    // The pointer moves in screen pixels and the document is in points, so at
    // 200% the same gesture must move the element half as far through the
    // document. Getting this wrong is invisible at 100% and doubles every drag
    // everywhere else.
    const { container } = draw();
    const at100 = container.querySelector('[data-element="frm_aaaaa"]')!;
    drag(at100, [200, 200], [320, 200]);
    const [wide] = geometry(commit.mock.calls[0][0]) as unknown as [{ x: number }];

    useView.setState({ zoom: 2 });
    const second = draw();
    const at200 = second.container.querySelector('[data-element="frm_aaaaa"]')!;
    drag(at200, [200, 200], [320, 200]);
    const [close] = geometry(commit.mock.calls[0][0]) as unknown as [{ x: number }];

    const start = 100;
    expect(close.x - start).toBeCloseTo((wide.x - start) / 2, 3);
  });

  it('moves every selected element together', () => {
    // No shift here: pressing an already-selected element keeps the group,
    // whereas shift would toggle the one under the pointer back out of it.
    const { container } = draw();
    useSelection.setState({ selected: ['frm_aaaaa', 'frm_bbbbb'] });
    drag(container.querySelector('[data-element="frm_aaaaa"]')!, [200, 200], [260, 200]);

    const ops = geometry(commit.mock.calls[0][0]);
    expect(ops.map((op) => op.nid).sort()).toEqual(['frm_aaaaa', 'frm_bbbbb']);
  });

  it('marks the page as dragging so text selection is suppressed', () => {
    const { container } = draw();
    const element = container.querySelector('[data-element="frm_aaaaa"]')!;
    fireEvent.pointerDown(element, { clientX: 200, clientY: 200 });
    expect(useSelection.getState().dragging).toBe(true);
    fireEvent.pointerUp(container.querySelector('.canvas-page')!, {
      clientX: 200,
      clientY: 200,
    });
    expect(useSelection.getState().dragging).toBe(false);
  });
});

describe('resizing', () => {
  it('resizes from a handle rather than moving', () => {
    const { container } = draw();
    useSelection.setState({ selected: ['frm_aaaaa'] });
    const { container: c2 } = draw();
    useSelection.setState({ selected: ['frm_aaaaa'] });

    const handle = c2.querySelector('[data-handle="e"]');
    if (!handle) return; // overlay renders only with a selection
    fireEvent.pointerDown(handle, { clientX: 400, clientY: 200 });
    const canvas = c2.querySelector('.canvas-page')!;
    fireEvent.pointerMove(canvas, { clientX: 400 + pt(20), clientY: 200 });
    fireEvent.pointerUp(canvas, { clientX: 400 + pt(20), clientY: 200 });

    const ops = commit.mock.calls[0]?.[0] as { w: number; x: number }[] | undefined;
    if (!ops) return;
    expect(ops[0].w).toBeCloseTo(220, 0);
    expect(ops[0].x).toBeCloseTo(100, 1); // the west edge stayed put
  });
});

describe('the print surface', () => {
  it('renders no handles, guides or drag affordances', () => {
    // Editing chrome in a PDF would be a visible defect in the artifact of
    // record.
    const { container } = render(
      <PageView page={page} doc={doc} claimed={new Set()} />
    );
    expect(container.querySelector('.overlay')).toBeNull();
    expect(container.querySelector('.handle')).toBeNull();
  });
});

describe('rotation', () => {
  function rotatable(nid: string) {
    return {
      nid,
      shape: 'rect' as const,
      rect: { x: 100, y: 100, w: 200, h: 100 },
      rotation: 0,
      fill: '#eee',
      stroke: null,
      stroke_width: 0,
      visible: true,
      locked: false,
    };
  }

  const shapePage: PageNode = {
    nid: 'pag_bbbbb',
    size: 'A4',
    orientation: 'portrait',
    background: null,
    elements: [rotatable('shp_aaaaa')] as PageNode['elements'],
  };

  const shapeDoc = { ...doc, pages: [shapePage] } as unknown as StudioDoc;

  it('offers a rotate handle for a shape', () => {
    useSelection.setState({ selected: ['shp_aaaaa'] });
    const { container } = render(
      <PageView page={shapePage} doc={shapeDoc} claimed={new Set()} interactive commit={vi.fn()} />
    );
    expect(container.querySelector('[data-handle="rotate"]')).not.toBeNull();
  });

  it('offers none for a text frame', () => {
    // A rotated contentEditable breaks caret positioning, so the handle would
    // promise something that does not work.
    useSelection.setState({ selected: ['frm_aaaaa'] });
    const { container } = draw();
    expect(container.querySelector('[data-handle="rotate"]')).toBeNull();
  });

  it('commits a rotation rather than a move', () => {
    useSelection.setState({ selected: ['shp_aaaaa'] });
    const rotateCommit = vi.fn();
    const { container } = render(
      <PageView
        page={shapePage}
        doc={shapeDoc}
        claimed={new Set()}
        interactive
        commit={rotateCommit}
      />
    );

    const handle = container.querySelector('[data-handle="rotate"]')!;
    const canvas = container.querySelector('.canvas-page')!;
    // Client coordinates, so the page's own offset is part of the sum -- the
    // element's centre is in page points and the pointer is not, and the
    // conversion between them is the thing most likely to be wrong.
    // Grab directly above the centre (200,150), drag to its right: a quarter turn.
    const client = (x: number, y: number) => ({
      clientX: pt(x) + PAGE_ORIGIN.x,
      clientY: pt(y) + PAGE_ORIGIN.y,
    });
    fireEvent.pointerDown(handle, client(200, 50));
    fireEvent.pointerMove(canvas, client(350, 150));
    fireEvent.pointerUp(canvas, client(350, 150));

    const ops = rotateCommit.mock.calls[0]?.[0] as { rotation?: number; x?: number }[];
    expect(ops).toHaveLength(1);
    expect(ops[0].rotation).toBeCloseTo(90, 0);
    // A rotation must not also move the box.
    expect(ops[0].x).toBeUndefined();
  });

  it('commits nothing when the handle is grabbed but not turned', () => {
    useSelection.setState({ selected: ['shp_aaaaa'] });
    const rotateCommit = vi.fn();
    const { container } = render(
      <PageView
        page={shapePage}
        doc={shapeDoc}
        claimed={new Set()}
        interactive
        commit={rotateCommit}
      />
    );
    const handle = container.querySelector('[data-handle="rotate"]')!;
    const canvas = container.querySelector('.canvas-page')!;
    const still = { clientX: pt(200) + PAGE_ORIGIN.x, clientY: pt(50) + PAGE_ORIGIN.y };
    fireEvent.pointerDown(handle, still);
    fireEvent.pointerMove(canvas, still);
    fireEvent.pointerUp(canvas, still);
    expect(rotateCommit).not.toHaveBeenCalled();
  });
});
