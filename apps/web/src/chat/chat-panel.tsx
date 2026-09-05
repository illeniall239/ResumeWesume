'use client';

/**
 * The assistant.
 *
 * The conversation lives here, in the sidebar, and so does the box you type
 * into. A brief version of this app moved the input out to a command line under
 * the drawing on the argument that a composer in a left rail is the generic
 * chat-beside-preview arrangement; that was true of the shape and wrong about
 * the product. You talk to the assistant, and the place you talk to it is the
 * place it answers.
 *
 * What is *not* here is the document's record of what changed. That is the
 * revision block, above the title block on the sheet -- see
 * `canvas/revision-block`. The split is real rather than cosmetic: this pane is
 * a dialogue, and it is gone when you reload; that one describes the artifact.
 *
 * One thing this is not: a chat log with two colours. The user's message is an
 * *instruction* and the assistant's reply is an *entry*, and they are not
 * peers -- the person holding the caret outranks the assistant, and the layout
 * says so.
 */

import { useEffect, useRef, useState } from 'react';

import { Markdown } from '@/chat/markdown';
import { ModelPicker } from '@/chat/model-picker';
import { TargetJob } from '@/chat/target-job';
import { stripNodeIds } from '@/chat/prose';
import { readTarget, textOf } from '@/doc/read';
import { Caution, Check, Cross, Query, Running, Undo } from '@/ui/marks';
import { useChat, type ChatMessage, type PendingConfirm, type ToolActivity } from '@/store/chat';
import { useCanvas } from '@/store/canvas';
import { useStudio } from '@/store/studio';

const SUGGESTIONS = [
  'Tighten every bullet in my most recent job',
  'Make my summary punchier',
  'Add Rust to my skills',
];

/**
 * One tool call, as a row with a state mark.
 *
 * The row says what the assistant did and carries the number drawn on the
 * document beside the region it touched. It does not carry the two readings:
 * those belong to the revision block, which is the document's record rather
 * than the conversation's, and duplicating them here would put the same fact
 * in two places that can disagree.
 */
function Item({ item }: { item: ToolActivity }) {

  const touched = item.touched ?? [];
  const nid = touched[0];

  const Mark =
    item.status === 'applied'
      ? Check
      : item.status === 'rejected'
        ? Cross
        : item.status === 'confirm'
          ? Query
          : item.status === 'note'
            ? Caution
            : item.status === 'done'
              ? Check
              : Running;

  const body = (
    <>
      <span className="item__mark">
        <Mark size={13} />
      </span>
      <span className="item__body">
        <span className="item__label">{item.label || item.name.replace(/_/g, ' ')}</span>
        {item.detail && <span className="item__detail">{item.detail}</span>}
      </span>
    </>
  );

  // A plain row. It used to be a button that lit the region it named on the
  // document -- which needed the revision layer to do the lighting, and that
  // is gone. A control offering to show you something and then showing you
  // nothing is worse than no control, which is what its own rule said.
  return <div className={`item item--${item.status}`}>{body}</div>;
}

function Instruction({ message }: { message: ChatMessage }) {
  // Right-aligned, in its own bubble. The "Instruction" legend that used to sit
  // above it is gone with the alignment: which side a message sits on already
  // says who wrote it, and a label repeating that is one more thing to read.
  return (
    <div className="instruction">
      <p className="instruction__text">{message.text}</p>
    </div>
  );
}

/**
 * One assistant turn. Exported for tests: which steps survive a finished turn
 * is a judgement about what the sidebar is for, and worth pinning.
 */
export function Revision({ message }: { message: ChatMessage }) {
  const undoTurn = useChat((state) => state.undoTurn);
  // Not while the document is mid-write: a revert lands as a whole new
  // version, and racing it against a save in flight is how two clients end up
  // disagreeing about which one won.
  const busy = useStudio((state) => state.saving);
  const versions = useCanvas((state) => state.boards.length);

  const idle = message.status === 'streaming' && !message.text && !message.activity.length;
  /**
   * The turn finished and brought back nothing at all.
   *
   * Not a hypothetical: a reasoning model can spend its whole generation budget
   * inside its thinking block and stop before writing either a reply or a tool
   * call. The entry then rendered completely empty -- blank space where a reply
   * should be -- and "it did nothing" is what a person concludes.
   *
   * The server now says which of the two happened, so this only has to cover
   * the case where it did not: the model finished normally and simply produced
   * nothing. A truncated turn arrives as its own `truncated` warning and reads
   * as a row like any other note.
   */
  const finished = message.status && message.status !== 'streaming';
  const silent =
    Boolean(finished) && !message.text && message.activity.length === 0;

  // Node ids are for the model, never for the reader. Stripped here rather than
  // only asked for in the prompt, because a prompt is advisory and this one is
  // routinely ignored after the model has just read forty lines of
  // id-annotated outline.
  const prose = stripNodeIds(message.text, {
    streaming: message.status === 'streaming',
  });

  /**
   * Which steps stay on screen once the turn is over.
   *
   * While it runs, all of them: watching the work happen is most of what makes
   * an agent legible, and a bubble that sat blank for thirty seconds would read
   * as a hang.
   *
   * Afterwards, only the ones that still say something. A successful edit is
   * already recorded in the revision block -- same number as the mark on the
   * sheet, with the outgoing and incoming text in full -- so repeating it here
   * as "rewrite text" is a worse copy of a better record, and it buries the
   * rows that have no other home: an edit that was refused, and an advisory
   * note about what the turn did.
   */
  const steps = finished
    ? message.activity.filter(
        (item) =>
          item.status === 'rejected' ||
          item.status === 'confirm' ||
          item.status === 'note'
      )
    : message.activity;

  return (
    <div className="revision">
      <div className="revision__body">
        {/* Always present when there is reasoning to show, and closed until
            asked for. A header toggle made it a setting to find and remember;
            a closed disclosure says it is there and costs one line. */}
        {message.thinking && (
          <details className="fold fold--reasoning">
            <summary>Reasoning</summary>
            <pre>{message.thinking}</pre>
          </details>
        )}

        {/* The steps, once the turn is over: the complete record, closed.
            Directly under Reasoning and built the same way, because they answer
            the same kind of question -- how the answer was arrived at -- and a
            reader who wants one usually wants the other.

            Only after the turn. While it runs the live list below is open and
            unfolding, which is most of what makes an agent legible; folding the
            work away as it happens would leave a bubble that sits blank for
            thirty seconds and reads as a hang.

            Everything is in here, successful edits included. The few rows that
            still appear below are the ones that need an answer -- a refusal, a
            confirmation, an advisory note -- and burying those behind a
            disclosure is how a turn ends up looking like it worked when it did
            not. This is the log; those are the alerts. */}
        {finished && message.activity.length > 0 && (
          <details className="fold fold--steps">
            <summary>Tool calls</summary>
            <div className="items items--log">
              {message.activity.map((item) => (
                <Item key={item.callId} item={item} />
              ))}
            </div>
          </details>
        )}

        {prose && (
          <div className="revision__prose">
            <Markdown text={prose} />
          </div>
        )}
        {idle && <p className="revision__status">Working…</p>}

        {silent && message.status !== 'cancelled' && (
          <p className="revision__status revision__status--silent">
            The model returned nothing — no reply and no changes.
          </p>
        )}

        {steps.length > 0 && (
          <div className="items">
            {steps.map((item) => (
              <Item key={item.callId} item={item} />
            ))}
          </div>
        )}

        {message.status === 'cancelled' && <p className="revision__status">Stopped.</p>}
        {message.status === 'partial' && (
          <p className="revision__status">Some changes could not be applied.</p>
        )}

        {/* The whole turn back, in one press.
            Ctrl+Z reverses one committed batch and the agent commits one per
            tool call, so undoing a fourteen-edit turn by hand is fourteen
            presses -- long enough that people stop halfway and are left with a
            document nobody asked for. This is offered only on a turn that
            actually moved the sheet; see `ChatMessage.checkpoint`. */}
        {message.checkpoints?.length && !message.reverted ? (
          <button
            type="button"
            className="revision__undo"
            onClick={() => void undoTurn(message.id)}
            disabled={busy}
            title="Put the résumé back to how it was before this turn"
          >
            <Undo size={12} />
            {message.checkpoints.length > 1 ? 'Undo this turn everywhere' : 'Undo this turn'}
          </button>
        ) : null}
        {message.reverted && (
          <p className="revision__status">Reverted. The sheet is as it was before this.</p>
        )}

        {/* Which model answered. A caption rather than a row in the activity
            list: it says nothing about the resume, and listed there it wore the
            same cross as a rejected edit. Worth showing at all because the plan
            default is not fixed -- the same document answered on Opus one turn
            and Sonnet the next. */}
        {/* Which version this turn landed on. Only where there is more than
            one: the conversation is about the résumé, and naming the version
            on a résumé that has exactly one would be answering a question
            nobody could be asking. */}
        {versions > 1 && message.board && (
          <p className="revision__model">on {message.board}</p>
        )}

        {message.model && <p className="revision__model">{message.model}</p>}
      </div>
    </div>
  );
}

/**
 * What a proposed change actually does, in the document's own words.
 *
 * The arguments are addressed either at a node (`set_text` against a nid) or
 * at one of its attributes (`set_field` against `nid.title`), and the document
 * still holds the outgoing value, so both sides of the change are readable
 * without asking the server for anything.
 */
function describe(
  args: Record<string, unknown>
): { where: string; was: string; now: string } | null {
  const doc = useStudio.getState().doc;
  const target = typeof args.target === 'string' ? args.target : null;
  const nid = typeof args.nid === 'string' ? args.nid : null;

  const next = [args.value, args.text].find((value) => typeof value === 'string') as
    | string
    | undefined;
  if (next === undefined) return null;

  if (target) return { where: target, was: readTarget(doc, target), now: next };
  if (nid) return { where: nid, was: textOf(doc, nid), now: next };
  return null;
}

/**
 * The stamp block.
 *
 * A change the engine will not apply without authority. This used to print
 * `JSON.stringify(args)` into a `<pre>`, which asked a job seeker under time
 * pressure to audit a data structure in order to decide whether to trust it --
 * and the raw arguments do not even say what the change replaces, only what it
 * would become.
 *
 * A drawing office solved this with a block that states the alteration in
 * words, shows the reading it supersedes, and does not take effect until it is
 * signed. Refusing is given equal weight, because the safe choice must never
 * be the harder one to find.
 */
function StampBlock({
  confirm,
  documentId,
  onSign,
  onRefuse,
}: {
  confirm: PendingConfirm;
  documentId: string;
  onSign: (documentId: string) => void;
  onRefuse: () => void;
}) {
  const change = describe(confirm.args);

  return (
    <section className="stamp" aria-label="A change awaiting your signature">
      <div className="stamp__head">
        <Query size={14} />
        <span className="stamp__title">Needs your signature</span>
      </div>

      <div className="stamp__body">
        <p className="stamp__risk">{confirm.risk}</p>

        {change && (
          <div className="stamp__diff">
            {change.was && (
              <div className="stamp__line">
                <span className="stamp__side">Now</span>
                <span className="stamp__was">{change.was}</span>
              </div>
            )}
            <div className="stamp__line">
              <span className="stamp__side">Would be</span>
              <span className="stamp__now">{change.now}</span>
            </div>
          </div>
        )}

        <p className="stamp__where">
          {confirm.tool.replace(/_/g, ' ')}
          {change ? ` · ${change.where}` : ''}
        </p>
      </div>

      <div className="stamp__actions">
        <button type="button" className="ctl ctl--sign" onClick={() => onSign(documentId)}>
          Sign and apply
        </button>
        <button type="button" className="ctl" onClick={onRefuse}>
          Refuse
        </button>
      </div>
    </section>
  );
}

export function ChatPanel({ documentId }: { documentId: string }) {
  const messages = useChat((state) => state.messages);
  const streaming = useChat((state) => state.streaming);
  const error = useChat((state) => state.error);
  const confirm = useChat((state) => state.confirm);
  const send = useChat((state) => state.send);
  const cancel = useChat((state) => state.cancel);
  const approveConfirm = useChat((state) => state.approveConfirm);
  const dismissConfirm = useChat((state) => state.dismissConfirm);

  const [draft, setDraft] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  const submit = () => {
    if (!draft.trim() || streaming) return;
    send(documentId, draft.trim());
    setDraft('');
  };

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  return (
    <div className="schedule">
      <div className="schedule__rows">
        {messages.length === 0 && (
          <div className="schedule__empty">
            <p>
              Ask for a change and watch it land. Every edit is marked on the
              document, and every one of them is reversible.
            </p>
            <ul className="schedule__suggestions">
              {SUGGESTIONS.map((suggestion) => (
                <li key={suggestion}>
                  <button
                    className="schedule__suggestion"
                    type="button"
                    onClick={() => send(documentId, suggestion)}
                  >
                    {suggestion}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {messages.map((message) => {
          if (message.role === 'user') {
            return <Instruction key={message.id} message={message} />;
          }
          return (
            <Revision key={message.id} message={message} />
          );
        })}

        {error && <div className="notice notice--error">{error}</div>}

        {confirm && (
          <StampBlock
            confirm={confirm}
            documentId={documentId}
            onSign={approveConfirm}
            onRefuse={dismissConfirm}
          />
        )}

        <div ref={endRef} />
      </div>

      {/* A bordered box, not a ruled line. The field is where everything in
          this column starts, and an underline alone left it ambiguous how far
          it went and where to click. */}
      <div className="composer">
        {/* Which job this résumé is aimed at. Above the field rather than
            beside Send, because it is a standing fact about the document and
            not a thing you set per message. */}
        <TargetJob />

        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line. A resume instruction is
            // almost always one line, so this is the right default.
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="Tell the assistant what to change…"
          rows={2}
          disabled={streaming}
        />
        {/* Under the field and pushed right, the model beside the button it
            governs. It used to sit in a header strip of its own above the
            transcript -- a bar carrying one control, and the one control that
            only matters at the moment you send. */}
        <div className="composer__row">
          <ModelPicker />
          {streaming ? (
            <button className="ctl" type="button" onClick={cancel}>
              Stop
            </button>
          ) : (
            <button
              className="ctl ctl--primary"
              type="button"
              onClick={submit}
              disabled={!draft.trim()}
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default ChatPanel;
