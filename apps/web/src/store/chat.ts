/**
 * Chat and turn state.
 *
 * Separate store from the document on purpose. Assistant text arrives token by
 * token, so a combined store would re-render the resume on every token; keeping
 * them apart means the document only re-renders when a patch actually lands.
 *
 * Events are applied through a single reducer-style switch rather than scattered
 * handlers, so the wire protocol has exactly one place that interprets it.
 */

'use client';

import { create } from 'zustand';

import type { StreamEvent } from '@/stream/ndjson';
import { clearCanvasMessages, fetchCanvasMessages } from '@/lib/api';
import { cancelTurn, startTurn } from '@/stream/ndjson';
import { useStudio } from '@/store/studio';

export interface ToolActivity {
  callId: string;
  name: string;
  tier: string;
  status: 'running' | 'applied' | 'rejected' | 'confirm' | 'done' | 'note';
  label?: string;
  detail?: string;
  code?: string;
  touched?: string[];
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  /** Which model actually answered. Only set where the server reports one. */
  model?: string;
  thinking?: string;
  activity: ToolActivity[];
  status?: 'streaming' | 'ok' | 'partial' | 'failed' | 'cancelled';
  /**
   * The snapshot taken before this turn's first edit, streamed on `done`.
   *
   * Only set when the turn actually changed something. A turn that answered a
   * question and touched nothing has a checkpoint too, but reverting to it
   * would restore a document identical to the current one -- a new version, a
   * fresh entry in the history, and no visible effect, which is the shape of a
   * control that appears broken.
   */
  checkpoint?: string;
  /** Already put back. The offer is not made twice. */
  reverted?: boolean;
  /**
   * Which version this turn acted on, and what it is called.
   *
   * The conversation is about the résumé; an edit landed on exactly one of its
   * versions. Worth saying only where there is more than one, which is what
   * the sidebar decides.
   */
  board?: string;
  boardId?: string;
}

export interface PendingConfirm {
  callId: string;
  tool: string;
  args: Record<string, unknown>;
  risk: string;
}

interface ChatState {
  messages: ChatMessage[];
  streaming: boolean;
  turnId: string | null;
  error: string | null;
  confirm: PendingConfirm | null;

  send: (documentId: string, text: string, jobDescription?: string) => void;
  cancel: () => void;
  dismissConfirm: () => void;
  approveConfirm: (documentId: string) => void;
  reset: () => void;
  /** Put the document back to how it was before this turn. */
  undoTurn: (messageId: string) => Promise<void>;
  /** Read the conversation for a canvas — every version of the résumé. */
  load: (canvasId: string) => Promise<void>;
  forget: (canvasId: string) => Promise<void>;
}

let abortCurrent: (() => void) | null = null;

/**
 * The tier each in-flight call declared, keyed by call id.
 *
 * `tool_start` carries the tier and `tool_args` carries the target, and they
 * are two events apart -- so the one that knows *where* does not know whether
 * anything will change there. Rather than re-deriving that from the tool name
 * on this side, which would put a second copy of the tool table in the client
 * and break the day a tool is added, the tier is simply remembered until its
 * arguments arrive.
 */
const tiers = new Map<string, string>();

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

export const useChat = create<ChatState>((set, get) => ({
  messages: [],
  streaming: false,
  turnId: null,
  error: null,
  confirm: null,

  send(documentId, text, jobDescription) {
    if (get().streaming || !text.trim()) return;

    const assistantId = newId();
    set((state) => ({
      messages: [
        ...state.messages,
        { id: newId(), role: 'user', text, activity: [] },
        { id: assistantId, role: 'assistant', text: '', activity: [], status: 'streaming' },
      ],
      streaming: true,
      error: null,
      confirm: null,
    }));

    const patch = (change: (message: ChatMessage) => ChatMessage) =>
      set((state) => ({
        messages: state.messages.map((message) =>
          message.id === assistantId ? change(message) : message
        ),
      }));

    const studio = useStudio.getState();
    // A new instruction ends the last one's change flash, here rather than on
    // a timer -- so a mark stays for exactly as long as it still describes the
    // current state.
    studio.clearChanged();
    // The pen comes down the moment the instruction is given, not when the
    // first tool call arrives -- a local model can think for many seconds
    // before it says anything, and an empty sheet through all of it reads as
    // nothing having happened. It waits with no target, which is what is
    // actually true until the model names one.
    studio.attend({ target: null, kind: 'waiting' });

    const handle = startTurn(
      {
        document_id: documentId,
        message: text,
        job_description: jobDescription,
        // Only the last few turns: a local model's context is small, and older
        // history is the least valuable thing competing for it.
        history: get()
          .messages.slice(-6)
          .filter((message) => message.text)
          .map((message) => ({ role: message.role, content: message.text })),
        // The node the user has focus in. The agent is refused there.
        busy_nids: studio.focused ? [studio.focused] : [],
      },
      {
        onEvent: (event) => applyEvent(event, patch, set, assistantId),
        onGap: (expected, received) =>
          set({
            error: `Missed events ${expected}-${received - 1}; reloading the document.`,
          }),
        onError: (error) => set({ error: error.message }),
        onClose: () => {
          set({ streaming: false, turnId: null });
          abortCurrent = null;
          // `done` is the polite ending and does this too, but a stream that
          // drops never sends one -- and a pen left resting on a node would
          // then claim forever that the agent is still working there.
          useStudio.getState().attend(null);
          tiers.clear();
          patch((message) => ({
            ...message,
            status: message.status === 'streaming' ? 'ok' : message.status,
            // Close anything still showing the running mark. A read-only tool
            // emits `tool_start` and never a `patch_applied` -- the protocol
            // has no `tool_end` -- so without this the step that finished
            // first kept the open ring while the edit below it was already
            // ticked, and a completed step read as unfinished. `done` rather
            // than `applied`: it completed, but it changed nothing, and the
            // check colour means a change was accepted.
            activity: message.activity.map((item) =>
              item.status === 'running' ? { ...item, status: 'done' as const } : item
            ),
          }));
          // The document store is the source of truth for content; refetch once
          // at the end rather than trusting the incremental replay.
          void useStudio.getState().refresh();
        },
      }
    );

    abortCurrent = handle.abort;
    void handle.turnId.then((id) => set({ turnId: id }));
  },

  cancel() {
    useStudio.getState().attend(null);
    const { turnId } = get();
    if (turnId) void cancelTurn(turnId);
    // Server-side cancellation is cooperative and lands on a clean boundary;
    // aborting the fetch only stops us listening.
    abortCurrent?.();
    set({ streaming: false });
  },

  dismissConfirm() {
    set({ confirm: null });
  },

  approveConfirm(documentId) {
    const pending = get().confirm;
    if (!pending) return;
    set({ confirm: null });
    // Re-issue as a fresh turn carrying the consent token. A token authorises
    // exactly one operation and does not persist beyond it.
    const token = `${pending.tool}:${pending.args.nid ?? pending.args.field ?? ''}`;
    const state = get();
    state.messages.push({
      id: newId(),
      role: 'user',
      text: 'Yes, go ahead.',
      activity: [],
    });
    startTurnWithConsent(documentId, pending, token, set, get);
  },

  reset() {
    abortCurrent?.();
    useStudio.getState().attend(null);
    tiers.clear();
    set({ messages: [], streaming: false, turnId: null, error: null, confirm: null });
  },

  /**
   * Put the document back to how it was before one turn, and say so.
   *
   * The whole turn, in one press. Undo reverses a batch and the agent commits
   * one batch per tool call, so a fourteen-edit turn is fourteen presses of it
   * -- long enough that people stop halfway and are left with a document in a
   * state nobody asked for.
   *
   * The mark is set only after the document has actually moved: a line reading
   * "reverted" above a sheet that still carries the changes would be worse
   * than no line at all.
   */
  async undoTurn(messageId) {
    const message = get().messages.find((held) => held.id === messageId);
    if (!message?.checkpoint || message.reverted) return;

    await useStudio.getState().revertTo(message.checkpoint);
    if (useStudio.getState().error) return;

    set({
      messages: get().messages.map((held) =>
        held.id === messageId ? { ...held, reverted: true } : held
      ),
    });
  },

  async load(canvasId) {
    // Never over a live turn: opening a second tab on a document that is
    // mid-stream would otherwise replace the streaming bubble with the stored
    // transcript, which does not contain it yet.
    if (get().streaming) return;

    try {
      const { messages } = await fetchCanvasMessages(canvasId);
      set({
        messages: messages.map((message) => ({
          id: message.id,
          role: message.role,
          text: message.text,
          activity: [],
          status: message.status ?? undefined,
          checkpoint: message.checkpoint ?? undefined,
          board: message.board ?? undefined,
          boardId: message.board_id ?? undefined,
        })),
      });
    } catch {
      // A conversation that will not load is not worth blocking the document
      // for. The résumé is the thing the person came for.
    }
  },

  async forget(canvasId) {
    abortCurrent?.();
    set({ messages: [], streaming: false, turnId: null, error: null, confirm: null });
    try {
      await clearCanvasMessages(canvasId);
    } catch {
      // Cleared on screen either way; a failed delete resurfaces on reload
      // rather than leaving the user staring at a chat they asked to remove.
    }
  },
}));

function startTurnWithConsent(
  documentId: string,
  pending: PendingConfirm,
  token: string,
  set: (partial: Partial<ChatState>) => void,
  get: () => ChatState
): void {
  const assistantId = newId();
  set({
    messages: [
      ...get().messages,
      { id: assistantId, role: 'assistant', text: '', activity: [], status: 'streaming' },
    ],
    streaming: true,
  });

  const patch = (change: (message: ChatMessage) => ChatMessage) =>
    set({
      messages: get().messages.map((message) =>
        message.id === assistantId ? change(message) : message
      ),
    });

  useStudio.getState().attend({ target: null, kind: 'waiting' });

  const handle = startTurn(
    {
      document_id: documentId,
      message: `Yes, ${pending.risk.toLowerCase()} Please proceed.`,
      consent_tokens: [token],
    },
    {
      onEvent: (event) => applyEvent(event, patch, set as never, assistantId),
      onError: (error) => set({ error: error.message }),
      onClose: () => {
        set({ streaming: false, turnId: null });
        useStudio.getState().attend(null);
        tiers.clear();
        void useStudio.getState().refresh();
      },
    }
  );
  abortCurrent = handle.abort;
}

/**
 * Where in the document a set of tool arguments points, if anywhere.
 *
 * Deliberately shallow. Every editing tool names its target the same two ways
 * -- a node id, or a node id plus a field -- and a read names a section, so
 * these four keys cover the registry without the client holding a copy of it.
 * A tool whose arguments name no place returns `null` and the pen simply stays
 * where it was, which is the honest answer: nothing on the sheet is happening.
 */
export function targetOf(args: Record<string, unknown> | undefined): string | null {
  if (!args) return null;
  const nid = typeof args.nid === 'string' ? args.nid : null;
  // `nid.field` is the same path `drafting` and `data-field` already use, so a
  // field edit resolves to the exact span rather than to its whole entry.
  if (nid && typeof args.field === 'string') return `${nid}.${args.field}`;
  if (nid) return nid;
  if (typeof args.target === 'string') return args.target;
  if (typeof args.section === 'string') return args.section;
  return null;
}

/**
 * The single place the wire protocol is interpreted.
 *
 * Exported for tests: this is the reducer that decides whether a line is drawn
 * as a failure, and that judgement was wrong for every advisory notice until it
 * was pinned.
 */
export function applyEvent(
  event: StreamEvent,
  patch: (change: (message: ChatMessage) => ChatMessage) => void,
  set: (partial: Partial<ChatState>) => void,
  _assistantId: string
): void {
  const studio = useStudio.getState();

  switch (event.type) {
    case 'assistant_delta':
      patch((message) => ({ ...message, text: message.text + String(event.text) }));
      break;

    case 'thinking_delta':
      patch((message) => ({
        ...message,
        thinking: (message.thinking ?? '') + String(event.text),
      }));
      break;

    case 'tool_start':
      tiers.set(String(event.call_id), String(event.tier ?? 'A'));
      patch((message) => ({
        ...message,
        activity: [
          ...message.activity,
          {
            callId: String(event.call_id),
            name: String(event.name),
            tier: String(event.tier ?? 'A'),
            status: 'running',
          },
        ],
      }));
      // Dim the node the agent is about to write to, and block editing there.
      break;

    /**
     * Validated arguments, emitted before the edit is attempted.
     *
     * This is what lets the pen reach a node *ahead* of the change rather than
     * chasing it: the server has parsed the call and knows the target, and the
     * op has not been compiled yet. Until now the client dropped this event on
     * the floor.
     */
    case 'tool_args': {
      const target = targetOf(event.args as Record<string, unknown> | undefined);
      if (target) {
        studio.attend({
          target,
          // Tier R reads and changes nothing -- the server's own word for it,
          // not a guess from the name.
          kind: tiers.get(String(event.call_id)) === 'R' ? 'reading' : 'writing',
        });
      }
      break;
    }

    case 'drafting':
      // Shown, not applied. The patch that follows is what edits the document;
      // this is only so the words appear as they are written.
      useStudio.getState().draft(String(event.target), String(event.text));
      // A target arrives with every chunk, so the pen stays on the node whose
      // text is landing even when a call rewrites several in turn.
      studio.attend({ target: String(event.target), kind: 'writing' });
      return;

    case 'board_forked': {
      // The turn has moved onto a new version, so the page follows it. The
      // patches after this land on the copy, and a plane still showing the
      // original would draw them against a document that never received them.
      studio.adoptBoard(String(event.board_id), String(event.title));
      patch((message) => ({
        ...message,
        activity: message.activity.map((item) =>
          item.callId === event.call_id
            ? {
                ...item,
                status: 'applied' as const,
                label: `started ${String(event.title)}`,
              }
            : item
        ),
      }));
      break;
    }

    case 'patch_applied': {
      const touched = (event.touched as string[]) ?? [];
      patch((message) => ({
        ...message,
        activity: message.activity.map((item) =>
          item.callId === event.call_id
            ? { ...item, status: 'applied', label: String(event.label ?? ''), touched }
            : item
        ),
      }));
      // Wherever the change actually landed. `tool_args` gets the pen there
      // first when the arguments name a place, and `drafting` keeps it on
      // streaming text -- but neither covers a tool whose arguments name no
      // node at all. `set_photo` takes an asset id and nothing else, so the
      // pen never moved for it and a picture appeared on the sheet with no
      // sign of where it came from.
      //
      // This is the general answer rather than a case for that tool: `touched`
      // is derived from the ops themselves, so anything the agent changes puts
      // the pen on it, including tools that do not exist yet.
      if (touched.length) {
        studio.attend({ target: touched[0], kind: 'writing' });
      }
      // Apply to the live document immediately: this is the "watch it work"
      // beat. The ops travel with the event precisely so the client does not
      // need a round trip to show the change.
      studio.applyServerPatch(
        event.doc_version as number,
        String(event.hash),
        touched,
        (event.ops as never) ?? []
      );
      break;
    }

    case 'patch_rejected':
      patch((message) => ({
        ...message,
        activity: message.activity.map((item) =>
          item.callId === event.call_id
            ? {
                ...item,
                status: 'rejected',
                code: String(event.code),
                detail: String(event.message),
              }
            : item
        ),
      }));
      break;

    case 'confirm_required':
      patch((message) => ({
        ...message,
        activity: message.activity.map((item) =>
          item.callId === event.call_id ? { ...item, status: 'confirm' } : item
        ),
      }));
      set({
        confirm: {
          callId: String(event.call_id),
          tool: String(event.tool),
          args: (event.args as Record<string, unknown>) ?? {},
          risk: String(event.risk ?? ''),
        },
      });
      break;

    case 'node_lock':
      studio.setLocked((event.nids as string[]) ?? [], Boolean(event.locked));
      break;

    case 'drift_suppressed':
    case 'warning':
      // Which model ran is not a warning about the resume, so it does not go in
      // the activity list at all -- rendered there it wore the same cross as a
      // rejected edit and read as a failure.
      if (event.source === 'model') {
        patch((message) => ({
          ...message,
          model: String(event.message ?? ''),
        }));
        return;
      }

      patch((message) => ({
        ...message,
        activity: [
          ...message.activity,
          {
            callId: `note_${event.seq}`,
            name: String(event.guard ?? event.source ?? 'note'),
            tier: 'A',
            // A note, not a rejection. These are the advisory lines -- "this
            // turn rewrote most of the resume", "added without support" -- that
            // exist precisely because the engine chose to report rather than
            // refuse. Marking them rejected undid that: the work had landed,
            // and the sidebar said it had failed.
            status: 'note',
            detail: String(event.detail ?? event.message ?? ''),
          },
        ],
      }));
      break;

    case 'error':
      set({ error: String(event.message) });
      patch((message) => ({ ...message, status: 'failed' }));
      break;

    case 'done':
      // A draft outlives its call only when the call never landed -- truncated
      // mid-write, or rejected. Left on screen it would be text the document
      // does not contain and undo cannot remove.
      useStudio.getState().clearDrafts();
      // Pen up. A pointer left on the sheet after the turn ends would claim
      // work is still happening there, which is the one thing it must never
      // say untruthfully.
      studio.attend(null);
      tiers.clear();
      patch((message) => ({
        ...message,
        status: (event.status as ChatMessage['status']) ?? 'ok',
        // Where this turn began. Kept only when it moved the document: see
        // `ChatMessage.checkpoint`.
        checkpoint:
          Number(event.applied) > 0 && typeof event.checkpoint_id === 'string'
            ? event.checkpoint_id
            : undefined,
      }));
      break;

    default:
      break;
  }
}
