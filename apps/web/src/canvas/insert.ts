/**
 * Building the ops that put a new thing on a page.
 *
 * Pure: given a page and a placement, return the ops. No DOM, no fetch, no
 * store — so the awkward parts (a text block needing *two* nodes, an image
 * needing its true aspect ratio) are testable without a browser.
 *
 * Every element is minted client-side. The engine validates the id's kind
 * prefix, so these have to be well-formed, but they do not have to come from
 * the server: an element the user just created must be addressable before any
 * round trip, or the optimistic render has nothing to key on and the drag that
 * usually follows a placement has nothing to target.
 */

import type { DocOp, ImageElement, Rect, ShapeElement, TextBlockNode } from '@/contracts/doc';

import { mint } from './ids';

/** Default size for something dropped without a drawn rect, in points. */
const DEFAULT_TEXT: Rect = { x: 0, y: 0, w: 200, h: 48 };
const DEFAULT_SHAPE: Rect = { x: 0, y: 0, w: 160, h: 100 };
const DEFAULT_IMAGE_WIDTH = 160;

const NEUTRAL_STYLE = {
  align: 'left' as const,
  font_scale: 1,
  color: null,
  background: null,
  padding: 0,
  radius: 0,
  opacity: 1,
};

export interface Placement {
  pageNid: string;
  /** Top-left in page points. */
  at: { x: number; y: number };
}

/**
 * A free text box.
 *
 * Two nodes, not one: a `txb_` block holding a `sum_` line. The block is the
 * thing on the page, and the line inside it is what the agent addresses and
 * what `set_text` edits — which is what keeps a hand-placed caption a
 * first-class part of the document rather than a string the assistant cannot
 * see. "If it has words, it has a nid."
 */
export function insertTextBlock(
  placement: Placement,
  text = 'New text'
): { ops: DocOp[]; nid: string; blockNid: string } {
  const blockNid = mint('txb');
  const lineNid = mint('sum');
  const frameNid = mint('frm');

  const block: TextBlockNode = {
    nid: blockNid,
    role: 'body',
    lines: [{ nid: lineNid, text, style: 'plain' } as TextBlockNode['lines'][number]],
  };

  return {
    nid: frameNid,
    blockNid,
    ops: [
      // Content first: the frame that renders it is rejected by the coverage
      // gate if its `ref` does not resolve yet, and ops within a batch apply
      // in order.
      {
        op: 'insert_node',
        parent: 'blocks',
        index: -1,
        node: block as unknown as Record<string, unknown>,
      } as DocOp,
      {
        op: 'insert_node',
        parent: placement.pageNid,
        index: -1,
        node: {
          nid: frameNid,
          ref: blockNid,
          rect: { ...DEFAULT_TEXT, x: placement.at.x, y: placement.at.y },
          rotation: 0,
          autogrow: 'height',
          visible: true,
          locked: false,
          style: NEUTRAL_STYLE,
        },
      } as DocOp,
    ],
  };
}

/**
 * A shape. Decoration only — no nid appears in any text export, by design.
 */
export function insertShape(
  placement: Placement,
  shape: ShapeElement['shape'] = 'rect'
): { ops: DocOp[]; nid: string } {
  const nid = mint('shp');
  const rect = { ...DEFAULT_SHAPE, x: placement.at.x, y: placement.at.y };

  return {
    nid,
    ops: [
      {
        op: 'insert_node',
        parent: placement.pageNid,
        index: -1,
        node: {
          nid,
          shape,
          // A line is a rule, not a box: giving it a box's height would make
          // the first thing anyone does with it a resize.
          rect: shape === 'line' ? { ...rect, h: 0 } : rect,
          rotation: 0,
          fill: shape === 'line' ? null : '#e2e8f0',
          stroke: shape === 'line' ? '#334155' : null,
          stroke_width: shape === 'line' ? 1 : 0,
          visible: true,
          locked: false,
        } satisfies ShapeElement as unknown as Record<string, unknown>,
      } as DocOp,
    ],
  };
}

/**
 * An uploaded image, at its own aspect ratio.
 *
 * The upload response carries pixel dimensions precisely so this does not have
 * to guess, or decode the file a second time to find out.
 */
export function insertImage(
  placement: Placement,
  asset: { id: string; width: number; height: number },
  alt = ''
): { ops: DocOp[]; nid: string } {
  const nid = mint('img');
  const ratio = asset.height > 0 ? asset.width / asset.height : 1;

  return {
    nid,
    ops: [
      {
        op: 'insert_node',
        parent: placement.pageNid,
        index: -1,
        node: {
          nid,
          asset: asset.id,
          rect: {
            x: placement.at.x,
            y: placement.at.y,
            w: DEFAULT_IMAGE_WIDTH,
            h: Math.round(DEFAULT_IMAGE_WIDTH / (ratio || 1)),
          },
          rotation: 0,
          fit: 'contain',
          crop: null,
          alt,
          visible: true,
          locked: false,
          style: NEUTRAL_STYLE,
        } satisfies ImageElement as unknown as Record<string, unknown>,
      } as DocOp,
    ],
  };
}

/** A blank page after the given one, or at the end. */
export function insertPage(after?: string, index = -1): { ops: DocOp[]; nid: string } {
  const nid = mint('pag');
  return {
    nid,
    ops: [
      {
        op: 'insert_node',
        parent: 'pages',
        index,
        node: {
          nid,
          size: 'A4',
          orientation: 'portrait',
          background: null,
          elements: [],
        },
      } as DocOp,
    ],
  };
}
