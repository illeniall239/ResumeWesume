/**
 * Adding and removing a line of a résumé by hand.
 *
 * Everything on the sheet could be *edited* by hand and nothing could be added
 * or taken away: the insert strip offers a text box, an image and three
 * shapes, which are decoration on the canvas, not a bullet under a job. So a
 * person who wanted one more line had to ask the assistant for it, and a
 * person who emptied a bullet was left with a bullet point marking nothing,
 * with no way to be rid of it.
 *
 * The gesture is the one every list editor already has, so it needs no new
 * chrome and nothing to discover: Enter at the end of a line opens the next
 * one, Backspace in an empty line closes it. This module is the part that
 * decides which ops that means, kept pure so the rules are readable without a
 * document open.
 */

import type { DocOp, StudioDoc } from '@/contracts/doc';

import { mint } from '@/canvas/ids';

/** A list of lines somewhere in the document, and who owns it. */
interface Owner {
  /** The nid `insert_node` addresses: the entry whose `bullets` these are. */
  parent: string;
  lines: { nid: string }[];
}

/**
 * Which list a line belongs to.
 *
 * Bullets hang off experience, project and custom entries, and the free text
 * blocks a person places on the canvas carry lines of their own. All four are
 * looked through rather than special-cased, because "which kind of entry is
 * this" is a question the caller should not have to answer to add a line.
 */
function ownerOf(doc: StudioDoc | null, nid: string): Owner | null {
  if (!doc) return null;

  const lists: Owner[] = [];
  for (const entry of doc.experience ?? []) {
    lists.push({ parent: entry.nid, lines: entry.bullets ?? [] });
  }
  for (const entry of doc.projects ?? []) {
    lists.push({ parent: entry.nid, lines: entry.bullets ?? [] });
  }
  for (const section of doc.custom ?? []) {
    const entries = (section as { entries?: { nid: string; bullets?: { nid: string }[] }[] })
      .entries;
    for (const entry of entries ?? []) {
      lists.push({ parent: entry.nid, lines: entry.bullets ?? [] });
    }
  }
  for (const block of doc.blocks ?? []) {
    lists.push({ parent: block.nid, lines: block.lines ?? [] });
  }

  return lists.find((list) => list.lines.some((line) => line.nid === nid)) ?? null;
}

export interface AddedLine {
  ops: DocOp[];
  /** The new line's nid, so the caller can put the caret in it. */
  nid: string;
}

/**
 * Open a line directly below `after`.
 *
 * Empty, and immediately below rather than at the end of the list: Enter in
 * the middle of a job's bullets means "another one here", and appending would
 * quietly move the new line past work the person was writing between.
 */
export function addLineAfter(doc: StudioDoc | null, after: string): AddedLine | null {
  const owner = ownerOf(doc, after);
  if (!owner) return null;

  const at = owner.lines.findIndex((line) => line.nid === after);
  const nid = mint('blt');
  return {
    nid,
    ops: [
      {
        op: 'insert_node',
        parent: owner.parent,
        index: at + 1,
        node: { nid, text: '', style: 'bullet' },
      } as DocOp,
    ],
  };
}

/**
 * Close a line, and say which one takes the caret.
 *
 * The last line of an entry is kept. A job with no bullets at all still
 * renders its title and dates, so removing it is not destructive -- but
 * Backspace is held down, and an entry emptying itself out from under the
 * cursor is not what anybody meant by it.
 */
export function removeLine(
  doc: StudioDoc | null,
  nid: string
): { ops: DocOp[]; focus: string | null } | null {
  const owner = ownerOf(doc, nid);
  if (!owner || owner.lines.length < 2) return null;

  const at = owner.lines.findIndex((line) => line.nid === nid);
  const neighbour = owner.lines[at - 1] ?? owner.lines[at + 1] ?? null;
  return {
    ops: [{ op: 'remove_node', nid } as DocOp],
    focus: neighbour ? neighbour.nid : null,
  };
}
