/**
 * The review screen: what we read, beside what it was read from.
 *
 * Lives in `src/` rather than in `app/` because `vitest.config.ts` aliases
 * only `@ -> ./src`, so a component under `app/` cannot be imported by a test.
 * The route file is a two-line wrapper around this.
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

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

import { labelFor } from '@/ingest/events';
import DocumentFlow from '@/render/document-flow';
import { useImport, type ImportSection } from '@/store/import';

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

function SectionRow({ section }: { section: ImportSection }) {
  const [open, setOpen] = useState(false);
  const showable = Boolean(section.sourceText);

  return (
    <li className={`check check--${section.status}`}>
      <div className="check__head">
        <span className="check__dot" aria-hidden />
        <span className="check__label">
          {labelFor(section.key, section.heading)}
        </span>
        <span className="check__status">
          {STATUS_TEXT[section.status] ?? section.status}
          {section.status === 'parsed' && countOf(section)
            ? ` · ${countOf(section)}`
            : ''}
        </span>
        {showable && (
          <button
            className="check__toggle"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
          >
            {open ? 'hide source' : 'source'}
          </button>
        )}
      </div>

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

export default function ImportReview() {
  const router = useRouter();
  const state = useImport();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    state.hydrate();
    // Hydration runs once, on mount, and only when nothing is in flight.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const busy = state.status === 'uploading' || state.status === 'parsing';

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
      <main className="import import--empty">
        <h1>Nothing to review</h1>
        <p className="import__muted">
          Upload a resume from the home page to see it here.
        </p>
        <button className="button" onClick={() => router.push('/')}>
          Back
        </button>
      </main>
    );
  }

  return (
    <main className="import">
      <header className="import__head">
        <div>
          <h1 className="import__title">
            {busy ? 'Reading your resume' : 'Check what we read'}
          </h1>
          <p className="import__muted">
            {busy ? (
              <>
                {state.filename} · this runs on your machine, so it takes a
                minute or two. Nothing is saved until you say so.
              </>
            ) : (
              <>
                Nothing has been saved yet. Compare this with your original, then
                import it — you can fix anything else once it is open.
              </>
            )}
          </p>
        </div>
        <div className="import__actions">
          <button className="button" onClick={discard} disabled={saving}>
            {busy ? 'Stop' : 'Discard'}
          </button>
          <button
            className="button button--primary"
            onClick={accept}
            disabled={busy || saving || !state.resumeData}
          >
            {saving ? 'Importing…' : 'Import'}
          </button>
        </div>
      </header>

      {state.error && <div className="notice">{state.error}</div>}
      {state.warnings.map((warning) => (
        <div className="notice notice--soft" key={warning}>
          {warning}
        </div>
      ))}

      <div className="import__grid">
        <section className="import__panel">
          <h2 className="import__h2">Sections</h2>
          <ul className="checks">
            {state.sections.map((section) => (
              <SectionRow
                key={`${section.key}#${section.order}#${section.heading}`}
                section={section}
              />
            ))}
          </ul>
          {state.failed > 0 && (
            <p className="import__muted">
              {state.failed} section{state.failed === 1 ? '' : 's'} could not be
              read. You can still import everything else.
            </p>
          )}
        </section>

        <section className="import__panel import__panel--preview">
          <h2 className="import__h2">
            Preview
            {!busy && (
              <input
                className="import__name"
                value={state.title}
                onChange={(event) => state.setTitle(event.target.value)}
                aria-label="Document name"
              />
            )}
          </h2>
          {state.doc ? (
            <div className="page">
              <DocumentFlow doc={state.doc} editable={false} />
            </div>
          ) : (
            <p className="import__muted">
              The preview appears once every section has been read.
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
