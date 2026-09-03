'use client';

import { use, useEffect, useRef, useState } from 'react';

import type { DocOp } from '@/contracts/doc';
import ChatPanel from '@/chat/chat-panel';
import IssueStation from '@/export/issue-station';
import { InsertToolbar } from '@/canvas/insert-toolbar';
import { PageCanvas } from '@/canvas/page-canvas';
import { RevisionLayer } from '@/canvas/revision-layer';
import { RevisionBlock } from '@/canvas/revision-block';
import { mintLike } from '@/canvas/ids';
import { removeElements } from '@/canvas/pages';
import { useSelection } from '@/canvas/selection';
import { canZoom, fitZoom, useView } from '@/canvas/view';
import { useReflow } from '@/canvas/use-reflow';
import { Minus, Plus, Redo, Sheet, Undo } from '@/ui/marks';
import { useChat } from '@/store/chat';
import { useStudio } from '@/store/studio';

/**
 * The board: the revision schedule at the left, the print at the right.
 *
 * Both panes read from stores rather than from each other. A token of assistant
 * text must not re-render the resume, and a patch landing must not re-render the
 * transcript, so the two are kept in separate stores with per-field selectors.
 *
 * The rails are fixed and only the sheet scrolls. That is what keeps the title
 * block on screen: it is where the document says what it is and what state it
 * is in, and a person scrolling to the foot of page three should not have to
 * scroll back to find out whether their work is saved.
 */
export default function StudioPage({ params }: { params: Promise<{ docId: string }> }) {
  const { docId } = use(params);

  const doc = useStudio((state) => state.doc);
  const title = useStudio((state) => state.title);
  const loading = useStudio((state) => state.loading);
  const saving = useStudio((state) => state.saving);
  const error = useStudio((state) => state.error);
  const changed = useStudio((state) => state.changed);
  const version = useStudio((state) => state.version);
  const unverified = useStudio((state) => state.unverified);
  // Text a tool call is writing right now, shown in place while it arrives.
  const drafts = useStudio((state) => state.drafts);
  const locked = useStudio((state) => state.locked);
  const load = useStudio((state) => state.load);
  const edit = useStudio((state) => state.edit);
  const setFocus = useStudio((state) => state.setFocus);
  const history = useStudio((state) => state.history);
  const stage = useStudio((state) => state.stage);
  const flush = useStudio((state) => state.flush);
  const revisions = useStudio((state) => state.revisions);
  const confirmInvented = useStudio((state) => state.confirmInvented);
  const nudgeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [issuing, setIssuing] = useState(false);

  const zoom = useView((state) => state.zoom);
  const setZoom = useView((state) => state.setZoom);
  const zoomIn = useView((state) => state.zoomIn);
  const zoomOut = useView((state) => state.zoomOut);
  const resetZoom = useView((state) => state.reset);

  const streaming = useChat((state) => state.streaming);
  const resetChat = useChat((state) => state.reset);
  const loadChat = useChat((state) => state.load);

  useEffect(() => {
    void load(docId);
    // The conversation comes back with the document. Without this a reload
    // emptied the sidebar *and* the history handed to the model, so the
    // assistant would ask again for facts it had already been given.
    void loadChat(docId);
    return () => resetChat();
  }, [docId, load, loadChat, resetChat]);

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
  // Version is part of the key: without it the pass runs once and never
  // again, so text an agent turn made longer overflows its frame and is
  // drawn over whatever sits below.
  useReflow(canvasRef, doc, edit, version);

  /**
   * Open at a magnification the sheet actually fits.
   *
   * On a phone an A4 page is roughly twice the pane, so opening at 100% showed
   * less than half the résumé -- and the first thing a document has to say is
   * what it is. Runs once per document, and only when the sheet does not fit:
   * a desktop pane is wider than a page, so this never touches it, and it must
   * never override a magnification the person chose themselves.
   */
  const scrollRef = useRef<HTMLDivElement>(null);
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    const pane = scrollRef.current;
    if (!doc || !pane || fitted.current === docId) return;
    const sheet = pane.querySelector('.canvas-page');
    if (!sheet) return;

    fitted.current = docId;
    // The rendered width already carries the current zoom, so it is divided
    // back out to get the sheet's own width before choosing a step.
    const sheetWidth = sheet.getBoundingClientRect().width / zoom;
    const room = pane.clientWidth - 32;
    if (sheetWidth > room) setZoom(fitZoom(room, sheetWidth));
    // `zoom` is read, not depended on: reacting to it would refit the moment
    // the user zoomed in, which is the opposite of what they asked for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, docId, setZoom]);

  return (
    <main className="deck">
      <aside>
        <ChatPanel documentId={docId} />
      </aside>

      <section className="deck__board">
        <div className="rail rail--top">
          {doc && (
            <InsertToolbar doc={doc} documentId={docId} commit={(ops) => void edit(ops)} />
          )}
          <span className="rail__spacer" />
          <button
            type="button"
            className="ctl ctl--small"
            onClick={() => void history('undo')}
            disabled={saving}
            title="Undo (Ctrl+Z)"
          >
            <Undo size={13} />
            Undo
          </button>
          <button
            type="button"
            className="ctl ctl--small"
            onClick={() => void history('redo')}
            disabled={saving}
            title="Redo (Ctrl+Shift+Z)"
          >
            <Redo size={13} />
            Redo
          </button>
          {/* With the other actions, not stranded in the title block at the
              foot of the page. Everything you *do* to the document lives on
              this rail; the block below states what the document is. */}
          <button
            type="button"
            className="ctl ctl--small"
            onClick={() => setIssuing(true)}
            disabled={!doc}
            title="Export a PDF"
          >
            <Sheet size={13} />
            Export PDF
          </button>
        </div>

        {error && <div className="notice notice--error">{error}</div>}
        {loading && <div className="notice">Reading the sheet…</div>}

        {/* Said once, above the document, rather than beside each line: a
            résumé tailored from a template can have a dozen invented lines, and
            twelve buttons is not a review. The underlines say which; this says
            how many and offers the one action worth having. */}
        {unverified.size > 0 && (
          <div className="unchecked">
            <span className="unchecked__count">{unverified.size}</span>
            <p className="unchecked__text">
              {unverified.size === 1 ? 'One line was' : 'These lines were'} written
              by the assistant from a template, so {unverified.size === 1 ? 'it is' : 'they are'}{' '}
              plausible rather than true. Underlined below — replace anything you
              would not want to be asked about.
            </p>
            <button
              type="button"
              className="ctl ctl--small"
              onClick={() => void confirmInvented()}
            >
              These are accurate
            </button>
          </div>
        )}

        <div className="sheet-scroll" ref={scrollRef}>
          {/* `zoom` rather than a transform: it scales the layout, so the pane
              still scrolls to the bottom of a magnified document, and a measured
              rect comes back in the same space the pointer reports. A transform
              would leave the scroll height at 100%.

              `position: relative` because the revision layer paints inside this
              wrapper, so its coordinates and the document's are the same space
              at any zoom. */}
          {doc && (
            <div ref={canvasRef} className="sheet" style={{ zoom }}>
              <PageCanvas
                doc={doc}
                changed={changed}
                locked={locked}
                unverified={unverified}
                drafts={drafts}
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
              <RevisionLayer host={canvasRef} zoom={zoom} />
            </div>
          )}
        </div>

        {/* Above the title block, in the lower right of the sheet -- which is
            exactly where a drawing keeps its revision block. It records what
            the document has had done to it; the conversation about it lives in
            the sidebar. */}
        <RevisionBlock />

        <div className="rail rail--bottom">
          {/* Deliberately outside the scaled wrapper: inside it these would zoom
              along with the document and read 200% while being twice their own
              size. Zoom is a property of this viewer, never of the resume -- it
              reaches no op, so it cannot be undone and cannot bump a version,
              which is also why it sits apart from the title block's fields. */}
          <div className="zoom" aria-label="Magnification">
            <button
              type="button"
              className="zoom__step"
              title="Zoom out"
              aria-label="Zoom out"
              disabled={!doc || !canZoom(zoom, -1)}
              onClick={zoomOut}
            >
              <Minus size={13} />
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
              aria-label="Zoom in"
              disabled={!doc || !canZoom(zoom, 1)}
              onClick={zoomIn}
            >
              <Plus size={13} />
            </button>
          </div>

          <span className="rail__spacer" />

          <div className="title-block">
            <div className="title-block__field">
              <span className="legend">Document</span>
              <span className="title-block__value title-block__value--name">
                {title || 'Untitled'}
              </span>
            </div>

            {/* Revisions accepted in this session, which is what the revision
                block above lists. Deliberately *not* the document's `version`:
                that is an ETag half counting every accepted write, so the
                reflow pass on open bumps it and an undo bumps it too --
                counting up while taking you back. Shown raw it reads as a
                revision number and is not one. It is still sent on every write;
                it is just not something to put in front of a person. */}
            <div className="title-block__field">
              <span className="legend">Rev</span>
              <span className="title-block__value title-block__value--rev">{revisions}</span>
            </div>

            <div className="title-block__field">
              <span className="legend">State</span>
              <span
                className={`title-block__value${
                  saving || streaming ? ' title-block__value--work' : ' title-block__value--live'
                }`}
              >
                {saving ? 'Saving' : streaming ? 'Assistant editing' : 'Saved'}
              </span>
            </div>
          </div>
        </div>
      </section>

      {issuing && (
        <IssueStation
          documentId={docId}
          title={title || 'resume'}
          doc={doc}
          onClose={() => setIssuing(false)}
        />
      )}
    </main>
  );
}
