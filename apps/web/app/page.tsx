'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

import type { CanvasResponse } from '@/contracts/doc';
import { createDocument, fetchCanvases } from '@/lib/api';
import DocumentFlow from '@/render/document-flow';
import { BLANK_DOC, PREVIEW_DOC } from '@/render/preview-doc';
import { TemplateCard } from '@/render/template-card';
import CanvasCard from '@/register/canvas-card';
import { search } from '@/register/search';
import ProviderSettings from '@/settings/provider-settings';
import { useModels } from '@/store/models';
import { Caret, Plus, Search, Sliders } from '@/ui/marks';
import { Wordmark } from '@/ui/wordmark';
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
  const [canvases, setCanvases] = useState<CanvasResponse[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState(false);
  const [query, setQuery] = useState('');
  const loadProviders = useModels((state) => state.load);

  // Loaded when the dialog opens rather than on every visit: nothing on this
  // page names a model any more, so a request for the catalogue would be spent
  // before anybody asked for it.
  useEffect(() => {
    if (settings) void loadProviders();
  }, [settings, loadProviders]);

  // Canvases, not documents. A board is a document and always was, but what
  // you pick from here is the résumé and every version of it aimed at a
  // particular job — and those belong together on one row.
  useEffect(() => {
    fetchCanvases()
      .then(setCanvases)
      .catch((cause: Error) => setError(cause.message));
  }, []);

  async function create(template: TemplateInfo, blank = false) {
    setBusy(true);
    setError(null);
    // Just "Untitled". The template used to be appended here because there was
    // nowhere else to say which one a sheet was set in; the studio's bar now
    // states it beside the name, and carrying it in both places printed
    // "Untitled — Centered  Centered" across the top of the document. A name is
    // the person's to give, and "Untitled" is a better thing to type over.
    const title = 'Untitled';
    try {
      const created = await createDocument(
        blank
          // A skeleton, not an empty document. Creating one with no content at
          // all produced a page carrying nothing but the header -- every
          // section renders only when it has something in it -- so it landed
          // on a blank sheet with nothing to type into.
          ? { title, template: template.id, layout: template.layout, starter: true }
          // `scaffold` says the words in it are the card's, not yours. It is
          // what lets the assistant replace them wholesale, and it ends the
          // moment you type into the document yourself.
          : {
              title,
              doc: {
                ...PREVIEW_DOC,
                template: template.id,
                layout: template.layout,
                scaffold: true,
              },
            }
      );
      // Client navigation, not a document load. Assigning to
      // window.location.href tears down the module-scoped zustand stores,
      // which would kill an import running in the background.
      // The canvas, not the board. Both work — the studio follows a board id
      // to the canvas it sits on — but the URL should say what the screen is.
      router.push(`/studio/${created.canvas_id ?? created.id}`);
    } catch (cause) {
      setError((cause as Error).message);
      setBusy(false);
    }
  }

  const filtered = search(canvases, query);

  return (
    <main className="register">
      {/* --- top bar ------------------------------------------------------- */}
      <div className="reg-bar">
        <Wordmark size={15} />

        <span className="reg-bar__spring" />

        {/* Filters the list as you type. A search box that only looked like
            one would be the same lie as the Archive tab. */}
        <label className="reg-search">
          <Search size={13} />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search"
            aria-label="Search your résumés"
          />
        </label>

        <button className="reg-link" onClick={() => fileInput.current?.click()} disabled={busy}>
          Import a PDF
        </button>

        {/* A key belongs to the machine rather than to a document, so somebody
            arriving to start their first résumé can set one without opening a
            résumé first. */}
        <button
          className="reg-link"
          type="button"
          onClick={() => setSettings(true)}
          title="Model and API key settings"
        >
          <Sliders size={13} />
          Settings
        </button>


        <button
          className="reg-new"
          type="button"
          onClick={() => create(TEMPLATES[0], true)}
          disabled={busy}
        >
          New résumé
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
      </div>

      {/* Loaded when it opens rather than on every visit: the catalogue is a
          handful of requests, and a page whose purpose is picking a template
          should not spend them until somebody asks for settings. */}
      {settings && <ProviderSettings onClose={() => setSettings(false)} />}

      <div className="reg-body">
        {error && <p className="reg-notice">{error}</p>}

        <div className="reg-meta">
          <span>
            {filtered.length} {filtered.length === 1 ? 'résumé' : 'résumés'}
            {query.trim() && canvases.length !== filtered.length
              ? ` of ${canvases.length}`
              : ''}
          </span>
          <span className="reg-meta__sort">
            Last edited
            <Caret size={12} />
          </span>
        </div>

        {/* Real renders, not pictures of renders: every board runs the same
            `DocumentFlow` the studio and the PDF do, on the document it names,
            so a card cannot go stale against the thing it opens. */}
        <ul className="reg-docs">
          {filtered.map((canvas) => (
            <li key={canvas.id}>
              <CanvasCard
                canvas={canvas}
                onDeleted={(id) =>
                  setCanvases((current) => current.filter((held) => held.id !== id))
                }
                onError={setError}
              />
            </li>
          ))}

          {/* The empty slot closes the row, and is the same action as the
              button in the bar -- offered where the eye already is. */}
          {!query.trim() && (
            <li>
              <button
                type="button"
                className="reg-slot"
                onClick={() => create(TEMPLATES[0], true)}
                disabled={busy}
              >
                <Plus size={15} />
                New résumé
                {/* The other way in, for somebody who would rather build the
                    sheet themselves than start from a skeleton. Its own press,
                    not a second click target inside this one. */}
              </button>
            </li>
          )}
        </ul>

        {filtered.length === 0 && query.trim() && (
          <p className="reg-empty">Nothing here matches “{query.trim()}”.</p>
        )}

        {/* --- templates --------------------------------------------------- */}
        <section className="reg-strip">
          <div className="reg-strip__head">
            <span className="reg-strip__label">Start from a template</span>
            <span className="reg-strip__count">{TEMPLATES.length + 1}</span>
          </div>

          <ul className="reg-strip__list">
            {/* Blank sits among the templates rather than as a control above
                them. It is the same kind of choice -- what the sheet starts as
                -- and as a card it can show what you get. */}
            <li>
              <button
                type="button"
                className="reg-tpl"
                onClick={() => create(TEMPLATES[0], true)}
                disabled={busy}
                title="The headings and nothing else"
              >
                <span className="reg-tpl__paper" aria-hidden="true">
                  <span className="reg-tpl__sheet">
                    <DocumentFlow doc={BLANK_DOC} editable={false} placeholders prompting />
                  </span>
                </span>
                <span className="reg-tpl__name">Blank</span>
              </button>
            </li>

            {TEMPLATES.map((template) => (
              <li key={template.id}>
                <button
                  type="button"
                  className="reg-tpl"
                  onClick={() => create(template)}
                  disabled={busy}
                  title={template.note}
                >
                  <span className="reg-tpl__paper" aria-hidden="true">
                    <span className="reg-tpl__sheet">
                      <TemplateCard doc={PREVIEW_DOC} template={template} />
                    </span>
                  </span>
                  <span className="reg-tpl__name">{template.name}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>

    </main>
  );
}
