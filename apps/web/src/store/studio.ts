/**
 * Studio state.
 *
 * Lives outside React on purpose. The streaming reader will apply patches from
 * a fetch loop, not from a component, so the store must be callable as
 * `useStudio.getState().applyServerPatch(...)` without a render in between.
 *
 * The counter-example is Resume-Matcher's builder: 1588 lines with ~25 useState
 * hooks, where the resume lived inside one component and a sibling panel could
 * not read or write it without a large refactor. Here the chat and the document
 * are siblings that both need the document, so it belongs to neither.
 */

'use client';

import { create } from 'zustand';

import type { ApplyResponse, DocOp, RejectedOp, StudioDoc } from '@/contracts/doc';
import { applyOps, fetchDocument } from '@/lib/api';

/** How long a changed node stays highlighted. */
const CHANGE_FLASH_MS = 1200;

interface StudioState {
  documentId: string | null;
  doc: StudioDoc | null;
  version: number;
  hash: string;
  title: string;

  loading: boolean;
  error: string | null;
  saving: boolean;

  /** Recently changed nodes, for the highlight sweep. */
  changed: Set<string>;
  /** Nodes the agent is writing to; direct editing is blocked on these. */
  locked: Set<string>;
  /** Node the user has focus in. The agent is refused here. */
  focused: string | null;

  rejected: RejectedOp[];

  load: (documentId: string) => Promise<void>;
  edit: (ops: DocOp[]) => Promise<void>;
  setFocus: (nid: string | null) => void;
  clearError: () => void;
}

export const useStudio = create<StudioState>((set, get) => ({
  documentId: null,
  doc: null,
  version: 0,
  hash: '',
  title: '',
  loading: false,
  error: null,
  saving: false,
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

  async edit(ops) {
    const { documentId, version } = get();
    if (!documentId || ops.length === 0) return;

    set({ saving: true, error: null });
    try {
      const response: ApplyResponse = await applyOps(documentId, ops, version);
      const touched = new Set(response.applied.flatMap((entry) => entry.touched));
      set({
        doc: response.doc,
        version: response.version,
        hash: response.hash,
        rejected: response.rejected,
        changed: touched,
        saving: false,
      });
      if (touched.size > 0) {
        setTimeout(() => {
          // Only clear if nothing newer has landed, or a fast follow-up edit
          // would have its highlight cut short by this timer.
          if (get().changed === touched) set({ changed: new Set() });
        }, CHANGE_FLASH_MS);
      }
    } catch (error) {
      const message = (error as Error).message;
      // A conflict is recoverable: refetch rather than surfacing a scary error.
      // Rebasing the user's in-flight edit lands with inline editing in P5.
      if (message.includes('409')) {
        await get().load(documentId);
        set({ saving: false, error: 'Document changed elsewhere; reloaded.' });
        return;
      }
      set({ saving: false, error: message });
    }
  },

  setFocus(nid) {
    set({ focused: nid });
  },

  clearError() {
    set({ error: null });
  },
}));
