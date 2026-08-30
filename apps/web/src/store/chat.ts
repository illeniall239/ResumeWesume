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
import { cancelTurn, startTurn } from '@/stream/ndjson';
import { useStudio } from '@/store/studio';

export interface ToolActivity {
  callId: string;
  name: string;
  tier: string;
  status: 'running' | 'applied' | 'rejected' | 'confirm';
  label?: string;
  detail?: string;
  code?: string;
  touched?: string[];
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
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
  showThinking: boolean;

  send: (documentId: string, text: string, jobDescription?: string) => void;
  cancel: () => void;
  dismissConfirm: () => void;
  approveConfirm: (documentId: string) => void;
  toggleThinking: () => void;
  reset: () => void;
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
  showThinking: false,

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
          patch((message) =>
            message.status === 'streaming' ? { ...message, status: 'ok' } : message
          );
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

  toggleThinking() {
    set((state) => ({ showThinking: !state.showThinking }));
  },

  reset() {
    abortCurrent?.();
    set({ messages: [], streaming: false, turnId: null, error: null, confirm: null });
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
function applyEvent(
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
      patch((message) => ({
        ...message,
        activity: [
          ...message.activity,
          {
            callId: `note_${event.seq}`,
            name: String(event.guard ?? event.source ?? 'note'),
            tier: 'A',
            status: 'rejected',
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
      patch((message) => ({
        ...message,
        status: (event.status as ChatMessage['status']) ?? 'ok',
      }));
      break;

    default:
      break;
  }
}
