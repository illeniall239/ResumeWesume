/**
 * What is selected, and what gesture is in flight.
 *
 * A separate store from the document on purpose. Selection is not content: it
 * must never reach the content hash, never enter the undo stack, and never
 * cause a version bump. Keeping it here makes that structural rather than a
 * rule someone has to remember.
 *
 * The live gesture lives here too, for the same reason — a drag in progress is
 * not a fact about the resume until the pointer comes up.
 */

'use client';

import { create } from 'zustand';

import type { Guide } from './snapping';

export interface SelectionState {
  /** Selected element ids, in the order they were picked. */
  selected: string[];
  /**
   * The one element whose text is live, if any.
   *
   * Selection and editing are different states, and conflating them is what
   * made a hand-placed text box unreachable: its editable text filled it edge
   * to edge, so every press landed on a caret and the box itself could never
   * be picked up. A click now selects, a double-click starts editing, and only
   * the element named here renders its text as `contentEditable`.
   */
  editing: string | null;
  /** True while a pointer gesture is running. */
  dragging: boolean;
  /** Guides to draw right now. Cleared when the gesture ends. */
  guides: Guide[];

  select: (nid: string, additive?: boolean) => void;
  selectMany: (nids: string[]) => void;
  clear: () => void;
  toggle: (nid: string) => void;
  beginEditing: (nid: string) => void;
  stopEditing: () => void;
  beginDrag: () => void;
  endDrag: () => void;
  setGuides: (guides: Guide[]) => void;
}

export const useSelection = create<SelectionState>((set, get) => ({
  selected: [],
  editing: null,
  dragging: false,
  guides: [],

  select(nid, additive = false) {
    if (!additive) {
      // Editing ends the moment the selection moves elsewhere. Left alone, a
      // box would keep a live caret while a different box wore the handles.
      set({ selected: [nid], editing: get().editing === nid ? nid : null });
      return;
    }
    get().toggle(nid);
  },

  selectMany(nids) {
    const editing = get().editing;
    set({
      selected: [...new Set(nids)],
      editing: editing && nids.includes(editing) ? editing : null,
    });
  },

  toggle(nid) {
    const { selected, editing } = get();
    const next = selected.includes(nid)
      ? selected.filter((id) => id !== nid)
      : [...selected, nid];
    set({ selected: next, editing: editing && next.includes(editing) ? editing : null });
  },

  clear() {
    set({ selected: [], editing: null });
  },

  beginEditing(nid) {
    set({ selected: [nid], editing: nid });
  },

  stopEditing() {
    set({ editing: null });
  },

  beginDrag() {
    set({ dragging: true });
  },

  endDrag() {
    set({ dragging: false, guides: [] });
  },

  setGuides(guides) {
    set({ guides });
  },
}));
