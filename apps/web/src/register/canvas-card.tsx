/**
 * One canvas in the register: its boards, its name, and the way to be rid of it.
 *
 * A canvas is a résumé and the versions of it aimed at particular jobs, so the
 * card shows the versions rather than a count of them — you recognise your own
 * work by looking at it, and "4 boards" is a fact about the database.
 *
 * The previews are real renders, the same `DocumentFlow` the studio and the PDF
 * use, on the documents this card opens. The list endpoint returns every board
 * in full, so there is nothing to fetch and nothing that can go stale against
 * what it names.
 *
 * Deleting asks first, in place. There is no undo for it: undo reverses a batch
 * *within* a document, and a deleted canvas has no op log left to reverse — so
 * the second press is the only thing between a stray click and losing a whole
 * job hunt.
 *
 * A bin, on the name row, on hover. It was a cross down among the metadata,
 * which was wrong twice over: a cross is the mark this app already uses for a
 * rejected edit and for closing a dialog, neither of which destroys anything;
 * and beside a timestamp it read as furniture belonging to the timestamp
 * rather than to the résumé. It acts on the thing the name names, so that is
 * the line it belongs on.
 */

'use client';

import { useState } from 'react';

import type { CanvasResponse } from '@/contracts/doc';
import { deleteCanvas } from '@/lib/api';
import { timeAgo } from '@/lib/when';
import DocumentFlow from '@/render/document-flow';
import { Bin } from '@/ui/marks';

/**
 * How many boards a card draws.
 *
 * Three, then a count. A row of eleven thumbnails at this size is a texture
 * rather than a set of documents, and the fourth onwards are all the same
 * shape anyway — what the card has to answer is "which résumé is this and how
 * many ways am I sending it".
 */
const SHOWN = 3;

export function CanvasCard({
  canvas,
  onDeleted,
  onError,
}: {
  canvas: CanvasResponse;
  onDeleted: (id: string) => void;
  onError: (message: string) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  const boards = canvas.boards ?? [];
  const shown = boards.slice(0, SHOWN);
  const hidden = boards.length - shown.length;

  /**
   * Where the card goes: the canvas, which is what this card is.
   *
   * Only when it has something on it. An empty canvas opens on a sentence
   * saying so, and a link to that is a link to a dead end.
   */
  const opens = boards.length ? canvas.id : null;

  async function remove() {
    setBusy(true);
    try {
      await deleteCanvas(canvas.id);
      onDeleted(canvas.id);
    } catch (cause) {
      onError((cause as Error).message);
      // Back to resting, so a failure that was the network's fault can be
      // tried again rather than leaving the card stuck mid-question.
      setBusy(false);
      setAsking(false);
    }
  }

  return (
    <div className="reg-doc">
      {/* The sheets open the canvas; the delete is their sibling, never nested
          inside. A button inside an anchor is invalid markup, and the press
          would navigate before it deleted anything. */}
      {opens ? (
        <a
          className="reg-doc__stack"
          href={`/studio/${opens}`}
          aria-label={`Open ${canvas.title || 'Untitled'}`}
        >
          <span className="reg-doc__boards" aria-hidden="true">
            {shown.map((board) => (
              <span className="reg-doc__paper" key={board.id}>
                <span className="reg-doc__sheet">
                  <DocumentFlow doc={board.doc} editable={false} />
                </span>
              </span>
            ))}
            {hidden > 0 && <span className="reg-doc__more">+{hidden}</span>}
          </span>
        </a>
      ) : (
        // A canvas with nothing on it yet. Real, not broken: one is made
        // before its first résumé arrives from an import or a template.
        <div className="reg-doc__stack reg-doc__stack--bare">
          <span className="reg-doc__empty">Nothing on this canvas yet</span>
        </div>
      )}

      {/* The name and the way to be rid of it, on one line: the bin acts on
          the thing the name names. Drawn on hover -- and on keyboard focus,
          which is the same affordance for anyone not using a mouse -- because
          a delete on every card at rest turns a list of your own work into a
          list of things to be careful of. */}
      <span className="reg-doc__head">
        <span className="reg-doc__name">{canvas.title || 'Untitled'}</span>
        <button
          type="button"
          className="reg-doc__drop"
          onClick={() => setAsking(true)}
          aria-label={`Delete ${canvas.title || 'Untitled'}`}
          title="Delete"
        >
          <Bin size={14} />
        </button>
      </span>

      <span className="reg-doc__sub">
        {asking ? (
          <>
            {/* Specific about what goes: "delete this" understates it for a
                canvas holding four versions aimed at four different jobs. */}
            <span className="reg-doc__ask">
              {boards.length > 1
                ? `Delete this and all ${boards.length} versions?`
                : 'Delete this?'}
            </span>
            <button
              type="button"
              className="reg-doc__answer reg-doc__answer--go"
              onClick={remove}
              disabled={busy}
            >
              {busy ? 'Deleting…' : 'Delete'}
            </button>
            <button
              type="button"
              className="reg-doc__answer"
              onClick={() => setAsking(false)}
            >
              Keep
            </button>
          </>
        ) : (
          <>
            {/* When it last changed, not how many writes it has taken. A write
                count is a fact about the engine; what tells you which résumé
                this is, is when you last had it open. */}
            <span>{timeAgo(canvas.updated_at)}</span>
            {boards.length > 1 && (
              <span className="reg-doc__count">{boards.length} versions</span>
            )}
          </>
        )}
      </span>
    </div>
  );
}

export default CanvasCard;
