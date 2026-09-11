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
import { readsAsAPosting } from '@/chat/posting';

/**
 * The node the caret is actually in, or none.
 *
 * The one input to the agent's "is this line busy" gate, read from the live
 * DOM so it cannot desync. A text node carries `data-nid`; a field carries
 * `data-field` as `nid.field`, and the gate keys on the bare nid, so the part
 * before the dot is what it wants. Anything else focused -- the composer, a
 * button, the body after a re-render -- means nobody is editing the résumé.
 */
export function editingNode(): string[] {
  if (typeof document === 'undefined') return [];
  const active = document.activeElement as HTMLElement | null;
  if (!active || active.getAttribute('contenteditable') === null) return [];
  const field = active.getAttribute('data-field');
  const nid = active.getAttribute('data-nid') ?? (field ? field.split('.')[0] : null);
  return nid ? [nid] : [];
}
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
   * Every version this turn touched, and where each stood before it.
   *
   * A list because a turn can move between versions: "add Rust to the Stripe
   * one" edits a board the turn did not start on, and putting it back means
   * putting back every board it reached.
   *
   * Only set when the turn actually changed something. A turn that answered a
   * question and touched nothing has snapshots too, but restoring one would
   * write a new version and change nothing on screen -- which is the shape of
   * a control that appears broken.
   */
  checkpoints?: { boardId: string; checkpointId: string }[];
  /**
   * Where to put every board back to, once this turn has been undone.
   *
   * The counterpart of `checkpoints`, and the same kind of thing: a revert
   * writes the state it replaced as a snapshot of its own, so putting a turn
   * back needs no second mechanism -- only the other id. The two swap places
   * on every press, which is what lets undo and redo be pressed alternately
   * rather than once each.
   */
  redo?: { boardId: string; checkpointId: string }[];
  /** Currently undone, so the offer on it is to put it back. */
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
  /** And put it back, for a turn that has been undone. */
  redoTurn: (messageId: string) => Promise<void>;
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

/** How much of an older message is worth carrying forward. */
const HISTORY_CHARS = 600;

function clip(text: string): string {
  return text.length <= HISTORY_CHARS ? text : `${text.slice(0, HISTORY_CHARS - 1)}…`;
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

    // A posting pasted into the chat is what the sheet is aimed at from now
    // on. Kept on the document rather than read off this one message, because
    // tailoring is a conversation -- you paste it, you ask, you read it back,
    // you ask again -- and only the first of those turns contains the advert.
    //
    // Compared against what the document is already aimed at, so a follow-up
    // is not a round trip for no change. Not awaited: the turn carries the
    // text itself, so the store catching up only redraws the line above the
    // field.
    const pasted =
      readsAsAPosting(text) && text.trim() !== (studio.jobDescription ?? '').trim();
    if (pasted) void studio.aimAt(text);

    const handle = startTurn(
      {
        document_id: documentId,
        message: text,
        job_description: pasted ? text : jobDescription,
        // Only the last few turns: a local model's context is small, and older
        // history is the least valuable thing competing for it. Clipped as
        // well as counted, because a job posting is pasted into this box now
        // and six of those unabridged is the whole context window gone -- on
        // the one thing already sent in full under <job_description>.
        history: get()
          .messages.slice(-6)
          .filter((message) => message.text)
          .map((message) => ({ role: message.role, content: clip(message.text) })),
        // The node the caret is genuinely in right now. Read from the DOM,
        // not from the `focused` store flag: that flag is set on focus and
        // cleared on blur, and a line unmounted while focused -- which the
        // skills list does on any reorder or re-render -- never fires its
        // blur, so the flag sticks on a line that is gone. Every later turn
        // then sent that stale nid and the agent was refused on a line
        // nobody was editing. `document.activeElement` cannot go stale: if
        // the caret is not in an editable node, nothing is busy.
        busy_nids: editingNode(),
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
    if (!message?.checkpoints?.length || message.reverted) return;
    await travel(messageId, message.checkpoints, true, set, get);
  },

  /**
   * And back again.
   *
   * Undo without redo is a trapdoor: taking the offer is the only way to find
   * out what the turn did, and a fourteen-edit turn undone by mistake is
   * fourteen edits to type back by hand -- which is the exact cost `undoTurn`
   * exists to remove, pointed the other way.
   *
   * The same call as undo, at the snapshot the revert left behind. Nothing
   * here knows which direction it is going; the two lists swap and the mark
   * flips.
   */
  async redoTurn(messageId) {
    const message = get().messages.find((held) => held.id === messageId);
    if (!message?.redo?.length || !message.reverted) return;
    await travel(messageId, message.redo, false, set, get);
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
          // From the server, in the shape the reducer builds live. The two are
          // the same list read twice: the running, drafting and confirming
          // states a turn passes through belong to the stream, and a turn that
          // is over has none of them left.
          thinking: message.thinking ?? undefined,
          activity: (message.activity ?? []).map((item) => ({
            callId: item.call_id,
            name: item.name,
            tier: item.tier,
            status: item.status,
            label: item.label,
            detail: item.detail,
            code: item.code,
            touched: item.touched,
          })),
          status: message.status ?? undefined,
          checkpoints: message.checkpoints?.length
            ? message.checkpoints.map((point) => ({
                boardId: point.board_id,
                checkpointId: point.checkpoint_id,
              }))
            : undefined,
          redo: message.redo?.length
            ? message.redo.map((point) => ({
                boardId: point.board_id,
                checkpointId: point.checkpoint_id,
              }))
            : undefined,
          // From the log, not from this session: a turn undone before the page
          // was reloaded is still undone, and offering to undo it again would
          // put the résumé back where it already is.
          reverted: message.reverted || undefined,
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
  // A `field` with no node is `set_personal_info`, whose fields render as
  // `personal.<name>` -- the same path the header's own `data-field` carries.
  if (typeof args.field === 'string') return `personal.${args.field}`;

  // The entry a line is being added to or reordered inside. `add_bullet` and
  // `reorder_bullets` name no node of their own, because the node they are
  // about does not exist yet or is not one node -- but the change appears
  // inside the parent either way.
  if (typeof args.parent === 'string') return args.parent;
  // A whole section, by key: `set_section` shows, hides or reorders one, and
  // the section frame carries `data-section`.
  if (typeof args.key === 'string') return args.key;
  // A layout gesture over several boxes at once. The first is where the eye
  // should be; the rest move with it.
  if (Array.isArray(args.nids) && typeof args.nids[0] === 'string') return args.nids[0];
  // The sheet something is being placed on or taken off. Coarser than the
  // rest, and the best there is: the box does not exist yet, so there is no
  // finer thing to point at until `touched` names it.
  if (typeof args.page === 'string') return args.page;
  return null;
}

/**
 * The single place the wire protocol is interpreted.
 *
 * Exported for tests: this is the reducer that decides whether a line is drawn
 * as a failure, and that judgement was wrong for every advisory notice until it
 * was pinned.
 */
/**
 * Move a turn's boards to a set of snapshots, and record the way back.
 *
 * One function for both directions because they are one operation: a revert
 * hands back the state it replaced, so the list to press next is always the
 * one this press produced. Written separately they drifted immediately -- the
 * first version of redo forgot to record its own way back, so a turn could be
 * put back exactly once and then neither button did anything.
 */
async function travel(
  messageId: string,
  points: { boardId: string; checkpointId: string }[],
  reverted: boolean,
  set: (partial: Partial<ChatState>) => void,
  get: () => ChatState
): Promise<void> {
  // Every board it touched, not the one that happens to be open. A turn that
  // moved between versions changed both, and putting back only the one on
  // screen would leave the other quietly edited.
  const back: { boardId: string; checkpointId: string }[] = [];
  for (const point of points) {
    const wayBack = await useStudio.getState().revertTo(point.checkpointId, point.boardId);
    if (useStudio.getState().error) return;
    if (wayBack) back.push({ boardId: point.boardId, checkpointId: wayBack });
  }

  set({
    messages: get().messages.map((held) =>
      held.id === messageId
        ? {
            ...held,
            reverted,
            // The lists swap. What was pressed becomes the way back, and the
            // way back becomes what to press.
            ...(reverted ? { redo: back } : { checkpoints: back }),
          }
        : held
    ),
  });
}

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

    case 'board_switched': {
      // The turn moved onto another version. Same reasoning as a fork: the
      // patches after this land there, and a page still showing the previous
      // one would draw them against a document that never received them.
      studio.adoptBoard(String(event.board_id), String(event.title));
      patch((message) => ({
        ...message,
        activity: message.activity.map((item) =>
          item.callId === event.call_id
            ? {
                ...item,
                status: 'applied' as const,
                label: `moved to ${String(event.title)}`,
              }
            : item
        ),
      }));
      break;
    }

    case 'board_renamed': {
      // The name is what both the reader and the assistant refer to a version
      // by, so the plane and the rails have to show the new one at once.
      studio.renameBoard(String(event.board_id), String(event.title));
      patch((message) => ({
        ...message,
        activity: message.activity.map((item) =>
          item.callId === event.call_id
            ? {
                ...item,
                status: 'applied' as const,
                label: `renamed it ${String(event.title)}`,
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
      // does not contain and undo cannot remove. The lock that went with it
      // goes too: the server releases every node as its call settles, and this
      // is the backstop for a turn whose stream died before it could.
      useStudio.getState().clearDrafts();
      // Pen up. A pointer left on the sheet after the turn ends would claim
      // work is still happening there, which is the one thing it must never
      // say untruthfully.
      studio.attend(null);
      tiers.clear();
      patch((message) => ({
        ...message,
        status: (event.status as ChatMessage['status']) ?? 'ok',
        // Every board this turn touched. Kept only when it moved something:
        // see `ChatMessage.checkpoints`.
        checkpoints:
          Number(event.applied) > 0
            ? ((event.checkpoints as { board_id: string; checkpoint_id: string }[]) ?? [])
                .map((point) => ({
                  boardId: point.board_id,
                  checkpointId: point.checkpoint_id,
                }))
                // An older server sends one id and no list.
                .concat(
                  typeof event.checkpoint_id === 'string' && !event.checkpoints
                    ? [
                        {
                          boardId: useStudio.getState().documentId ?? '',
                          checkpointId: event.checkpoint_id,
                        },
                      ]
                    : []
                )
            : undefined,
      }));
      break;

    default:
      break;
  }
}
