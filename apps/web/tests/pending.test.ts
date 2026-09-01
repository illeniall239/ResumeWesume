/**
 * The queue between a gesture and the server.
 *
 * Pure op-list arithmetic, which is where the subtle bugs in an optimistic
 * client live: what collapses, what survives a conflict, and in what order.
 * None of it needs a network or a DOM.
 */

import { describe, expect, it } from 'vitest';

import type { DocOp } from '@/contracts/doc';
import { coalesce, rebase } from '@/store/pending';

const move = (nid: string, x: number, y?: number): DocOp =>
  ({ op: 'set_geometry', nid, x, y: y ?? null } as DocOp);

const remove = (nid: string): DocOp => ({ op: 'remove_node', nid } as DocOp);

const insert = (nid: string, parent = 'pag_1'): DocOp =>
  ({ op: 'insert_node', parent, index: -1, node: { nid, ref: 'skills' } } as DocOp);

const text = (nid: string, value: string): DocOp =>
  ({ op: 'set_text', nid, value } as DocOp);

describe('coalesce', () => {
  it('collapses a drag into one op', () => {
    // Sixty pointer events are one gesture and must be one undo step.
    const dragged = coalesce([move('a', 1), move('a', 2), move('a', 3)]);
    expect(dragged).toHaveLength(1);
    expect((dragged[0] as { x: number }).x).toBe(3);
  });

  it('keeps fields the later op did not mention', () => {
    // Which is the reason set_geometry has all-optional fields: a move is
    // {x,y} and a resize is {w,h}, and merging must not erase either.
    const merged = coalesce([
      { op: 'set_geometry', nid: 'a', x: 10, y: 20 } as DocOp,
      { op: 'set_geometry', nid: 'a', w: 100 } as DocOp,
    ]);
    expect(merged[0]).toMatchObject({ x: 10, y: 20, w: 100 });
  });

  it('keeps the earliest expectation', () => {
    // It describes the state the gesture actually started from; the later one
    // describes a position this same gesture created.
    const merged = coalesce([
      { op: 'set_geometry', nid: 'a', x: 1, expect: { x: 0 } } as DocOp,
      { op: 'set_geometry', nid: 'a', x: 2, expect: { x: 1 } } as DocOp,
    ]);
    expect(merged[0]).toMatchObject({ expect: { x: 0 } });
  });

  it('does not merge across different elements', () => {
    expect(coalesce([move('a', 1), move('b', 2), move('a', 3)])).toHaveLength(2);
  });

  it('lets a removal absorb the moves before it', () => {
    // Moving something and then deleting it is just deleting it.
    const result = coalesce([move('a', 1), move('a', 2), remove('a')]);
    expect(result).toEqual([remove('a')]);
  });

  it('cancels an insert followed by its own removal', () => {
    // Creating and deleting in one batch changes nothing, and sending both
    // would burn a version to do it.
    expect(coalesce([insert('a'), move('a', 5), remove('a')])).toEqual([]);
  });

  it('folds geometry into the insert that created the element', () => {
    // One creation, not a creation plus a correction referencing a nid the
    // server has not seen yet.
    const result = coalesce([insert('a'), move('a', 42)]);
    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({ op: 'insert_node' });
    expect((result[0] as unknown as { node: { rect: { x: number } } }).node.rect.x).toBe(42);
  });

  it('leaves unrelated ops in order', () => {
    const ops = [text('b1', 'one'), move('a', 1), text('b2', 'two')];
    expect(coalesce(ops)).toEqual(ops);
  });

  it('does not disturb ops for other elements when one is removed', () => {
    const result = coalesce([text('b', 'kept'), move('a', 1), remove('a')]);
    expect(result).toEqual([text('b', 'kept'), remove('a')]);
  });

  it('is a no-op on an empty list', () => {
    expect(coalesce([])).toEqual([]);
  });
});

describe('rebase', () => {
  it('keeps everything when nothing was deleted underneath', () => {
    const mine = [move('a', 1), text('b', 'x')];
    expect(rebase(mine, [text('c', 'theirs')])).toEqual(mine);
  });

  it('drops ops addressing something the other writer deleted', () => {
    // Re-sending them would earn an unknown_node rejection and a confusing
    // error about work the user did not know was gone.
    const result = rebase([move('a', 1), text('b', 'x')], [remove('a')]);
    expect(result).toEqual([text('b', 'x')]);
  });

  it('keeps ops that name no node', () => {
    const reorder = { op: 'reorder', parent: 'pag_1', order: ['a'] } as DocOp;
    expect(rebase([reorder], [remove('a')])).toEqual([reorder]);
  });

  it('returns a copy rather than the original array', () => {
    const mine = [move('a', 1)];
    expect(rebase(mine, [])).not.toBe(mine);
  });
});
