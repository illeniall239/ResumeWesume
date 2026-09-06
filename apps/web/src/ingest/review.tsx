/**
 * The review screen: what we read, beside what it was read from.
 *
 * Lives in `src/` rather than in `app/` because `vitest.config.ts` aliases
 * only `@ -> ./src`, so a component under `app/` cannot be imported by a test.
 * The route file is a two-line wrapper around this.
 *
 * It is the studio's frame, not a page of its own. It was a centred 1180px
 * document with a heading and two columns -- a form about a résumé. But this
 * screen is the same act as the studio: a sheet on a board, with a rail beside
 * it saying what is true of it. Wearing a different shape made the import read
 * as a detour on the way to the app rather than as the first sight of it, and
 * the sheet -- the whole point of the screen -- was the part that lost room.
 *
 * Two decisions worth stating.
 *
 * The preview is the real `DocumentFlow` with `editable={false}`, not a
 * summary of fields. The user is being asked to accept a document, so they
 * should be looking at the document -- and it costs no new rendering code,
 * because that component is already pure and already styled.
 *
 * A failed section does not block the import. "Import what worked" is the
 * containment promise the per-section design makes, and a review screen that
 * refused to continue would quietly turn one bad section into a total failure
 * -- exactly the outcome the design was meant to avoid.
 */

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

import { labelFor } from '@/ingest/events';
import { Check, Cross, Minus, NotApplicable, Pending, Plus, Running } from '@/ui/marks';
import DocumentFlow from '@/render/document-flow';
import { PageCanvas } from '@/canvas/page-canvas';
import Wordmark from '@/ui/wordmark';
import type { DocOp } from '@/contracts/doc';
import { useImport, type ImportSection } from '@/store/import';
import { useReflow } from '@/canvas/use-reflow';
import { useView } from '@/canvas/view';

const STATUS_TEXT: Record<string, string> = {
  pending: 'waiting',
  running: 'reading…',
  parsed: 'read',
  failed: 'could not read',
  skipped: 'not imported',
};

function countOf(section: ImportSection): string {
  const data = section.data;
  if (!data) return '';
  const entries = data.entries;
  if (Array.isArray(entries)) {
    return `${entries.length} ${entries.length === 1 ? 'entry' : 'entries'}`;
  }
  const items = data.items;
  if (Array.isArray(items)) {
    return `${items.length} ${items.length === 1 ? 'item' : 'items'}`;
  }
  if (typeof data.summary === 'string' && data.summary) return 'found';
  if (section.key === 'contact') return 'found';
  return '';
}

/** Which contact details were found, in the words a person uses for them. */
const CONTACT_FIELDS: [string, string][] = [
  ['phone', 'phone'],
  ['email', 'email'],
  ['location', 'location'],
  ['github', 'github'],
  ['linkedin', 'linkedin'],
  ['website', 'site'],
];

/** What an entry calls itself: an employer, an institution, a project. */
function nameOf(entry: unknown): string {
  if (!entry || typeof entry !== 'object') return '';
  const fields = entry as Record<string, unknown>;
  for (const key of ['company', 'institution', 'name', 'title']) {
    const value = fields[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function shorten(text: string, limit = 24): string {
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

/**
 * One line naming what actually came out of a section.
 *
 * The count beside the heading says how much; this says what. "5 entries" is
 * true of a section that read five jobs and of one that read the same job five
 * times, and the point of this screen is to be checkable against the original
 * -- so the row names the first employer and the last, and the reader can tell
 * in one glance whether the span is right.
 */
function noteOf(section: ImportSection): string {
  const data = section.data;
  if (!data) return '';

  const entries = data.entries;
  if (Array.isArray(entries) && entries.length) {
    const names = entries.map(nameOf).filter(Boolean).map((name) => shorten(name));
    if (!names.length) return '';
    if (names.length === 1) return names[0];
    return `${names[0]} → ${names[names.length - 1]}`;
  }

  const items = data.items;
  if (Array.isArray(items) && items.length) {
    // As many as fit on one line, rather than a fixed three: "Python, SQL, R"
    // and three IBM certificate titles are the same count and not remotely the
    // same length, and the count of what is left has to survive the trim.
    const shown: string[] = [];
    for (const item of items) {
      const text = String(item).trim();
      if (!text) continue;
      if (!shown.length) {
        shown.push(shorten(text, 34));
        continue;
      }
      if ([...shown, text].join(', ').length > 40) break;
      shown.push(text);
    }
    if (!shown.length) return '';
    const rest = items.length - shown.length;
    return rest > 0 ? `${shown.join(', ')} + ${rest} more` : shown.join(', ');
  }

  if (section.key === 'contact') {
    return CONTACT_FIELDS.filter(([field]) => {
      const value = data[field];
      return typeof value === 'string' && value.trim();
    })
      .map(([, label]) => label)
      .join(', ');
  }

  if (typeof data.summary === 'string' && data.summary.trim()) {
    const lines = countLines(section.sourceText);
    return lines ? `${lines} ${lines === 1 ? 'line' : 'lines'}` : '';
  }

  return '';
}

function countLines(text: string | undefined): number {
  if (!text) return 0;
  return text.split('\n').filter((line) => line.trim()).length;
}

/**
 * The mark a checker puts against a line.
 *
 * State is carried by which mark it is, not only by what colour it is: a
 * checked item and a queried one differ in shape, so the column can be scanned
 * without relying on hue.
 */
const MARKS: Record<string, typeof Check> = {
  parsed: Check,
  failed: Cross,
  running: Running,
  skipped: NotApplicable,
  pending: Pending,
};

function SectionRow({ section }: { section: ImportSection }) {
  const [open, setOpen] = useState(false);
  const showable = Boolean(section.sourceText);
  const Mark = MARKS[section.status] ?? Pending;
  const note = section.status === 'parsed' ? noteOf(section) : '';
  const count = section.status === 'parsed' ? countOf(section) : '';

  return (
    <li className={`check check--${section.status}`}>
      <div className="check__head">
        <span className="check__mark">
          <Mark size={13} />
        </span>
        <span className="check__label">
          {labelFor(section.key, section.heading)}
        </span>
        {/* A rule, not a gap. The count and the source link are a column of
            their own down the rail, and space alone would let them wander with
            the length of each heading. */}
        <span className="check__spring" aria-hidden="true" />
        <span className="check__status">
          {count || STATUS_TEXT[section.status] || section.status}
        </span>
        {showable && (
          <button
            className="check__peek"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
          >
            {open ? 'hide' : 'source'}
          </button>
        )}
      </div>

      {note && <p className="check__note">{note}</p>}

      {section.status === 'failed' && (
        <p className="check__reason">
          {section.message || 'This section could not be read.'} Its text is
          below, so nothing has been lost — you can add it by hand, or ask the
          assistant to once the document is open.
        </p>
      )}
      {section.status === 'skipped' && (
        <p className="check__reason">
          “{section.heading}” is not a section we import yet, so it is not in
          the document below. Its text is kept here.
        </p>
      )}

      {open && <pre className="check__source">{section.sourceText}</pre>}
    </li>
  );
}

/** What the board is showing: the print, the text it was read from, or both. */
type View = 'print' | 'source' | 'split';

export default function ImportReview() {
  const router = useRouter();
  const state = useImport();
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState<View>('print');
  const sheetRef = useRef<HTMLDivElement>(null);

  const zoom = useView((view) => view.zoom);
  const zoomIn = useView((view) => view.zoomIn);
  const zoomOut = useView((view) => view.zoomOut);
  const resetZoom = useView((view) => view.reset);

  useEffect(() => {
    state.hydrate();
    // Hydration runs once, on mount, and only when nothing is in flight.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const busy = state.status === 'uploading' || state.status === 'parsing';

  // The same pass the studio runs on a freshly laid-out document: the server
  // places the frames without being able to measure them, so until this runs
  // the sections sit on top of one another and everything is on page one.
  // Committed into the store rather than to the API, because nothing here is
  // saved yet -- see `reflowPreview`.
  const reflowLocally = useCallback(
    (ops: DocOp[]) => useImport.getState().reflowPreview(ops),
    []
  );
  useReflow(sheetRef, state.doc, reflowLocally);

  async function accept() {
    setSaving(true);
    try {
      const id = await state.confirm();
      router.push(`/studio/${id}`);
    } catch {
      setSaving(false);
    }
  }

  function discard() {
    state.cancel();
    state.reset();
    router.push('/');
  }

  if (state.status === 'idle') {
    return (
      <main className="checker">
        <h1 className="checker__title">Nothing to check</h1>
        <p className="checker__muted">
          Upload a resume from the home page to see it here.
        </p>
        <button className="ctl" onClick={() => router.push('/')}>
          Back
        </button>
      </main>
    );
  }

  const read = state.sections.filter(
    (section) => section.status === 'parsed'
  ).length;
  const lines = countLines(state.sourceText);
  // How the file was divided, and how it comes out. Worth saying because they
  // routinely differ: a résumé exported with a page break after the summary
  // arrives as three pages and reads as two once it is set in a template. The
  // board shows what you will get; these two numbers say what you brought.
  const sheets = state.doc?.pages?.length ?? 0;
  const showPrint = view !== 'source';
  const showSource = view !== 'print';
  // Side by side has to fit two documents into one board. An A4 sheet at 100%
  // is already three quarters of the room, so the print comes down to make
  // space -- and the readout below says the number you are actually looking
  // at, because a magnification that lies is worse than none.
  const scale = view === 'split' ? zoom * 0.62 : zoom;

  return (
    <main className="deck deck--review">
      {/* The studio's bar, doing the studio's job: which document this is, and
          what you can do to it as a whole. The rule between its halves is the
          rule between the rail and the board, continued upward. */}
      <div className="rail rail--top">
        <div className="rail__brand">
          <Wordmark size={12} />
        </div>

        <div className="rail__actions">
          <span className="rail__doc">
            {/* The name it will be filed under, editable here because this is
                the only screen where it can be: the API takes a title at
                creation and has no rename endpoint. */}
            <input
              className="rail__name rail__name--field"
              value={state.title}
              onChange={(event) => state.setTitle(event.target.value)}
              aria-label="Document name"
              spellCheck={false}
            />
            {/* Stated, not offered. Nothing here is saved, and saying so beside
                the name is quieter than a banner and harder to miss than one. */}
            <span className="rail__template">
              {busy ? 'reading' : 'parsed draft'}
            </span>
          </span>

          <span className="rail__spacer" />

          <button className="ctl" onClick={discard} disabled={saving}>
            {busy ? 'Stop' : 'Discard'}
          </button>
          <button
            className="ctl ctl--primary"
            onClick={accept}
            disabled={busy || saving || !state.resumeData}
          >
            {saving ? 'Importing…' : 'Import to editor'}
          </button>
        </div>
      </div>

      <div className="deck__panes">
        <aside className="read">
          <div className="rail read__head">
            <span className="read__title">What we read</span>
            <span className="rail__spacer" />
            <span className="read__count">
              {read} of {state.sections.length || 0} sections
            </span>
          </div>

          <div className="read__body">
            <p className="read__intro">
              {busy ? (
                <>
                  {state.filename} — this runs on your machine, so it takes a
                  minute or two. Nothing is saved until you say so.
                </>
              ) : (
                <>
                  Nothing has been saved yet. Compare this against your
                  original, then import — anything still wrong is easier to fix
                  once the file is open in the editor.
                </>
              )}
            </p>

            {state.error && <div className="notice notice--error">{state.error}</div>}
            {state.warnings.map((warning) => (
              <div className="notice notice--soft" key={warning}>
                {warning}
              </div>
            ))}

            <ul className="checks">
              {state.sections.map((section) => (
                <SectionRow
                  key={`${section.key}#${section.order}#${section.heading}`}
                  section={section}
                />
              ))}
            </ul>

            {state.failed > 0 && (
              <p className="read__failed">
                {state.failed} section{state.failed === 1 ? '' : 's'} could not
                be read. You can still import everything else.
              </p>
            )}
          </div>
        </aside>

        <section className="deck__board">
          <div className="rail rail--tools">
            <span className="read__strip">The print</span>
            <span className="read__sub">
              {sheets
                ? `${sheets} ${sheets === 1 ? 'page' : 'pages'} once imported`
                : 'how it reads once imported'}
            </span>
            <span className="rail__spacer" />
            {/* The source text, not the original PDF. The file itself is not
                kept past the read -- offering to show it would be a button
                that cannot do what it says. */}
            <button
              type="button"
              className={`chip${view === 'source' ? ' chip--on' : ''}`}
              aria-pressed={view === 'source'}
              disabled={!state.sourceText}
              onClick={() => setView(view === 'source' ? 'print' : 'source')}
            >
              Source text
            </button>
            <button
              type="button"
              className={`chip${view === 'split' ? ' chip--on' : ''}`}
              aria-pressed={view === 'split'}
              disabled={!state.sourceText}
              onClick={() => setView(view === 'split' ? 'print' : 'split')}
            >
              Side by side
            </button>
          </div>

          <div className="sheet-scroll">
            <div className={`spread${view === 'split' ? ' spread--split' : ''}`}>
              {showSource && (
                <pre className="spread__source">{state.sourceText}</pre>
              )}
              {showPrint &&
                (state.doc ? (
                  // `zoom`, not a transform: it scales the layout, so the pane
                  // still scrolls to the bottom of a magnified sheet.
                  <div className="spread__paper" style={{ zoom: scale }}>
                    {/* Sheets, not one long column. The same read-only
                        `PageCanvas` the studio draws an unselected version
                        with, so what you approve here is the document you
                        open -- page breaks and all. */}
                    {state.doc.pages?.length ? (
                      <div className="sheet" ref={sheetRef}>
                        <PageCanvas doc={state.doc} editable={false} />
                      </div>
                    ) : (
                      // A parse stashed before the preview carried a page
                      // layout, found again on reload. It has content and no
                      // sheets to put it on, so it flows -- which is what this
                      // screen did for every import until now.
                      <div className="page">
                        <DocumentFlow doc={state.doc} editable={false} />
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="plane__empty">
                    <p>The print appears once every section has been read.</p>
                  </div>
                ))}
            </div>
          </div>

          <div className="rail rail--bottom">
            {/* The one line that says what happened, in the words of the thing
                that happened. It ends on "not saved" because that is the fact
                the screen exists to keep in front of somebody. */}
            <span className="read__ledger">
              {busy
                ? `reading ${state.filename}…`
                : `read ${state.filename} — ${state.pages ? `${state.pages} ${state.pages === 1 ? 'page' : 'pages'}, ` : ''}${state.sections.length} sections, ${lines} lines, ${state.failed} unreadable — not saved`}
            </span>
            <span className="rail__spacer" />
            <div className="zoom" aria-label="Magnification">
              <button
                type="button"
                className="zoom__step"
                title="Zoom out"
                aria-label="Zoom out"
                disabled={!state.doc}
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
                {Math.round(scale * 100)}%
              </button>
              <button
                type="button"
                className="zoom__step"
                title="Zoom in"
                aria-label="Zoom in"
                disabled={!state.doc}
                onClick={zoomIn}
              >
                <Plus size={13} />
              </button>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
