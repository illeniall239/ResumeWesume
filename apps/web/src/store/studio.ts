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
import { textOf } from '@/doc/read';
import {
  applyOps as pushOps,
  confirmInvented,
  fetchDocument,
  fetchRevisions,
  isVersionConflict,
  renameDocument,
  reverseHistory,
} from '@/lib/api';
import { coalesce, rebase } from '@/store/pending';

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

  /**
   * Nodes the last accepted batch changed. Each one is drawn with a revision
   * cloud around it.
   *
   * These used to clear themselves after 1.4 seconds, which is right for a
   * flash and wrong for a mark you are meant to be able to inspect -- and
   * inspecting it is now the point, since clicking a cloud reveals the reading
   * it replaced. A drawing office leaves a cloud on the sheet until the next
   * issue; so does this. `clearRevisions` is that next issue.
   */
  changed: Set<string>;
  /**
   * What each clouded node said before the batch landed.
   *
   * Captured on the client, from the document it already holds, because the
   * op contract sent to the browser carries no `before` -- the server records
   * one for its own inverse, and does not ship it. Reading the outgoing value
   * a moment before it is overwritten costs nothing and needs no round trip.
   */
  superseded: Map<string, string>;
  /**
   * Text a tool call is still writing, keyed by the node or field it targets.
   *
   * A picture of work in flight, never a change: nothing here is in `doc`, no
   * op has been compiled, and a call that never balances leaves the document
   * exactly as it was. The renderer prefers a draft over the stored text so
   * the words appear as they are written; the patch that follows a moment
   * later is what actually edits anything, and clears the draft as it lands.
   */
  drafts: Map<string, string>;
  /** Accepted batches this session. The revision number in the schedule. */
  revisions: number;
  /**
   * Nodes the assistant invented while the document was still a template.
   *
   * Read from the document rather than tracked here: it survives a reload,
   * because an unchecked claim is not something that should quietly expire
   * when the tab closes.
   */
  unverified: Set<string>;
  /**
   * The mark number carried by each changed node.
   *
   * A drawing cross-references a revision by putting the same numbered delta
   * on the sheet and in the schedule; you find the change by matching the
   * number, not by tracing a line. That is what this is for -- one counter,
   * read by both the tag on the document and the row in the schedule, so the
   * connection is legible at rest instead of only under the pointer.
   */
  marks: Map<string, number>;
  /**
   * The node a schedule row is pointing at, while the pointer is on that row.
   *
   * Confirmation, not the connection itself. `marks` is what makes a row and
   * its region findable at rest; this is the pointer landing on one and the
   * other lighting up, which is cheap and answers "that one?" instantly.
   */
  spotlight: string | null;
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
  /** Light the region a schedule row names, or nothing. */
  setSpotlight: (nid: string | null) => void;
  /** Wipe the clouds. Called when a new instruction is given. */
  rename: (title: string) => Promise<void>;
  loadRevisions: (documentId: string) => Promise<void>;
  draft: (target: string, text: string) => void;
  clearDrafts: () => void;
  clearRevisions: () => void;
  /** Accept the assistant's invented lines. Empty means all of them. */
  confirmInvented: (nids?: string[]) => Promise<void>;
}

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
      // Typing into the document ends its scaffolding server-side and clears
      // the marks it covered, so this follows the server rather than guessing.
      unverified: new Set(response.doc.unverified ?? []),
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
  superseded: new Map(),
  drafts: new Map(),
  revisions: 0,
  unverified: new Set(),
  marks: new Map(),
  spotlight: null,
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
        unverified: new Set(response.doc.unverified ?? []),
        // A fresh sheet: marks from a document you were looking at a moment
        // ago must not appear on this one. The ones belonging to *this*
        // document are restored just below, from the server.
        changed: new Set(),
        superseded: new Map(),
        revisions: 0,
        marks: new Map(),
        spotlight: null,
      });

      await get().loadRevisions(documentId);
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
    // Read the outgoing text *before* the ops are applied. A moment later the
    // only copy of it is in the server's inverse, which the browser never sees.
    const outgoing = get().doc;
    const superseded = new Map(get().superseded);
    const marks = new Map(get().marks);
    for (const nid of touched) {
      // One number per node, minted the first time it changes and kept if a
      // later batch touches it again -- a region carries one mark, however
      // many times it was worked on.
      if (!marks.has(nid)) marks.set(nid, marks.size + 1);
      const was = textOf(outgoing, nid);
      // Only a genuine replacement is worth keeping. An inserted node had no
      // previous reading, and offering an empty one invites the user to open a
      // cloud that has nothing under it.
      if (was) superseded.set(nid, was);
    }

    const base = serverDoc && ops?.length ? applyOps(serverDoc, ops) : serverDoc;
    const changed = new Set(touched);

    // The real text is in now, so the draft of it must go -- left behind it
    // would sit on top of the very edit it was previewing.
    const drafts = new Map(get().drafts);
    for (const nid of touched) drafts.delete(nid);

    set({
      serverDoc: base,
      doc: base ? applyOps(base, [...pending, ...local]) : get().doc,
      version,
      hash,
      changed,
      superseded,
      marks,
      drafts,
      // The assistant may have invented these; the server decides, because it
      // is the only side that knows whether the document is still scaffolding.
      unverified: new Set(base?.unverified ?? get().doc?.unverified ?? []),
      revisions: get().revisions + 1,
      locked: new Set([...get().locked].filter((nid) => !changed.has(nid))),
    });
  },

  setSpotlight(nid) {
    set({ spotlight: nid });
  },

  async confirmInvented(nids = []) {
    const { documentId } = get();
    if (!documentId) return;
    try {
      const state = await confirmInvented(documentId, nids);
      // The server owns both facts, so they are read back rather than assumed:
      // confirming ends the scaffolding, which changes what the assistant is
      // allowed to do next.
      set({
        doc: state.doc,
        serverDoc: state.doc,
        unverified: new Set(state.doc.unverified ?? []),
      });
    } catch (cause) {
      set({ error: (cause as Error).message });
    }
  },

  /**
   * Give this document a different name.
   *
   * Optimistic, and deliberately so: a rename is a label, the field already
   * shows what was typed, and snapping it back to the old name for a moment
   * while a request lands would read as the edit being rejected. On failure it
   * is put back and the error is shown, which is the honest version of the
   * same thing.
   */
  async rename(title) {
    const documentId = get().documentId;
    const previous = get().title;
    const clean = title.trim();
    if (!documentId || !clean || clean === previous) return;

    set({ title: clean });
    try {
      const response = await renameDocument(documentId, clean);
      // The server trims and caps; take its answer rather than assuming ours
      // survived intact.
      if (get().documentId === documentId) set({ title: response.title });
    } catch (cause) {
      if (get().documentId === documentId) set({ title: previous });
      set({ error: (cause as Error).message });
    }
  },

  /**
   * Put back the marks this document was left with.
   *
   * The clouds and their readings used to live only in the browser, so a
   * reload cleared them while the conversation came back -- the record of what
   * changed on weaker footing than the dialogue about it. The op log has kept
   * both all along; this is the read.
   */
  async loadRevisions(documentId) {
    try {
      const stored = await fetchRevisions(documentId);
      // Nothing to redraw is a normal answer, and it must not undo the reset
      // above by leaving a previous document's marks in place.
      if (get().documentId !== documentId) return;

      const marks = new Map<string, number>();
      const superseded = new Map<string, string>();
      for (const entry of stored.marks) {
        marks.set(entry.nid, entry.mark);
        if (entry.before) superseded.set(entry.nid, entry.before);
      }

      set({
        revisions: stored.revision,
        marks,
        superseded,
        changed: new Set(marks.keys()),
      });
    } catch {
      // A document that will not report its history is still editable. The
      // marks are an annotation, not the artifact.
    }
  },

  draft(target, text) {
    const drafts = new Map(get().drafts);
    drafts.set(target, text);
    set({ drafts });
  },

  clearDrafts() {
    if (!get().drafts.size) return;
    set({ drafts: new Map() });
  },

  clearRevisions() {
    set({
      changed: new Set(),
      superseded: new Map(),
      marks: new Map(),
      spotlight: null,
    });
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
        // Every cloud goes. A reversal can touch any part of the document and
        // the server does not say which, so the marks left on screen would be
        // describing a state that no longer exists -- and the readings under
        // them would be offering to restore text that is already back.
        changed: new Set(),
        superseded: new Map(),
        marks: new Map(),
        spotlight: null,
      });
    } catch (cause) {
      set({ error: (cause as Error).message, saving: false });
    }
  },

  clearError() {
    set({ error: null });
  },
}));
