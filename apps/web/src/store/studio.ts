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
import {
  applyOps as pushOps,
  fetchDocument,
  isVersionConflict,
  reverseHistory,
} from '@/lib/api';
import { coalesce, rebase } from '@/store/pending';

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

  /**
   * The last document the server confirmed.
   *
   * `doc` is this with everything still in flight replayed on top, so a drag
   * shows instantly and an agent patch arriving mid-gesture lands *underneath*
   * the user's work rather than yanking it away.
   */
  serverDoc: StudioDoc | null;
  /** Sent, not yet acknowledged. */
  pending: DocOp[];
  /** Made locally, not yet sent. */
  local: DocOp[];

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
  /** Apply locally and show it now; send at the next commit boundary. */
  stage: (ops: DocOp[]) => void;
  /** Send everything staged. Called on pointer-up, on blur, before a turn. */
  flush: () => Promise<void>;
  applyServerPatch: (
    version: number,
    hash: string,
    touched: string[],
    ops?: DocOp[]
  ) => void;
  setLocked: (nids: string[], locked: boolean) => void;
  setFocus: (nid: string | null) => void;
  /** Reverse the last committed batch, or put it back. */
  history: (direction: 'undo' | 'redo') => Promise<void>;
  clearError: () => void;
}

let flashTimer: ReturnType<typeof setTimeout> | null = null;

type Setter = (partial: Partial<StudioState>) => void;
type Getter = () => StudioState;

/**
 * Push one batch, resolving a conflict by rebasing rather than reloading.
 *
 * The server's 409 already carries `ops_since`, so a conflict is recoverable:
 * replay what the other writer did onto `serverDoc`, drop anything of ours that
 * addressed a node they deleted, and re-send. The previous behaviour reloaded
 * the document and discarded the user's edit, which is the correct answer only
 * if you have thrown the conflict payload away — which it did.
 */
async function send(set: Setter, get: Getter, batch: DocOp[]): Promise<void> {
  const { documentId, version } = get();
  if (!documentId) return;

  try {
    const response: ApplyResponse = await pushOps(documentId, batch, version);
    set({
      serverDoc: response.doc,
      version: response.version,
      hash: response.hash,
      rejected: response.rejected,
      pending: [],
      saving: false,
    });
    // Anything staged while this was in flight is replayed on the new base.
    const { local, serverDoc } = get();
    set({ doc: local.length && serverDoc ? applyOps(serverDoc, local) : response.doc });
    if (local.length) void get().flush();
  } catch (error) {
    if (isVersionConflict(error)) {
      const { serverDoc } = get();
      const theirs = error.detail.ops_since ?? [];
      const rebased = rebase(batch, theirs);
      const base = serverDoc ? applyOps(serverDoc, theirs) : serverDoc;

      set({
        serverDoc: base,
        version: error.detail.current_version,
        pending: [],
        saving: false,
        // Re-queue ahead of anything staged since, preserving order.
        local: [...rebased, ...get().local],
      });
      set({ doc: base ? applyOps(base, get().local) : get().doc });
      await get().flush();
      return;
    }

    // Keep the work: it is still staged and still rendered, so a transient
    // failure does not silently discard a gesture.
    set({
      saving: false,
      pending: [],
      local: [...batch, ...get().local],
      error: (error as Error).message,
    });
  }
}

export const useStudio = create<StudioState>((set, get) => ({
  documentId: null,
  doc: null,
  version: 0,
  hash: '',
  title: '',
  loading: false,
  saving: false,
  error: null,
  serverDoc: null,
  pending: [],
  local: [],
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
        serverDoc: response.doc,
        pending: [],
        local: [],
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
      set({
        doc: response.doc,
        serverDoc: response.doc,
        pending: [],
        local: [],
        version: response.version,
        hash: response.hash,
      });
    } catch (error) {
      set({ error: (error as Error).message });
    }
  },

  /**
   * Apply ops locally and show them at once; send at the next boundary.
   *
   * A drag cannot round-trip per pointer event, so what the user sees is the
   * server's document with everything in flight replayed over it. Nothing is
   * lost if a send fails: the ops are still in `local` and the rendered
   * document still includes them.
   */
  stage(ops) {
    if (!ops.length) return;
    const { serverDoc, pending, local } = get();
    const next = [...local, ...ops];
    set({
      local: next,
      doc: serverDoc ? applyOps(serverDoc, [...pending, ...next]) : get().doc,
    });
  },

  /**
   * Send everything staged, one batch at a time.
   *
   * Single-flight: a second call while a send is outstanding does nothing and
   * the work waits its turn, because two batches racing against the same
   * version would make one of them a guaranteed conflict.
   */
  async flush() {
    const { documentId, local, pending, saving } = get();
    if (!documentId || saving || pending.length || !local.length) return;

    const batch = coalesce(local);
    if (!batch.length) {
      set({ local: [] });
      return;
    }

    set({ saving: true, error: null, pending: batch, local: [] });
    await send(set, get, batch);
  },

  async edit(ops) {
    get().stage(ops);
    await get().flush();
  },

  applyServerPatch(version, hash, touched, ops) {
    const { serverDoc, pending, local } = get();
    // The agent's work lands on the *server* document. Rebuilding the rendered
    // one on top of it means an in-progress drag is replayed over the patch
    // rather than being wiped by it -- the failure where a bullet the assistant
    // rewrote mid-gesture would snap the box back under the pointer.
    const base = serverDoc && ops?.length ? applyOps(serverDoc, ops) : serverDoc;
    const changed = new Set(touched);

    set({
      serverDoc: base,
      doc: base ? applyOps(base, [...pending, ...local]) : get().doc,
      version,
      hash,
      changed,
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

  /**
   * Undo or redo one committed batch.
   *
   * Server-side, because the inverse of every op is already recorded there and
   * a second implementation of inversion in TypeScript is exactly what
   * `doc/apply.ts` forbids itself from becoming. One `POST /ops` is one
   * version is one undo unit, so this reverses a direct edit and an agent turn
   * through the same path.
   */
  async history(direction) {
    const { documentId } = get();
    if (!documentId) return;
    set({ saving: true, error: null });
    try {
      const result = await reverseHistory(documentId, direction);
      if (!result) {
        // An empty stack is an ordinary state, not a failure to report.
        set({ saving: false });
        return;
      }
      set({
        doc: result.doc,
        // The server document must move too, or the next render rebuilds
        // `doc` from a stale base and the reversal appears not to have
        // happened -- the request succeeds and the screen does not change.
        serverDoc: result.doc,
        pending: [],
        local: [],
        version: result.version,
        hash: result.hash,
        saving: false,
        // Sweep the highlight across everything, since a reversal can touch
        // any part of the document and the server does not say which.
        changed: new Set(),
      });
    } catch (cause) {
      set({ error: (cause as Error).message, saving: false });
    }
  },

  clearError() {
    set({ error: null });
  },
}));
