/**
 * One résumé in the register, with the way to be rid of it.
 *
 * The delete endpoint existed from the beginning and nothing in the interface
 * reached it, so a document could be created and never removed.
 *
 * It asks first, in place, rather than through a modal or the browser's own
 * dialog. There is no undo for this one: undo reverses a batch *within* a
 * document, and a deleted document has no op log left to reverse -- so the
 * second press is the only thing between a stray click and losing somebody's
 * résumé.
 */

'use client';

import { useState } from 'react';

import type { DocumentResponse } from '@/contracts/doc';
import { deleteDocument } from '@/lib/api';
import { timeAgo } from '@/lib/when';
import { Cross } from '@/ui/marks';

export function RecentSheet({
  document: sheet,
  onDeleted,
  onError,
}: {
  document: DocumentResponse;
  onDeleted: (id: string) => void;
  onError: (message: string) => void;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  async function remove() {
    setBusy(true);
    try {
      await deleteDocument(sheet.id);
      onDeleted(sheet.id);
    } catch (cause) {
      onError((cause as Error).message);
      // Back to resting, so a failure that was the network's fault can simply
      // be tried again rather than leaving the card stuck mid-question.
      setBusy(false);
      setAsking(false);
    }
  }

  if (asking) {
    return (
      <div className="recent recent--asking">
        <span className="recent__title">Delete this résumé?</span>
        <span className="recent__confirm">
          <button
            className="link link--rev"
            type="button"
            onClick={remove}
            disabled={busy}
          >
            {busy ? 'Deleting…' : 'Delete'}
          </button>
          <button className="link" type="button" onClick={() => setAsking(false)}>
            Keep
          </button>
        </span>
      </div>
    );
  }

  return (
    <div className="recent">
      {/* The link and the button are siblings, never nested: a button inside
          an anchor is invalid, and the press would navigate before it deleted
          anything. */}
      <a className="recent__open" href={`/studio/${sheet.id}`}>
        <span className="recent__title">{sheet.title}</span>
        {/* When it last changed, not how many writes it has taken. A write
            count is a fact about the engine; what tells you which résumé this
            is, is when you last had it open. */}
        <span className="recent__rev">{timeAgo(sheet.updated_at)}</span>
      </a>
      <button
        className="recent__discard"
        type="button"
        onClick={() => setAsking(true)}
        aria-label={`Delete ${sheet.title}`}
        title="Delete"
      >
        <Cross size={12} />
      </button>
    </div>
  );
}

export default RecentSheet;
