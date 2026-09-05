'use client';

import { use, useEffect, useRef, useState } from 'react';

import type { DocOp } from '@/contracts/doc';
import ChatPanel from '@/chat/chat-panel';
import IssueStation from '@/export/issue-station';
import { InsertToolbar } from '@/canvas/insert-toolbar';
import { SheetLength } from '@/canvas/sheet-length';
import { usePan } from '@/canvas/use-pan';
import { BoardPlane } from '@/canvas/board-plane';
import { mintLike } from '@/canvas/ids';
import { removeElements } from '@/canvas/pages';
import { useSelection } from '@/canvas/selection';
import { textOf } from '@/doc/read';
import { canZoom, fitZoom, useView } from '@/canvas/view';
import { useReflow } from '@/canvas/use-reflow';
import { Minus, Plus, Redo, Undo } from '@/ui/marks';
import { Wordmark } from '@/ui/wordmark';
import { templateLabel } from '@/render/templates';
import { useChat } from '@/store/chat';
import { createDocument } from '@/lib/api';
import { canvasLabel, useCanvas } from '@/store/canvas';
import { useStudio } from '@/store/studio';

/**
 * The board: the conversation at the left, the print at the right.
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
export default function StudioPage({
  params,
}: {
  params: Promise<{ canvasId: string }>;
}) {
  // Named a canvas because that is what it is, and it accepts a board id too:
  // every link written before canvases existed points at one, and so does
  // every bookmark somebody already has.
  const { canvasId } = use(params);

  const doc = useStudio((state) => state.doc);
  const title = useStudio((state) => state.title);
  const loading = useStudio((state) => state.loading);
  const saving = useStudio((state) => state.saving);
  const error = useStudio((state) => state.error);
  const changed = useStudio((state) => state.changed);
  const unverified = useStudio((state) => state.unverified);
  const confirmClaims = useStudio((state) => state.confirmInvented);
  const version = useStudio((state) => state.version);
  const hash = useStudio((state) => state.hash);
  // Text a tool call is writing right now, shown in place while it arrives.
  const drafts = useStudio((state) => state.drafts);
  const locked = useStudio((state) => state.locked);
  const load = useStudio((state) => state.load);
  const edit = useStudio((state) => state.edit);
  const setFocus = useStudio((state) => state.setFocus);
  const history = useStudio((state) => state.history);
  const stage = useStudio((state) => state.stage);
  const flush = useStudio((state) => state.flush);
  const nudgeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [adding, setAdding] = useState(false);

  const zoom = useView((state) => state.zoom);
  const setZoom = useView((state) => state.setZoom);
  const zoomIn = useView((state) => state.zoomIn);
  const zoomOut = useView((state) => state.zoomOut);
  const resetZoom = useView((state) => state.reset);

  const renameDoc = useStudio((state) => state.rename);
  const documentId = useStudio((state) => state.documentId);
  const resetChat = useChat((state) => state.reset);
  const loadChat = useChat((state) => state.load);

  const openCanvas = useCanvas((state) => state.open);
  const resetCanvas = useCanvas((state) => state.reset);
  const absorb = useCanvas((state) => state.absorb);
  const boards = useCanvas((state) => state.boards);
  const selected = useCanvas((state) => state.selected);
  const selectBoard = useCanvas((state) => state.select);
  const canvasTitle = useCanvas((state) => state.title);
  const canvasError = useCanvas((state) => state.error);
  const canvasIdLoaded = useCanvas((state) => state.id);

  // Open the canvas, then the board it selected. Two steps rather than one
  // because they answer different questions: which versions exist, and which
  // of them is the document everything else in here is pointed at.
  useEffect(() => {
    let live = true;
    void openCanvas(canvasId).then((board) => {
      if (!live || !board) return;
      void load(board);
    });
    return () => {
      live = false;
      resetChat();
      resetCanvas();
    };
  }, [canvasId, openCanvas, resetCanvas, load, loadChat, resetChat]);

  // The conversation belongs to the résumé, not to whichever version is open.
  // Read once, and it stays put as versions are switched between: asking for a
  // second version and reading the reply are one exchange, and a transcript
  // that changed underneath that would be changing the subject mid-sentence.
  useEffect(() => {
    if (!canvasIdLoaded) return;
    void loadChat(canvasIdLoaded);
  }, [canvasIdLoaded, loadChat]);

  // Selecting another version opens it. The rails and the agent follow the
  // selection because they read `useStudio`, which holds one document at a
  // time; the transcript above deliberately does not.
  useEffect(() => {
    if (!selected || selected === documentId) return;
    void load(selected);
  }, [selected, documentId, load]);

  // The plane draws unselected boards from what the server last sent, so an
  // edit to the live one has to be folded back in — otherwise switching away
  // and back would show the version from before the edit.
  useEffect(() => {
    if (!doc || !documentId) return;
    absorb({ id: documentId, title, version, hash, doc } as never);
  }, [doc, documentId, title, version, hash, absorb]);

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
  // Which template the sheet is set in, unless its name already says so.
  const template = templateLabel(title, doc?.template);
  // And which résumé it is a version of, unless the board's own name says that.
  const canvas = canvasLabel(canvasTitle, title);
  // On an empty canvas there is no version, so there is no second name to say
  // -- and saying one anyway printed "Untitled / Untitled", which is the same
  // word twice for two different things.
  const hasBoard = Boolean(documentId);

  // Lines only the job posting vouches for, in their own words. Read from the
  // document rather than tracked separately, so they survive a reload and
  // disappear the moment one is edited.
  const claimed = [...unverified]
    .map((nid) => textOf(doc, nid).trim())
    .filter(Boolean);

  const scrollRef = useRef<HTMLDivElement>(null);
  // Dragging the canvas to move around it. Space-drag, middle-drag, or a drag
  // on the ground between versions -- never on a version, where the pointer
  // already means select, edit or marquee.
  const pan = usePan(scrollRef);
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    const pane = scrollRef.current;
    if (!doc || !pane || fitted.current === documentId) return;
    const sheet = pane.querySelector('.canvas-page');
    if (!sheet) return;

    fitted.current = documentId;
    // The rendered width already carries the current zoom, so it is divided
    // back out to get the sheet's own width before choosing a step.
    const sheetWidth = sheet.getBoundingClientRect().width / zoom;
    const room = pane.clientWidth - 32;
    if (sheetWidth > room) setZoom(fitZoom(room, sheetWidth));
    // `zoom` is read, not depended on: reacting to it would refit the moment
    // the user zoomed in, which is the opposite of what they asked for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, documentId, setZoom]);

  /**
   * Put a résumé on this canvas.
   *
   * A skeleton rather than a truly empty document: a sheet with no content at
   * all renders as nothing but a header, because every section draws only when
   * it holds something -- so it would land on a blank page with nowhere to
   * type.
   */
  async function addBoard() {
    if (!canvasIdLoaded) return;
    setAdding(true);
    try {
      const created = await createDocument({
        title: 'Untitled',
        template: 'plain',
        starter: true,
        canvas_id: canvasIdLoaded,
      });
      // Through the canvas store, so the plane and the selection move together
      // -- the same path the assistant's own fork takes.
      useStudio.getState().adoptBoard(created.id, created.title);
    } catch (cause) {
      useStudio.setState({ error: (cause as Error).message });
    } finally {
      setAdding(false);
    }
  }

  return (
    <main className="deck">
      {/* Above both panes, not over the drawing alone. This bar names the
          document and carries what you do to it as a whole; the conversation
          on the left is about that document, so it should not begin above the
          line that says which one it is. */}
      <div className="rail rail--top">
        {/* The wordmark keeps the width of the column below it, so the rule
            between the two halves of this bar is the one that already runs
            between the conversation and the sheet -- and the document's name
            sits over the document. */}
        <div className="rail__brand">
          <Wordmark size={12} />
        </div>

        <div className="rail__actions">
          <span className="rail__doc">
            {/* Which résumé this is, when that is not the same as which version
                of it. A canvas and its first board are created together under
                one name, so on the sheet almost everybody has this stays out of
                the way; it earns its place once the versions have names of
                their own. */}
            {(canvas || !hasBoard) && (
              <span className={hasBoard ? 'rail__canvas' : 'rail__name'}>
                {canvasTitle || 'Untitled'}
              </span>
            )}
            {canvas && hasBoard && (
              <span className="rail__slash" aria-hidden="true">
                /
              </span>
            )}
            {/* Which version this is, where you look first, and editable in
                place. `plaintext-only` and blur-to-commit, exactly as every
                line of the résumé behaves -- a title that had to be renamed
                from somewhere else would be the one piece of text on screen
                that did not work the way the rest does.

                Keyed on the title so React replaces the node when the server's
                answer differs from what was typed. Without that the DOM keeps
                whatever is in it, and a name the server trimmed or refused
                would stay on screen looking saved. */}
            {hasBoard && (
            <span
              key={title}
              className="rail__name"
              contentEditable={Boolean(doc)}
              suppressContentEditableWarning
              spellCheck={false}
              role="textbox"
              aria-label="Document name"
              title="Rename"
              onBlur={(event) => {
                void renameDoc(event.currentTarget.textContent ?? '');
              }}
              onKeyDown={(event) => {
                // Enter commits rather than inserting a line: this is a name,
                // and the field is one line high.
                if (event.key === 'Enter') {
                  event.preventDefault();
                  event.currentTarget.blur();
                }
                if (event.key === 'Escape') {
                  event.currentTarget.textContent = title || 'Untitled';
                  event.currentTarget.blur();
                }
              }}
            >
              {title || 'Untitled'}
            </span>
            )}
          {/* What the résumé is set in. A fact about the document, beside the
              other one, and stated rather than offered: a template is chosen
              when the sheet is created and no op changes it afterwards, so the
              caret the design draws here would open nothing.

              Withheld when the name already carries it -- see
              `templateLabel`. */}
          {template && <span className="rail__template">{template}</span>}
          </span>
          <span className="rail__spacer" />
          {/* The icon alone, no frame. These are used constantly and a box
              around each puts two more outlines on a bar that is otherwise
              hairlines and one filled action. The label lives in the tooltip
              and in `aria-label`, so nothing is lost but the chrome.

              A pair, spaced closer to each other than to the rest of the bar:
              undo and redo are one control with two directions. */}
          <div className="rail__history">
            <button
              type="button"
              className="ctl ctl--bare"
              onClick={() => void history('undo')}
              disabled={saving}
              title="Undo (Ctrl+Z)"
              aria-label="Undo"
            >
              <Undo size={16} />
            </button>
            <button
              type="button"
              className="ctl ctl--bare"
              onClick={() => void history('redo')}
              disabled={saving}
              title="Redo (Ctrl+Shift+Z)"
              aria-label="Redo"
            >
              <Redo size={16} />
            </button>
        </div>

        <span className="rail__divider" aria-hidden="true" />
        {/* With the other actions, not stranded in the title block at the
            foot of the page. Everything you *do* to the document lives on
            this rail; the block below states what the document is. */}
        {/* The one filled control on the sheet: it is the only thing here
            that produces a file. */}
        <button
          type="button"
          className="ctl ctl--primary"
          onClick={() => setIssuing(true)}
          disabled={!doc}
          title="Export a PDF"
        >
        Export PDF
      </button>
        </div>
      </div>

      <div className="deck__panes">
        <aside>
          <ChatPanel documentId={documentId ?? canvasId} />
        </aside>

        <section className="deck__board">
          {/* Its own strip under the document's bar, rather than inline with the
              name. The top rail says what this document *is* and what you can do
              to it as a whole; this says what you can put on it -- two different
              questions that were sharing one line. */}
          {doc && (
            <div className="rail rail--tools">
              <span className="legend">Insert</span>
              <InsertToolbar
                doc={doc}
                documentId={documentId ?? canvasId}
                commit={(ops) => void edit(ops)}
              />
              <span className="rail__spacer" />
              {/* How long it is. The one fact about a résumé everybody is told
                  to care about, which this app knew and never said. */}
              <SheetLength doc={doc} />
            </div>
          )}

          {(error || canvasError) && (
            <div className="notice notice--error">{error || canvasError}</div>
          )}
          {loading && <div className="notice">Reading the sheet…</div>}

          {/* Claims the posting vouches for and nobody else does.
              `add_skill(evidence="jd")` verifies only that the word is in the
              advert -- not that it is anywhere in your résumé, and not that you
              ever said you have it. Named rather than counted: "1 line was
              written by the assistant" is nothing you can act on, and these two
              words are exactly what to look at. */}
          {claimed.length > 0 && (
            <div className="claims">
              <span>
                <span className="claims__what">{claimed.join(', ')}</span>{' '}
                came from the job posting, not from your résumé.
              </span>
              <span className="rail__spacer" />
              <button
                type="button"
                className="ctl ctl--small"
                onClick={() => void confirmClaims()}
                title="Clear the marks"
              >
                {claimed.length === 1 ? 'I have this' : 'I have these'}
              </button>
            </div>
          )}


          <div
            className={`sheet-scroll${pan.panning ? ' sheet-scroll--panning' : pan.ready ? ' sheet-scroll--pannable' : ''}`}
            ref={scrollRef}
          >
            {/* Every version of this résumé, one of them live.

                `zoom` rather than a transform: it scales the layout, so the
                pane still scrolls to the bottom of a magnified document, and a
                measured rect comes back in the same space the pointer reports.
                A transform would leave the scroll height at 100%. */}
            {boards.length > 0 ? (
              <BoardPlane
                boards={boards}
                selected={selected}
                onSelect={selectBoard}
                documentId={documentId}
                zoom={zoom}
                hostRef={canvasRef}
                commit={(ops) => void edit(ops)}
                onEditText={(nid, value) => {
                  void edit([{ op: 'set_text', nid, value }]);
                }}
                onEditField={(target, value) => {
                  void edit([{ op: 'set_field', target, value }]);
                }}
                onFocusNode={setFocus}
              />
            ) : (
              !loading && (
                // A canvas with nothing on it. Real rather than broken:
                // somebody building a résumé from scratch starts here, and
                // this is where the sheet is offered -- sending them back to
                // the home screen to choose a template would be answering a
                // question they have already answered by coming here.
                <div className="plane__empty">
                  <p>Nothing on this canvas yet.</p>
                  <button
                    type="button"
                    className="ctl ctl--primary"
                    onClick={() => void addBoard()}
                    disabled={adding}
                  >
                    {adding ? 'Starting…' : 'Start a résumé'}
                  </button>
                </div>
              )
            )}
          </div>

          <div className="rail rail--bottom">
            {/* Deliberately outside the scaled wrapper: inside it these would zoom
                along with the document and read 200% while being twice their own
                size. Zoom is a property of this viewer, never of the resume -- it
                reaches no op, so it cannot be undone and cannot bump a version. */}
            <span className="rail__spacer" />
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
          </div>
        </section>
      </div>

      {issuing && (
        <IssueStation
          documentId={documentId ?? canvasId}
          title={title || 'resume'}
          doc={doc}
          onClose={() => setIssuing(false)}
        />
      )}
    </main>
  );
}
