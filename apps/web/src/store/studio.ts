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
  isVersionConflict,
  renameDocument,
  reverseHistory,
  revertToCheckpoint,
  setJobDescription,
  setJobDescriptionFromPdf,
} from '@/lib/api';
import { useCanvas } from '@/store/canvas';
import { coalesce, rebase } from '@/store/pending';

/**
 * Somewhere on the sheet the agent is currently working, and in what way.
 *
 * `target` is whatever the protocol named: a node id, an `nid.field` path, or
 * a section key from a read. Resolving it to an element is the overlay's job,
 * because only the overlay knows what is actually on screen.
 */
export interface Attention {
  /**
   * Where on the sheet, or `null` while the agent has not named a place yet.
   *
   * A null target is the honest shape of waiting: the turn has started, the
   * model is thinking, and nothing has been said about the document. The pen
   * is on the sheet and visibly idle -- which is true -- rather than touring
   * sections to imply it is reading them, which is not.
   */
  target: string | null;
  /**
   * `waiting` is a turn in flight with no target yet; `reading` is a tier-R
   * call, which changes nothing; `writing` is a call that will land.
   */
  kind: 'waiting' | 'reading' | 'writing';
}

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
  /**
   * Whether everything staged since the last send is a re-derivation.
   *
   * The measure pass corrects frame geometry to match what the browser
   * rendered. That is not something a person did, so it must not become an
   * undo unit -- it lands after almost every edit that changes how much room a
   * line takes, and counting it cost a press per edit and killed redo on the
   * press after every undo. Decided by the first op of a batch and revoked by
   * any real gesture joining it, because a batch carrying both is undoable.
   */
  derived: boolean;
  /** Made locally, not yet sent. */
  local: DocOp[];

  /**
   * Nodes the last accepted batch changed, so the renderer can flash them.
   *
   * Held until the next instruction rather than cleared on a timer: a flash
   * that expires while you are still reading the reply tells you nothing.
   * `clearChanged` is what ends it.
   */
  changed: Set<string>;
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
  /**
   * Nodes the assistant invented while the document was still a template.
   *
   * Read from the document rather than tracked here: it survives a reload,
   * because an unchecked claim is not something that should quietly expire
   * when the tab closes.
   */
  unverified: Set<string>;
  /**
   * The posting this résumé is aimed at, verbatim.
   *
   * Held here rather than sent with each message. Tailoring is a conversation
   * -- you ask, you read it back, you ask again -- and a posting carried on the
   * turn survived exactly one exchange, after which every follow-up worked with
   * no idea what the sheet was being aimed at.
   */
  jobDescription: string | null;
  /**
   * Where the agent is working, right now.
   *
   * Not a guess and not an idle animation: the protocol reports a target
   * before the edit is attempted -- `tool_args` carries validated arguments
   * ahead of execution, and `drafting` names the node whose text is arriving.
   * So a pointer drawn here is always somewhere the agent genuinely is, which
   * is the only reason it earns a place on a sheet whose whole claim is that
   * every mark on it is evidence.
   *
   * `reading` is a tier-R call, which changes nothing; `writing` is a call
   * that will land. The distinction is the server's own, taken from the tier
   * it declares, never inferred from a tool name here.
   */
  attention: Attention | null;
  /** Nodes the agent is writing to; direct editing is blocked on these. */
  locked: Set<string>;
  /** Node the user has focus in. The agent is refused here. */
  focused: string | null;
  rejected: RejectedOp[];

  load: (documentId: string) => Promise<void>;
  refresh: () => Promise<void>;
  edit: (ops: DocOp[]) => Promise<void>;
  /** Apply locally and show it now; send at the next commit boundary. */
  stage: (ops: DocOp[], derived?: boolean) => void;
  /** Send everything staged. Called on pointer-up, on blur, before a turn. */
  flush: () => Promise<void>;
  /** Commit a geometry correction the measure pass derived; never an undo unit. */
  relayout: (ops: DocOp[]) => Promise<void>;
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
  /**
   * Aim the résumé at a posting; empty text stops aiming it at one.
   *
   * Called when a message pasted into the chat reads as an advert rather than
   * an instruction, and by the cross on the line above the field.
   */
  aimAt: (text: string) => Promise<void>;
  /** The same, read out of a job-board PDF dropped on the chat. */
  aimAtPdf: (file: File) => Promise<void>;
  /**
   * Put one board back to how it was before an agent turn.
   *
   * `boardId` names which, because a turn can move between versions and the
   * board being restored is not always the one on screen. Restoring a board
   * that is not open updates the plane and leaves the live document alone.
   */
  /**
   * Put a board back to a snapshot, returning the way out of it.
   *
   * The checkpoint handed back holds where the board stood *before* this call,
   * so restoring it undoes the revert. Returned rather than looked up later
   * because this is the only moment it is free -- the server wrote it as the
   * inverse of the restore a line before answering.
   *
   * Null if nothing moved: no board, or the revert failed.
   */
  revertTo: (checkpointId: string, boardId?: string) => Promise<string | null>;
  clearError: () => void;
  /** Point at where the agent is working. `null` puts the pen up. */
  attend: (attention: Attention | null) => void;
  /**
   * Follow the turn onto a version it has just started.
   *
   * Lives here rather than in the canvas store because the chat reducer is the
   * one place that interprets the wire protocol, and it already reaches for
   * `useStudio`. This loads the new board and puts it on the plane; the canvas
   * store's `adopt` is what makes it visible and selected.
   */
  adoptBoard: (boardId: string, title: string) => void;
  /**
   * A version the assistant has renamed.
   *
   * Applied to the plane always, and to the rails only when it is the version
   * on screen -- the title in the bar names the open board, and writing
   * another board's name into it would say the wrong thing about the résumé
   * being looked at.
   */
  renameBoard: (boardId: string, title: string) => void;
  rename: (title: string) => Promise<void>;
  draft: (target: string, text: string) => void;
  clearDrafts: () => void;
  /** Resolve once nothing is staged or in flight, so a reader of the saved
   *  document (the PDF export) sees the latest edit. */
  settle: () => Promise<void>;
  /** Drop the change flash. Called when a new instruction is given. */
  clearChanged: () => void;
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
async function send(
  set: Setter,
  get: Getter,
  batch: DocOp[],
  actor: 'user' | 'layout' = 'user'
): Promise<void> {
  const { documentId, version } = get();
  if (!documentId) return;

  try {
    const response: ApplyResponse = await pushOps(documentId, batch, version, actor);
    set({
      serverDoc: response.doc,
      version: response.version,
      hash: response.hash,
      rejected: response.rejected,
      pending: [],
      saving: false,
      // Typing into the document ends its scaffolding server-side, so this
      // follows the server rather than guessing.
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
  derived: false,
  local: [],
  changed: new Set(),
  drafts: new Map(),
  unverified: new Set(),
  jobDescription: null,
  attention: null,
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
        jobDescription: response.job_description ?? null,
        // A fresh sheet: the change flash from a document you were looking at
        // a moment ago must not appear on this one.
        changed: new Set(),
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
  stage(ops, derived = false) {
    if (!ops.length) return;
    const { serverDoc, pending, local } = get();
    const next = [...local, ...ops];
    set({
      local: next,
      derived: local.length === 0 ? derived : get().derived && derived,
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

    const derived = get().derived;
    set({ saving: true, error: null, pending: batch, local: [], derived: false });
    await send(set, get, batch, derived ? 'layout' : 'user');
  },

  async edit(ops) {
    get().stage(ops);
    await get().flush();
  },

  /**
   * Send a batch the measure pass produced, marked as derived.
   *
   * Same path as `edit` in every other respect -- one flight, one version, the
   * same conflict handling -- and different in the one way that matters: the
   * server files it under `layout`, so undo walks straight past it to the edit
   * the person actually made.
   */
  async relayout(ops) {
    get().stage(ops, true);
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
      drafts,
      // The assistant may have invented these; the server decides, because it
      // is the only side that knows whether the document is still scaffolding.
      unverified: new Set(base?.unverified ?? get().doc?.unverified ?? []),
      locked: new Set([...get().locked].filter((nid) => !changed.has(nid))),
    });
  },


  adoptBoard(boardId, title) {
    // Read in full rather than assembled from what the event carried: the copy
    // has its own version, hash and unverified marks, and the next patch is
    // compare-and-set against exactly those.
    void fetchDocument(boardId)
      .then((board) => {
        useCanvas.getState().adopt(board);
        set({
          documentId: board.id,
          doc: board.doc,
          serverDoc: board.doc,
          pending: [],
          local: [],
          version: board.version,
          hash: board.hash,
          title: board.title,
          jobDescription: board.job_description ?? null,
          unverified: new Set(board.doc.unverified ?? []),
          changed: new Set(),
        });
      })
      .catch(() => {
        // The turn goes on regardless; the version exists on the server and
        // will be there on the next load. Failing loudly here would put an
        // error over a résumé that is fine.
        set({ error: `Could not open ${title}. Reload to see it.` });
      });
  },

  renameBoard(boardId, title) {
    const held = useCanvas.getState().boards.find((board) => board.id === boardId);
    if (held) useCanvas.getState().absorb({ ...held, title });
    if (get().documentId === boardId) set({ title });
  },

  attend(attention) {
    set({ attention });
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

  draft(target, text) {
    const drafts = new Map(get().drafts);
    drafts.set(target, text);
    set({ drafts });
  },

  settle() {
    const clean = () => {
      const st = get();
      return !st.saving && st.pending.length === 0 && st.local.length === 0;
    };
    return new Promise<void>((resolve) => {
      // A field committing on blur stages its edit on the next tick, so give
      // that a frame to happen before deciding the document is clean --
      // otherwise a value typed a moment ago is judged "saved" while its POST
      // has not even been queued.
      const start =
        typeof requestAnimationFrame === 'function'
          ? requestAnimationFrame
          : (fn: () => void) => setTimeout(fn, 0);
      start(() => {
        if (clean()) return resolve();
        let done = false;
        const finish = () => {
          if (done) return;
          done = true;
          unsub();
          clearTimeout(cap);
          resolve();
        };
        const unsub = useStudio.subscribe(() => {
          if (clean()) finish();
        });
        // Never wedge the export: a save that failed leaves its ops queued in
        // `local`, which would keep this pending forever. After a moment, let
        // the export go with whatever did save; the save error surfaces on its
        // own path.
        const cap = setTimeout(finish, 4000);
      });
    });
  },

  clearDrafts() {
    // Locks go with them. Both say "a call is writing here", so one outliving
    // the other means either text nobody can see the source of, or a node the
    // user cannot type into with nothing writing to it -- and the second is
    // the worse half, because it looks like the editor is broken.
    if (!get().drafts.size && !get().locked.size) return;
    set({ drafts: new Map(), locked: new Set() });
  },

  clearChanged() {
    set({ changed: new Set() });
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
   * `doc/apply.ts` forbids itself from becoming.
   *
   * One `POST /ops` is one version is one undo unit -- a *batch*, which is not
   * the same as a turn. The agent loop applies once per tool call, so a turn
   * that made fourteen edits is fourteen of these. `revertTo` is what undoes a
   * turn whole.
   */
  async history(direction) {
    const { documentId, saving, local } = get();
    if (!documentId) return;
    // Not while a batch is in flight, and not on top of one still staged.
    // Both write, and the two replies race to set `version` -- the loser
    // leaves the client holding a stale one, so the next keystroke conflicts.
    // Worse, the staged work is discarded below, so an undo pressed a moment
    // after typing threw the typing away and then reversed something older.
    if (saving) return;
    if (local.length) await get().flush();

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
        // The flash goes. A reversal can touch any part of the document and
        // the server does not say which, so marks left on screen would be
        // describing a state that no longer exists.
        changed: new Set(),
      });
    } catch (cause) {
      set({ error: (cause as Error).message, saving: false });
    }
  },

  /**
   * Aim the résumé at a posting.
   *
   * Not an op, and so not part of the document's version: the posting changes
   * no word on the page, and routing it through `/ops` would put it in the
   * undo stack between two real edits and hand a conflict to every open
   * editor. Same reasoning, same path, as renaming.
   */
  async aimAt(text) {
    const { documentId } = get();
    if (!documentId) return;
    set({ error: null });
    try {
      const result = await setJobDescription(documentId, text);
      set({ jobDescription: result.job_description ?? null });
    } catch (cause) {
      set({ error: (cause as Error).message });
    }
  },

  async aimAtPdf(file) {
    const { documentId } = get();
    if (!documentId) return;
    set({ error: null });
    try {
      const result = await setJobDescriptionFromPdf(documentId, file);
      set({ jobDescription: result.job_description ?? null });
    } catch (cause) {
      // The server names the real problem -- a scan with no text layer, a file
      // that is not a PDF -- so it is shown rather than replaced with
      // something generic.
      set({ error: (cause as Error).message });
    }
  },

  /**
   * Put the document back to how it was before one agent turn.
   *
   * The one thing here that knows where a turn began. Ops are recorded per
   * tool call, so once a turn has ended nothing on this side can say which of
   * the last fourteen versions was the one the instruction started from -- but
   * the loop takes a snapshot before its first mutation and streams its id, so
   * the answer is carried rather than reconstructed.
   *
   * Everything in flight is dropped. Local edits made *during* the turn would
   * otherwise be replayed on top of a document that no longer has the nodes
   * they name, which is a rejection at best and a mangled sheet at worst.
   */
  async revertTo(checkpointId, boardId) {
    const { documentId } = get();
    const target = boardId ?? documentId;
    if (!target) return null;
    set({ saving: true, error: null });
    try {
      const result = await revertToCheckpoint(target, checkpointId);

      // A board the turn reached but which is not the one on screen. It still
      // has to go back, and the plane has to show that it did -- but nothing
      // about the live document changes, and overwriting it with another
      // board's contents is the bug this branch exists to avoid.
      if (target !== documentId) {
        useCanvas.getState().absorb(result);
        set({ saving: false });
        return result.redo_checkpoint;
      }

      useCanvas.getState().absorb(result);
      set({
        doc: result.doc,
        // As with `history`: the server document has to move too, or the next
        // render rebuilds `doc` from a stale base and the reversal looks like
        // it never happened.
        serverDoc: result.doc,
        pending: [],
        local: [],
        version: result.version,
        hash: result.hash,
        saving: false,
        // A turn can touch any part of the sheet, so marks left behind would
        // be describing a state that has just stopped existing.
        changed: new Set(),
        drafts: new Map(),
        unverified: new Set(result.doc.unverified ?? []),
      });
      return result.redo_checkpoint;
    } catch (cause) {
      set({ error: (cause as Error).message, saving: false });
      return null;
    }
  },

  clearError() {
    set({ error: null });
  },
}));
