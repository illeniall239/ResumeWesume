'use client';

import { use, useEffect, useRef } from 'react';

import type { DocOp } from '@/contracts/doc';
import ChatPanel from '@/chat/chat-panel';
import DocumentFlow from '@/render/document-flow';
import { InsertToolbar } from '@/canvas/insert-toolbar';
import { PageCanvas } from '@/canvas/page-canvas';
import { mintLike } from '@/canvas/ids';
import { removeElements } from '@/canvas/pages';
import { useSelection } from '@/canvas/selection';
import { canZoom, useView } from '@/canvas/view';
import { useReflow } from '@/canvas/use-reflow';
import { pdfUrl } from '@/lib/api';
import { useChat } from '@/store/chat';
import { useStudio } from '@/store/studio';

/**
 * The studio: assistant on the left, live document on the right.
 *
 * Both panes read from stores rather than from each other. A token of assistant
 * text must not re-render the resume, and a patch landing must not re-render the
 * transcript, so the two are kept in separate stores with per-field selectors.
 */
export default function StudioPage({ params }: { params: Promise<{ docId: string }> }) {
  const { docId } = use(params);

  const doc = useStudio((state) => state.doc);
  const title = useStudio((state) => state.title);
  const loading = useStudio((state) => state.loading);
  const saving = useStudio((state) => state.saving);
  const error = useStudio((state) => state.error);
  const changed = useStudio((state) => state.changed);
  const locked = useStudio((state) => state.locked);
  const load = useStudio((state) => state.load);
  const edit = useStudio((state) => state.edit);
  const setFocus = useStudio((state) => state.setFocus);
  const history = useStudio((state) => state.history);
  const stage = useStudio((state) => state.stage);
  const flush = useStudio((state) => state.flush);
  const nudgeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const zoom = useView((state) => state.zoom);
  const zoomIn = useView((state) => state.zoomIn);
  const zoomOut = useView((state) => state.zoomOut);
  const resetZoom = useView((state) => state.reset);

  const streaming = useChat((state) => state.streaming);
  const resetChat = useChat((state) => state.reset);

  useEffect(() => {
    void load(docId);
    return () => resetChat();
  }, [docId, load, resetChat]);

  // Keyboard, on the window rather than a focused node: a selected box is not
  // a focusable element, so there is nothing else to hang these on. Every
  // branch defers to contentEditable, where the browser's own handling is what
  // the user means.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target?.isContentEditable || target instanceof HTMLTextAreaElement;

      // Escape is handled before the deference to contentEditable, because
      // leaving the text is exactly what it means there -- and the handler
      // reads it in two steps: stop editing, then deselect. Everything below
      // stays hands-off while the caret is live.
      if (event.key === 'Escape') {
        if (useSelection.getState().editing) {
          useSelection.getState().stopEditing();
          target?.blur();
        } else {
          useSelection.getState().clear();
        }
        return;
      }
      if (typing) return;

      const selected = useSelection.getState().selected;

      if ((event.key === 'Delete' || event.key === 'Backspace') && selected.length) {
        event.preventDefault();
        // Not a bare remove per nid: a hand-placed text box has to take its
        // words with it, or the coverage gate refuses the batch and the box
        // will not delete at all.
        const doc = useStudio.getState().doc;
        void edit(
          doc
            ? removeElements(doc, selected)
            : selected.map((nid) => ({ op: 'remove_node', nid }) as DocOp)
        );
        useSelection.getState().clear();
        return;
      }
      const current = useStudio.getState().doc;

      // Select everything on the page the selection is already on, or the
      // first page when nothing is selected.
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
        const page =
          current?.pages.find((candidate) =>
            candidate.elements.some((element) => selected.includes(element.nid))
          ) ?? current?.pages[0];
        if (page) {
          event.preventDefault();
          useSelection.getState().selectMany(page.elements.map((e) => e.nid));
        }
        return;
      }

      // Duplicate: offset a little so the copy is visibly its own object
      // rather than hiding exactly beneath the original.
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'd' && selected.length) {
        event.preventDefault();
        const ops: DocOp[] = [];
        const fresh: string[] = [];
        for (const page of current?.pages ?? []) {
          for (const element of page.elements) {
            if (!selected.includes(element.nid)) continue;
            // Only decoration is duplicated. Copying a frame would produce two
            // boxes rendering the same content, which the coverage rule treats
            // as one node claimed twice -- and which is not what anyone means
            // by "duplicate this".
            if ('ref' in element) continue;
            const nid = mintLike(element.nid);
            fresh.push(nid);
            ops.push({
              op: 'insert_node',
              parent: page.nid,
              index: -1,
              node: {
                ...element,
                nid,
                rect: { ...element.rect, x: element.rect.x + 8, y: element.rect.y + 8 },
              },
            } as DocOp);
          }
        }
        if (ops.length) {
          void edit(ops);
          useSelection.getState().selectMany(fresh);
        }
        return;
      }

      // Z-order. List order is paint order, so this is an ordinary reorder.
      if ((event.key === '[' || event.key === ']') && selected.length) {
        event.preventDefault();
        const ops: DocOp[] = [];
        for (const page of current?.pages ?? []) {
          const ids = page.elements.map((element) => element.nid);
          if (!ids.some((nid) => selected.includes(nid))) continue;

          const moving = ids.filter((nid) => selected.includes(nid));
          const rest = ids.filter((nid) => !selected.includes(nid));
          // Forward means later in the list, which paints on top.
          const order = event.key === ']' ? [...rest, ...moving] : [...moving, ...rest];
          ops.push({ op: 'reorder', parent: page.nid, order } as DocOp);
        }
        if (ops.length) void edit(ops);
        return;
      }

      if (event.key.startsWith('Arrow') && selected.length) {
        event.preventDefault();
        const step = event.shiftKey ? 10 : 1;
        const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
        const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
        const ops: DocOp[] = [];
        for (const page of current?.pages ?? []) {
          for (const element of page.elements) {
            if (!selected.includes(element.nid)) continue;
            ops.push({
              op: 'set_geometry',
              nid: element.nid,
              x: element.rect.x + dx,
              y: element.rect.y + dy,
            } as DocOp);
          }
        }
        // Staged rather than sent: a burst of arrow presses is one gesture and
        // should be one undo step, so it goes out when the keys stop.
        if (ops.length) {
          stage(ops);
          if (nudgeTimer.current) clearTimeout(nudgeTimer.current);
          nudgeTimer.current = setTimeout(() => void flush(), 200);
        }
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [edit, stage, flush]);

  // Ctrl/Cmd+Z anywhere except inside text being edited, where the browser's
  // own undo is the one the user means.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'z') return;
      const target = event.target as HTMLElement | null;
      if (target?.isContentEditable || target instanceof HTMLTextAreaElement) return;
      event.preventDefault();
      void history(event.shiftKey ? 'redo' : 'undo');
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [history]);

  // The server places frames without being able to measure text, so a freshly
  // migrated document opens with its sections overlapping until this corrects
  // them from what the browser actually rendered.
  const canvasRef = useRef<HTMLDivElement>(null);
  useReflow(canvasRef, doc, edit);

  return (
    <main className="studio">
      <aside className="pane">
        <ChatPanel documentId={docId} />
      </aside>

      <section className="pane pane--doc">
        <div className="toolbar toolbar--doc">
          {/* No version number here on purpose. The document's `version` is an
              ETag half and the key an undo reverses -- it counts accepted
              writes, so a drag, the reflow pass on open and an undo each add
              one, and an undo counts *up* while taking you back. Shown raw it
              reads as a revision number and is not one. It is still sent on
              every write; it is just not something to show a person. */}
          <strong>{title || 'Resume'}</strong>
          {saving && <span className="badge">saving…</span>}
          <button
            className="button button--quiet"
            onClick={() => void history('undo')}
            disabled={saving}
            title="Undo (Ctrl+Z)"
          >
            Undo
          </button>
          <button
            className="button button--quiet"
            onClick={() => void history('redo')}
            disabled={saving}
            title="Redo (Ctrl+Shift+Z)"
          >
            Redo
          </button>
          {streaming && <span className="badge badge--live">assistant editing</span>}
          <span className="toolbar__spacer" />
          <a className="button" href={pdfUrl(docId)} target="_blank" rel="noreferrer">
            Export PDF
          </a>
        </div>

        {doc && <InsertToolbar doc={doc} documentId={docId} commit={(ops) => void edit(ops)} />}

        {error && <div className="notice notice--error">{error}</div>}
        {loading && <div className="notice">Loading…</div>}

        {/* `zoom` rather than a transform: it scales the layout, so the pane
            still scrolls to the bottom of a magnified document, and a measured
            rect comes back in the same space the pointer reports. A transform
            would leave the scroll height at 100%. */}
        {doc && (
          <div ref={canvasRef} style={{ zoom }}>
          <PageCanvas
            doc={doc}
            changed={changed}
            locked={locked}
            editable
            onFocusNode={setFocus}
            onEditText={(nid, value) => {
              void edit([{ op: 'set_text', nid, value }]);
            }}
            onEditField={(target, value) => {
              void edit([{ op: 'set_field', target, value }]);
            }}
            interactive
            commit={(ops) => void edit(ops)}
          />
          </div>
        )}

        {/* On the canvas rather than in the toolbar, and deliberately outside
            the scaled wrapper: inside it the buttons would zoom along with the
            document and read 200% while being twice their own size. Sticky, so
            they stay to hand at the bottom-right of a document taller than the
            pane. Zoom is a property of this viewer, never of the resume -- it
            reaches no op, so it cannot be undone and cannot bump a version. */}
        {doc && (
          <div className="zoom">
            <button
              type="button"
              className="zoom__step"
              title="Zoom out"
              disabled={!canZoom(zoom, -1)}
              onClick={zoomOut}
            >
              −
            </button>
            <button
              type="button"
              className="zoom__level"
              title="Reset to 100%"
              onClick={resetZoom}
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              type="button"
              className="zoom__step"
              title="Zoom in"
              disabled={!canZoom(zoom, 1)}
              onClick={zoomIn}
            >
              +
            </button>
          </div>
        )}
      </section>
    </main>
  );
}
