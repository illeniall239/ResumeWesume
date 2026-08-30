'use client';

import { use, useEffect } from 'react';

import ChatPanel from '@/chat/chat-panel';
import DocumentFlow from '@/render/document-flow';
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
  const version = useStudio((state) => state.version);
  const loading = useStudio((state) => state.loading);
  const saving = useStudio((state) => state.saving);
  const error = useStudio((state) => state.error);
  const changed = useStudio((state) => state.changed);
  const locked = useStudio((state) => state.locked);
  const load = useStudio((state) => state.load);
  const edit = useStudio((state) => state.edit);
  const setFocus = useStudio((state) => state.setFocus);

  const streaming = useChat((state) => state.streaming);
  const resetChat = useChat((state) => state.reset);

  useEffect(() => {
    void load(docId);
    return () => resetChat();
  }, [docId, load, resetChat]);

  return (
    <main className="studio">
      <aside className="pane">
        <ChatPanel documentId={docId} />
      </aside>

      <section className="pane pane--doc">
        <div className="toolbar toolbar--doc">
          <strong>{title || 'Resume'}</strong>
          <span className="badge">v{version}</span>
          {saving && <span className="badge">saving…</span>}
          {streaming && <span className="badge badge--live">assistant editing</span>}
          <span className="toolbar__spacer" />
          <a className="button" href={pdfUrl(docId)} target="_blank" rel="noreferrer">
            Export PDF
          </a>
        </div>

        {error && <div className="notice notice--error">{error}</div>}
        {loading && <div className="notice">Loading…</div>}

        {doc && (
          <div className="page">
            <DocumentFlow
              doc={doc}
              changed={changed}
              locked={locked}
              editable
              onFocusNode={setFocus}
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
