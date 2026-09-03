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
import { clearMessages, fetchMessages } from '@/lib/api';
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
  load: (documentId: string) => Promise<void>;
  forget: (documentId: string) => Promise<void>;
}

let abortCurrent: (() => void) | null = null;

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
    // A new instruction is a new issue of the drawing. The clouds from the
    // last one come off the sheet here rather than on a timer, so a mark stays
    // readable for exactly as long as it still describes the current state.
    studio.clearRevisions();

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
    set({ messages: [], streaming: false, turnId: null, error: null, confirm: null });
  },

  async load(documentId) {
    // Never over a live turn: opening a second tab on a document that is
    // mid-stream would otherwise replace the streaming bubble with the stored
    // transcript, which does not contain it yet.
    if (get().streaming) return;

    try {
      const { messages } = await fetchMessages(documentId);
      set({
        messages: messages.map((message) => ({
          id: message.id,
          role: message.role,
          text: message.text,
          activity: [],
          status: message.status ?? undefined,
        })),
      });
    } catch {
      // A conversation that will not load is not worth blocking the document
      // for. The résumé is the thing the person came for.
    }
  },

  async forget(documentId) {
    abortCurrent?.();
    set({ messages: [], streaming: false, turnId: null, error: null, confirm: null });
    try {
      await clearMessages(documentId);
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
        void useStudio.getState().refresh();
      },
    }
  );
  abortCurrent = handle.abort;
}

/** The single place the wire protocol is interpreted. */
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

    case 'drafting':
      // Shown, not applied. The patch that follows is what edits the document;
      // this is only so the words appear as they are written.
      useStudio.getState().draft(String(event.target), String(event.text));
      return;

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
      patch((message) => ({
        ...message,
        status: (event.status as ChatMessage['status']) ?? 'ok',
      }));
      break;

    default:
      break;
  }
}
