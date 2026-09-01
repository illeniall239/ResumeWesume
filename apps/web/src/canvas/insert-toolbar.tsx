/**
 * Adding things to the page.
 *
 * Deliberately not a floating palette or a drag-from-tray: a click places the
 * element near the top of the current page and selects it, so the very next
 * gesture is the drag that puts it where the user actually wants it. That is
 * one interaction instead of two, and it reuses the drag they already know.
 */

'use client';

import { useRef, useState } from 'react';

import type { DocOp, StudioDoc } from '@/contracts/doc';
import { uploadAsset } from '@/lib/api';

import { insertImage, insertPage, insertShape, insertTextBlock } from './insert';
import { useSelection } from './selection';

/** Where the first new element lands. */
const DROP = { x: 60, y: 60 };
/** Each subsequent one steps down-right so they do not stack invisibly. */
const CASCADE = 14;
const CASCADE_WRAP = 8;

export function InsertToolbar({
  doc,
  documentId,
  commit,
}: {
  doc: StudioDoc;
  documentId: string;
  commit: (ops: DocOp[]) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** The page holding the selection, else the first. */
  function activePage(): string | null {
    const selected = useSelection.getState().selected;
    const holding = doc.pages.find((page) =>
      page.elements.some((element) => selected.includes(element.nid))
    );
    return (holding ?? doc.pages[0])?.nid ?? null;
  }

  function place(build: (placement: { pageNid: string; at: typeof DROP }) => {
    ops: DocOp[];
    nid: string;
  }) {
    const pageNid = activePage();
    if (!pageNid) return;

    // Step each new element down and right. Dropping everything on one spot
    // means the third thing you add is invisible under the first two, and the
    // user has to drag blind to find out what they made. Wraps so a long
    // session does not walk off the page.
    const page = doc.pages.find((candidate) => candidate.nid === pageNid);
    const step = (page?.elements.length ?? 0) % CASCADE_WRAP;
    const at = { x: DROP.x + step * CASCADE, y: DROP.y + step * CASCADE };

    const { ops, nid } = build({ pageNid, at });
    commit(ops);
    // Select it so the drag that follows needs no extra click.
    useSelection.getState().selectMany([nid]);
  }

  async function onFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // Cleared immediately so choosing the same file twice still fires.
    event.target.value = '';
    if (!file) return;

    setBusy(true);
    setError(null);
    try {
      const asset = await uploadAsset(file, documentId);
      place((placement) =>
        insertImage(placement, asset, file.name.replace(/\.[^.]+$/, ''))
      );
    } catch (caught) {
      // The server's message names the actual problem -- too large, wrong
      // format -- so it is shown rather than replaced with something generic.
      setError((caught as Error).message.replace(/^\d+:\s*/, ''));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="insert-bar">
      <span className="insert-bar__label">Add</span>

      <button type="button" onClick={() => place((p) => insertTextBlock(p))}>
        Text
      </button>
      <button type="button" onClick={() => fileInput.current?.click()} disabled={busy}>
        {busy ? 'Uploading…' : 'Image'}
      </button>
      <button type="button" onClick={() => place((p) => insertShape(p, 'rect'))}>
        Box
      </button>
      <button type="button" onClick={() => place((p) => insertShape(p, 'ellipse'))}>
        Ellipse
      </button>
      <button type="button" onClick={() => place((p) => insertShape(p, 'line'))}>
        Line
      </button>

      <span className="insert-bar__divider" />

      <button type="button" onClick={() => commit(insertPage().ops)}>
        Page
      </button>

      <input
        ref={fileInput}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        onChange={onFile}
        hidden
      />

      {error && (
        <span className="insert-bar__error" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
