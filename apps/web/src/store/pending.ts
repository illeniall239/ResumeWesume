/**
 * The queue between a gesture and the server.
 *
 * Pure functions over op lists, extracted from the store so the awkward parts
 * are testable without a network: what coalesces, what a conflict does, and
 * what order things land in.
 *
 * A drag at 60fps cannot round-trip per pointer event, and must not send sixty
 * ops when the user made one gesture. So ops accumulate locally, coalesce, and
 * go out as one batch — while the document the user sees is the server's last
 * word with everything still in flight replayed on top.
 */

import type { DocOp } from '@/contracts/doc';

type Geometry = Extract<DocOp, { op: 'set_geometry' }>;

function isGeometry(op: DocOp): op is Geometry {
  return op.op === 'set_geometry';
}

/**
 * The node an op is about.
 *
 * An insert names its subject inside `node`, not at the top level, so without
 * this case a newly placed element looks unrelated to the very ops describing
 * where it went.
 */
function target(op: DocOp): string | null {
  if (op.op === 'insert_node') return insertedNid(op);
  if ('nid' in op && typeof op.nid === 'string') return op.nid;
  return null;
}

/**
 * Collapse a run of ops into the fewest that mean the same thing.
 *
 * Three rules, each earning its place:
 *
 * *Geometry merges.* Sixty pointer events on one element are one move. The
 * later op's stated fields win; fields it does not mention keep the earlier
 * value, which is why `set_geometry` has all-optional fields.
 *
 * *A removal absorbs what came before it.* Moving something and then deleting
 * it is just deleting it, and sending the move first would make the undo stack
 * two steps deep for one action.
 *
 * *An insert absorbs geometry on what it inserted.* Placing a new box and then
 * positioning it is one creation, not a creation plus a correction — and the
 * correction would otherwise reference a nid the server has not seen yet if
 * the batch were ever split.
 */
export function coalesce(ops: readonly DocOp[]): DocOp[] {
  const out: DocOp[] = [];

  for (const op of ops) {
    const nid = target(op);

    if (isGeometry(op) && nid) {
      const previous = out.findLast?.((candidate) => target(candidate) === nid);
      if (previous && isGeometry(previous)) {
        out[out.lastIndexOf(previous)] = mergeGeometry(previous, op);
        continue;
      }
      if (previous && previous.op === 'insert_node') {
        out[out.lastIndexOf(previous)] = foldIntoInsert(previous, op);
        continue;
      }
    }

    if (op.op === 'remove_node' && nid) {
      // Drop everything that only described the thing being removed. An insert
      // is kept out too: creating and deleting in one batch is a no-op, and
      // sending both would burn a version to change nothing.
      const inserted = out.some(
        (candidate) => candidate.op === 'insert_node' && insertedNid(candidate) === nid
      );
      const kept = out.filter((candidate) => target(candidate) !== nid);
      if (inserted) {
        out.length = 0;
        out.push(...kept);
        continue;
      }
      out.length = 0;
      out.push(...kept, op);
      continue;
    }

    out.push(op);
  }

  return out;
}

function mergeGeometry(earlier: Geometry, later: Geometry): Geometry {
  return {
    ...earlier,
    x: later.x ?? earlier.x,
    y: later.y ?? earlier.y,
    w: later.w ?? earlier.w,
    h: later.h ?? earlier.h,
    rotation: later.rotation ?? earlier.rotation,
    // The earliest expectation is the honest one: it describes the state the
    // gesture actually started from.
    expect: earlier.expect ?? later.expect,
  };
}

function insertedNid(op: Extract<DocOp, { op: 'insert_node' }>): string | null {
  const node = op.node as { nid?: unknown } | undefined;
  return typeof node?.nid === 'string' ? node.nid : null;
}

function foldIntoInsert(
  insert: Extract<DocOp, { op: 'insert_node' }>,
  geometry: Geometry
): DocOp {
  const node = { ...(insert.node as Record<string, unknown>) };
  const rect = { ...((node.rect as Record<string, number>) ?? {}) };
  if (geometry.x != null) rect.x = geometry.x;
  if (geometry.y != null) rect.y = geometry.y;
  if (geometry.w != null) rect.w = geometry.w;
  if (geometry.h != null) rect.h = geometry.h;
  node.rect = rect;
  if (geometry.rotation != null) node.rotation = geometry.rotation;
  return { ...insert, node };
}

/**
 * Whether an op still makes sense after someone else's ops landed underneath.
 *
 * Used on a 409: the client replays what the server did, then decides what of
 * its own work to re-send. Anything addressing a node the other writer deleted
 * is dropped, because re-sending it would only earn an `unknown_node`
 * rejection and a confusing error.
 */
export function rebase(pending: readonly DocOp[], theirs: readonly DocOp[]): DocOp[] {
  const removed = new Set(
    theirs.filter((op) => op.op === 'remove_node').map((op) => op.nid)
  );
  if (!removed.size) return [...pending];
  return pending.filter((op) => {
    const nid = target(op);
    return !nid || !removed.has(nid);
  });
}
