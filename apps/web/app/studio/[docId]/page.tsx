'use client';

import { use, useEffect } from 'react';

import DocumentFlow from '@/render/document-flow';
import { pdfUrl } from '@/lib/api';
import { useStudio } from '@/store/studio';

/**
 * The studio: chat on the left, live document on the right.
 *
 * In P0 the left pane is a placeholder. The document pane is real — it renders
 * from the store, supports direct inline editing, and every edit round-trips
 * through the same op pipeline the agent will use, so wiring the agent in later
 * needs no changes on this side.
 */
export default function StudioPage({ params }: { params: Promise<{ docId: string }> }) {
  const { docId } = use(params);

  const doc = useStudio((state) => state.doc);
  const title = useStudio((state) => state.title);
  const version = useStudio((state) => state.version);
  const loading = useStudio((state) => state.loading);
  const saving = useStudio((state) => state.saving);
  const error = useStudio((state) => state.error);
  const changed = useStudio((state) => state.changed);
  const locked = useStudio((state) => state.locked);
  const rejected = useStudio((state) => state.rejected);
  const load = useStudio((state) => state.load);
  const edit = useStudio((state) => state.edit);

  useEffect(() => {
    void load(docId);
  }, [docId, load]);

  return (
    <main className="studio">
      <aside className="pane">
        <div className="toolbar">
          <strong>Assistant</strong>
          <span className="toolbar__spacer" />
          <span className="badge">P1</span>
        </div>
        <div className="notice">
          The assistant arrives in P1. The document beside it is already live:
          click any bullet and type, and the edit goes through the same
          validated op pipeline the agent will use.
        </div>
        {rejected.length > 0 && (
          <div className="notice">
            <strong>Rejected</strong>
            <ul>
              {rejected.map((entry, index) => (
                <li key={index}>
                  <code>{entry.code}</code> — {entry.message}
                </li>
              ))}
            </ul>
          </div>
        )}
      </aside>

      <section className="pane pane--doc">
        <div className="toolbar" style={{ width: '210mm', maxWidth: '100%' }}>
          <strong>{title || 'Resume'}</strong>
          <span className="badge">v{version}</span>
          {saving && <span className="badge">saving…</span>}
          <span className="toolbar__spacer" />
          <a className="button" href={pdfUrl(docId)} target="_blank" rel="noreferrer">
            Export PDF
          </a>
        </div>

        {error && <div className="notice">{error}</div>}
        {loading && <div className="notice">Loading…</div>}

        {doc && (
          <div className="page">
            <DocumentFlow
              doc={doc}
              changed={changed}
              locked={locked}
              editable
              onEditText={(nid, value) => {
                void edit([{ op: 'set_text', nid, value }]);
              }}
            />
          </div>
        )}
      </section>
    </main>
  );
}
