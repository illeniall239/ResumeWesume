/**
 * Document state.
 *
 * Lives outside React deliberately: the NDJSON reader applies patches from a
 * fetch loop, not from a component, so the store must be callable as
 * `useStudio.getState().applyServerPatch(...)` with no render in between.
 *
 * The counter-example is Resume-Matcher's builder: 1588 lines with ~25 useState
 * hooks, where the resume lived inside one component and a sibling panel could
 * not read or write it. Here the chat and the document are siblings that both
 * need it, so it belongs to neither.
 */

'use client';

import { create } from 'zustand';

import type { ApplyResponse, DocOp, RejectedOp, StudioDoc } from '@/contracts/doc';
import { applyOps } from '@/doc/apply';
import { applyOps as pushOps, fetchDocument } from '@/lib/api';

/** How long a changed node stays highlighted. */
const CHANGE_FLASH_MS = 1400;

interface StudioState {
  documentId: string | null;
  doc: StudioDoc | null;
  version: number;
  hash: string;
  title: string;

  loading: boolean;
  saving: boolean;
  error: string | null;

  /** Recently changed nodes, for the highlight sweep. */
  changed: Set<string>;
  /** Nodes the agent is writing to; direct editing is blocked on these. */
  locked: Set<string>;
  /** Node the user has focus in. The agent is refused here. */
  focused: string | null;
  rejected: RejectedOp[];

  load: (documentId: string) => Promise<void>;
  refresh: () => Promise<void>;
  edit: (ops: DocOp[]) => Promise<void>;
  applyServerPatch: (
    version: number,
    hash: string,
    touched: string[],
    ops?: DocOp[]
  ) => void;
  setLocked: (nids: string[], locked: boolean) => void;
  setFocus: (nid: string | null) => void;
  clearError: () => void;
}

let flashTimer: ReturnType<typeof setTimeout> | null = null;

export const useStudio = create<StudioState>((set, get) => ({
  documentId: null,
  doc: null,
  version: 0,
  hash: '',
  title: '',
  loading: false,
  saving: false,
  error: null,
  changed: new Set(),
  locked: new Set(),
  focused: null,
  rejected: [],

  async load(documentId) {
    set({ loading: true, error: null });
    try {
      const response = await fetchDocument(documentId);
      set({
        documentId,
        doc: response.doc,
        version: response.version,
        hash: response.hash,
        title: response.title,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },

  async refresh() {
    const { documentId } = get();
    if (!documentId) return;
    try {
      const response = await fetchDocument(documentId);
      set({ doc: response.doc, version: response.version, hash: response.hash });
    } catch (error) {
      set({ error: (error as Error).message });
    }
  },

  async edit(ops) {
    const { documentId, version } = get();
    if (!documentId || ops.length === 0) return;

    set({ saving: true, error: null });
    try {
      const response: ApplyResponse = await pushOps(documentId, ops, version);
      set({
        doc: response.doc,
        version: response.version,
        hash: response.hash,
        rejected: response.rejected,
        saving: false,
      });
    } catch (error) {
      const message = (error as Error).message;
      if (message.startsWith('409')) {
        // Someone else moved the document. Reload rather than clobber; a real
        // rebase of the user's in-flight keystroke lands with P5.
        await get().refresh();
        set({
          saving: false,
          error: 'The assistant changed this while you were typing; reloaded.',
        });
        return;
      }
      set({ saving: false, error: message });
    }
  },

  applyServerPatch(version, hash, touched, ops) {
    const { doc } = get();

    // Mirror the op locally so the edit is visible immediately. The turn ends
    // with a refresh that reconciles anything the mirror could not express.
    const next = doc && ops?.length ? applyOps(doc, ops) : doc;
    const changed = new Set(touched);

    set({
      doc: next,
      version,
      hash,
      changed,
      // A node that just received its patch is no longer locked.
      locked: new Set([...get().locked].filter((nid) => !changed.has(nid))),
    });

    if (flashTimer) clearTimeout(flashTimer);
    flashTimer = setTimeout(() => set({ changed: new Set() }), CHANGE_FLASH_MS);
  },

  setLocked(nids, locked) {
    const current = new Set(get().locked);
    for (const nid of nids) {
      if (locked) current.add(nid);
      else current.delete(nid);
    }
    set({ locked: current });
  },

  setFocus(nid) {
    set({ focused: nid });
  },

  clearError() {
    set({ error: null });
  },
}));
