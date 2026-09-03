/**
 * When the correction pass runs a second time.
 *
 * The pass measures what the browser rendered and rewrites the frame geometry,
 * because the server places frames without fonts or line-breaking and cannot
 * know how tall text is. It is guarded so it runs once rather than looping on
 * its own output.
 *
 * That guard was the page ids, which do not change when text does. So the pass
 * ran once on load and never again, and an agent turn that rewrote a two-line
 * bullet into four left the frame at the height measured for the old text --
 * with the section below drawn over the top of it. Dividers through the middle
 * of a sentence is what that looks like on screen.
 */

import { render } from '@testing-library/react';
import { useRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { DocOp, PageNode, StudioDoc } from '@/contracts/doc';
import { useReflow } from '@/canvas/use-reflow';

function docWithOneFrame(): StudioDoc {
  const page: PageNode = {
    nid: 'pag_aaaaa',
    size: 'A4',
    orientation: 'portrait',
    background: null,
    elements: [
      {
        nid: 'frm_aaaaa',
        ref: 'sum_00001',
        rect: { x: 0, y: 28, w: 500, h: 40 },
        rotation: 0,
        autogrow: 'height',
        visible: true,
        locked: false,
        pinned: false,
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
    ] as PageNode['elements'],
  } as PageNode;
  return { pages: [page] } as unknown as StudioDoc;
}

/** A frame whose rendered height is far past what the document records. */
function Harness({ version, commit }: { version: number; commit: (ops: DocOp[]) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useReflow(ref, docWithOneFrame(), commit, version);
  return (
    <div ref={ref}>
      <div className="element--frame" data-element="frm_aaaaa" />
    </div>
  );
}

function measuresTall(height: number) {
  Element.prototype.getBoundingClientRect = vi.fn(
    () => ({ height, width: 500, top: 0, left: 0, bottom: height, right: 500, x: 0, y: 0 }) as DOMRect
  );
}

describe('re-running the correction after content changes', () => {
  it('corrects again when the version moves', async () => {
    measuresTall(400);
    const commit = vi.fn();
    const { rerender } = render(<Harness version={1} commit={commit} />);
    await vi.waitFor(() => expect(commit).toHaveBeenCalledTimes(1));

    // The same document, one edit later: text is taller than the stored rect,
    // so the geometry has to be corrected a second time.
    rerender(<Harness version={2} commit={commit} />);
    await vi.waitFor(() => expect(commit).toHaveBeenCalledTimes(2));
  });

  it('does not run again for the same version', async () => {
    measuresTall(400);
    const commit = vi.fn();
    const { rerender } = render(<Harness version={1} commit={commit} />);
    await vi.waitFor(() => expect(commit).toHaveBeenCalledTimes(1));

    // Re-rendering for any other reason must not write a version per frame.
    rerender(<Harness version={1} commit={commit} />);
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(commit).toHaveBeenCalledTimes(1);
  });
});
