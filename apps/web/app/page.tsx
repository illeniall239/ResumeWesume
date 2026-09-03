'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

import type { DocumentResponse } from '@/contracts/doc';
import { createDocument, listDocuments } from '@/lib/api';
import DocumentFlow from '@/render/document-flow';
import { BLANK_DOC, PREVIEW_DOC } from '@/render/preview-doc';
import { timeAgo } from '@/lib/when';
import ProviderSettings from '@/settings/provider-settings';
import { useModels } from '@/store/models';
import { Sliders } from '@/ui/marks';
import { TEMPLATES, type TemplateInfo } from '@/render/templates';
import { useImport } from '@/store/import';

/**
 * Picking a template gives you the résumé on the card.
 *
 * Literally that document -- `PREVIEW_DOC` is what the card renders and what
 * gets created, with only the template swapped -- because "exactly as is" is
 * the whole promise a gallery makes, and two separate fixtures would have
 * drifted the first time either was edited. There used to be a second sample
 * in a legacy shape here that differed from the card in its jobs, its projects
 * and its skills; it is gone.
 */
export default function Home() {
  const router = useRouter();
  const startImport = useImport((state) => state.start);
  const fileInput = useRef<HTMLInputElement>(null);
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const loadProviders = useModels((state) => state.load);

  useEffect(() => {
    if (settings) void loadProviders();
  }, [settings, loadProviders]);

  useEffect(() => {
    listDocuments()
      .then(setDocuments)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  async function create(template: TemplateInfo, blank = false) {
    setBusy(true);
    setError(null);
    const title = blank ? 'Untitled' : `Untitled — ${template.name}`;
    try {
      const created = await createDocument(
        blank
          // A skeleton, not an empty document. Creating one with no content at
          // all produced a page carrying nothing but the header -- every
          // section renders only when it has something in it -- so it landed
          // on a blank sheet with nothing to type into.
          ? { title, template: template.id, starter: true }
          // `scaffold` says the words in it are the card's, not yours. It is
          // what lets the assistant replace them wholesale, and it ends the
          // moment you type into the document yourself.
          : { title, doc: { ...PREVIEW_DOC, template: template.id, scaffold: true } }
      );
      // Client navigation, not a document load. Assigning to
      // window.location.href tears down the module-scoped zustand stores,
      // which would kill an import running in the background.
      router.push(`/studio/${created.id}`);
    } catch (cause) {
      setError((cause as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="register">
      <header className="register__head">
        <h1 className="register__title">ResumeWesume</h1>
        <div className="rail__spacer" />
        <button
          className="ctl"
          onClick={() => fileInput.current?.click()}
          disabled={busy}
        >
          Import a PDF instead
        </button>
        {/* The same dialog the studio's model picker opens. Here too, because
            a key belongs to the machine rather than to a document -- somebody
            arriving to start their first résumé should be able to set one
            without opening a résumé first. */}
        <button
          className="ctl"
          type="button"
          onClick={() => setSettings(true)}
          title="Model and API key settings"
        >
          <Sliders size={13} />
          Settings
        </button>
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          onChange={(event) => {
            const file = event.target.files?.[0];
            // Cleared so choosing the same file twice in a row still fires.
            event.target.value = '';
            if (!file) return;
            startImport(file);
            router.push('/import');
          }}
        />
      </header>

      {/* Loaded when it opens rather than on every visit: the catalogue is a
          handful of requests, and a page whose purpose is picking a template
          should not spend them until somebody asks for settings. */}
      {settings && <ProviderSettings onClose={() => setSettings(false)} />}

      {error && <div className="notice notice--error">{error}</div>}

      {/* Recent work first, and along one line. Someone arriving usually wants
          the résumé they were already writing; the gallery is for the rarer
          visit where they are starting another. */}
      <section className="register__section">
        <span className="legend section-legend">Recent</span>
        {documents.length === 0 ? (
          <p className="register__note">No sheets yet.</p>
        ) : (
          <ul className="recents">
            {documents.map((document) => (
              <li key={document.id}>
                <a className="recent" href={`/studio/${document.id}`}>
                  <span className="recent__title">{document.title}</span>
                  {/* When it last changed, not how many writes it has taken.
                      A write count is a fact about the engine; what tells you
                      which résumé this is, is when you last had it open. */}
                  <span className="recent__rev">
                    {timeAgo(document.updated_at)}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="register__section">
        <span className="legend section-legend">Templates</span>

        {/* Real renders, not pictures of renders. Each card runs the same
            `DocumentFlow` the studio and the PDF do, on the same fixture, with
            only the template changed -- so a card cannot go stale against the
            thing it is advertising, and there is no image to ship. */}
        <ul className="gallery">
          {/* Blank sits among the templates rather than as a checkbox above
              them. It is the same kind of choice -- what the sheet starts as --
              and as a card it can show what you get, which a checkbox could
              only describe. */}
          <li>
            <button
              type="button"
              className="card"
              onClick={() => create(TEMPLATES[0], true)}
              disabled={busy}
            >
              <span className="card__paper" aria-hidden="true">
                <span className="card__sheet">
                  <DocumentFlow doc={BLANK_DOC} editable={false} placeholders />
                </span>
              </span>
              <span className="card__name">Blank</span>
              <span className="card__note">
                The headings and nothing else. Start from your own words.
              </span>
            </button>
          </li>

          {TEMPLATES.map((template) => (
            <li key={template.id}>
              <button
                type="button"
                className="card"
                onClick={() => create(template)}
                disabled={busy}
              >
                <span className="card__paper" aria-hidden="true">
                  <span className="card__sheet">
                    <DocumentFlow
                      doc={{ ...PREVIEW_DOC, template: template.id }}
                      editable={false}
                    />
                  </span>
                </span>
                <span className="card__name">{template.name}</span>
                <span className="card__note">{template.note}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

    </main>
  );
}
