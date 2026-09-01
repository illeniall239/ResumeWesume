/**
 * The ops that put a new element on a page.
 *
 * Pure builders, so this is a table of inputs and the ops they produce. The
 * cases worth having are the ones where the shape of the document constrains
 * the answer: a text box needs two nodes, and they have to be inserted in the
 * order the coverage gate accepts.
 */

import { describe, expect, it } from 'vitest';

import { insertImage, insertPage, insertShape, insertTextBlock } from '@/canvas/insert';

const at = { pageNid: 'pag_aaaaa', at: { x: 100, y: 200 } };

describe('text block', () => {
  it('creates the content before the frame that renders it', () => {
    // Ops apply in order within a batch, and a frame whose `ref` does not
    // resolve is refused by the coverage gate -- so the block has to exist
    // first. Emitting these the other way round fails on the server.
    const { ops } = insertTextBlock(at);
    expect(ops).toHaveLength(2);
    expect(ops[0]).toMatchObject({ op: 'insert_node', parent: 'blocks' });
    expect(ops[1]).toMatchObject({ op: 'insert_node', parent: 'pag_aaaaa' });
  });

  it('gives the words their own nid so the agent can address them', () => {
    // "If it has words, it has a nid" -- a hand-placed caption stays part of
    // the document rather than a string the assistant cannot see.
    const { ops } = insertTextBlock(at, 'Available from June');
    const block = (ops[0] as unknown as { node: { nid: string; lines: { nid: string; text: string }[] } })
      .node;
    expect(block.nid).toMatch(/^txb_/);
    expect(block.lines).toHaveLength(1);
    expect(block.lines[0].nid).toMatch(/^sum_/);
    expect(block.lines[0].text).toBe('Available from June');
  });

  it('binds the frame to the block it just made', () => {
    const { ops, blockNid } = insertTextBlock(at);
    expect((ops[1] as unknown as { node: { ref: string } }).node.ref).toBe(blockNid);
  });

  it('places it where it was dropped', () => {
    const { ops } = insertTextBlock(at);
    expect((ops[1] as unknown as { node: { rect: { x: number; y: number } } }).node.rect).toMatchObject({
      x: 100,
      y: 200,
    });
  });

  it('autogrows, because typing must not clip the box', () => {
    const { ops } = insertTextBlock(at);
    expect((ops[1] as unknown as { node: { autogrow: string } }).node.autogrow).toBe('height');
  });
});

describe('shapes', () => {
  it('mints a shape id, which keeps it out of every text export', () => {
    const { nid } = insertShape(at);
    expect(nid).toMatch(/^shp_/);
  });

  it('gives a rectangle a fill and no stroke', () => {
    const { ops } = insertShape(at, 'rect');
    const node = (ops[0] as unknown as { node: { fill: string | null; stroke_width: number } }).node;
    expect(node.fill).not.toBeNull();
    expect(node.stroke_width).toBe(0);
  });

  it('gives a line a stroke and no height', () => {
    // A line is a rule, not a box: a default height would make the first thing
    // anyone does with it a resize.
    const { ops } = insertShape(at, 'line');
    const node = (ops[0] as unknown as { node: { rect: { h: number }; stroke: string | null } }).node;
    expect(node.rect.h).toBe(0);
    expect(node.stroke).not.toBeNull();
  });

  it('is one op, since a shape has no content half', () => {
    expect(insertShape(at).ops).toHaveLength(1);
  });
});

describe('images', () => {
  const asset = { id: 'a'.repeat(64), width: 800, height: 400 };

  it('arrives at the asset’s own aspect ratio', () => {
    // The upload response carries dimensions precisely so this does not have
    // to guess or decode the file again.
    const { ops } = insertImage(at, asset);
    const rect = (ops[0] as unknown as { node: { rect: { w: number; h: number } } }).node.rect;
    expect(rect.w / rect.h).toBeCloseTo(2, 1);
  });

  it('survives an asset that reports no height', () => {
    const { ops } = insertImage(at, { id: 'x', width: 100, height: 0 });
    const rect = (ops[0] as unknown as { node: { rect: { h: number } } }).node.rect;
    expect(Number.isFinite(rect.h)).toBe(true);
    expect(rect.h).toBeGreaterThan(0);
  });

  it('carries alt text, which is what it contributes to reading order', () => {
    const { ops } = insertImage(at, asset, 'Headshot');
    expect((ops[0] as unknown as { node: { alt: string } }).node.alt).toBe('Headshot');
  });

  it('references the asset by id rather than embedding it', () => {
    // A photo inside the document JSON would be deep-copied on every batch.
    const { ops } = insertImage(at, asset);
    expect((ops[0] as unknown as { node: { asset: string } }).node.asset).toBe(asset.id);
  });
});

describe('pages', () => {
  it('adds a blank page', () => {
    const { ops, nid } = insertPage();
    expect(nid).toMatch(/^pag_/);
    expect(ops[0]).toMatchObject({ op: 'insert_node', parent: 'pages', index: -1 });
    expect((ops[0] as unknown as { node: { elements: unknown[] } }).node.elements).toEqual([]);
  });

  it('can be placed at a position rather than the end', () => {
    const { ops } = insertPage(undefined, 1);
    expect((ops[0] as unknown as { index: number }).index).toBe(1);
  });
});

describe('ids', () => {
  it('mints a distinct id every time', () => {
    const ids = new Set(Array.from({ length: 50 }, () => insertShape(at).nid));
    expect(ids.size).toBe(50);
  });

  it('mints ids the engine will accept', () => {
    // The prefix IS the kind, and the engine rejects anything it cannot parse.
    for (const nid of [insertShape(at).nid, insertPage().nid, insertTextBlock(at).blockNid]) {
      expect(nid).toMatch(/^[a-z]{3}_[0-9abcdefghjkmnpqrstvwxyz]{5}$/);
    }
  });
});
