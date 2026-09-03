/**
 * Issuing the sheet.
 *
 * Export used to be a link with `target="_blank"`. Three things were wrong
 * with that, and they are the reason this is a station rather than an anchor:
 *
 * 1. It ended in a blank tab. The API drives headless Chromium to the web
 *    app's print route, which takes seconds, so the new tab sat empty with no
 *    indication that anything was happening -- and if the export failed, it
 *    stayed empty. The person could not tell "working" from "broken".
 * 2. It always sent `template=ats`, which renders the content as one flowing
 *    column and ignores the canvas entirely. Anyone who had placed an image or
 *    moved a frame got a PDF without that work in it and was never told.
 * 3. It never confirmed the file existed. This fetches it, so the size below
 *    is the real file's size, not an assumption.
 *
 * The résumé is the artifact of record. The last thing that happens to it
 * should say what it produced.
 */

'use client';

import { useEffect, useRef, useState } from 'react';

import type { StudioDoc } from '@/contracts/doc';
import { Caution, Check, Cross, Sheet } from '@/ui/marks';
import { pdfUrl } from '@/lib/api';

type Template = 'ats' | 'canvas';

interface Issued {
  ok: boolean;
  message: string;
  href?: string;
  filename?: string;
}

/** A filename someone can find again, from the document's own title. */
function filenameFor(title: string): string {
  const stem =
    title
      .trim()
      .replace(/[^\w\s-]/g, '')
      .replace(/\s+/g, '-')
      .slice(0, 60) || 'resume';
  return `${stem}.pdf`;
}

function kb(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Whether the flowing template would drop work the person can see.
 *
 * A frame is content the flowing renderer draws anyway. An image, a shape or a
 * hand-placed text box exists only on the canvas, and so does every position.
 */
function placedWork(doc: StudioDoc | null): { placed: number; moved: boolean } {
  let placed = 0;
  let moved = false;
  for (const page of doc?.pages ?? []) {
    for (const element of page.elements) {
      if ('ref' in element) moved = true;
      else placed += 1;
    }
  }
  return { placed, moved: moved && (doc?.pages.length ?? 0) > 0 };
}

export function IssueStation({
  documentId,
  title,
  doc,
  onClose,
}: {
  documentId: string;
  title: string;
  doc: StudioDoc | null;
  onClose: () => void;
}) {
  const [template, setTemplate] = useState<Template>('ats');
  const [running, setRunning] = useState(false);
  const [issued, setIssued] = useState<Issued | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const objectUrl = useRef<string | null>(null);

  const { placed, moved } = placedWork(doc);
  const losing = template === 'ats' && (placed > 0 || moved);

  /**
   * Focus belongs inside a modal dialog, and goes back where it came from.
   *
   * Without this the dialog was `aria-modal` in name only: opening it left the
   * caret on the Export PDF button behind the scrim, so a keyboard user tabbed
   * through the whole studio underneath a dialog they could not reach, and on
   * close landed at the top of the document. `aria-modal` tells a screen reader
   * the rest of the page is inert; it does not make it so.
   */
  useEffect(() => {
    const returnTo = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(
        panel.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input, [tabindex]:not([tabindex="-1"])'
        ) ?? []
      );

    focusables()[0]?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;

      // Recomputed per keypress, not captured once: the Save and Open links
      // only exist after a successful issue, so a list taken at mount would
      // trap the user out of the two controls the flow exists to produce.
      const items = focusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (active === first || !panel.current?.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      returnTo?.focus?.();
    };
  }, [onClose]);

  // A blob URL is a live handle into this document. Released on unmount so a
  // session of repeated exports does not pin every PDF it ever made in memory.
  useEffect(
    () => () => {
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
    },
    []
  );

  async function issue() {
    setRunning(true);
    setIssued(null);
    try {
      const response = await fetch(pdfUrl(documentId, template));
      if (!response.ok) {
        // The API's own message names the actual failure -- the web app
        // unreachable from the API process is the common one, and a generic
        // "export failed" would send the user hunting in the wrong place.
        const detail = await response.text().catch(() => '');
        throw new Error(detail.slice(0, 400) || `The export failed (${response.status}).`);
      }

      const blob = await response.blob();
      if (!blob.size) throw new Error('The export produced an empty file.');

      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
      objectUrl.current = URL.createObjectURL(blob);

      setIssued({
        ok: true,
        message: `${kb(blob.size)}, ${template === 'ats' ? 'one flowing column' : 'as laid out'}.`,
        href: objectUrl.current,
        filename: filenameFor(title),
      });
    } catch (cause) {
      setIssued({ ok: false, message: (cause as Error).message });
    } finally {
      setRunning(false);
    }
  }

  return (
    <div
      className="scrim"
      onPointerDown={(event) => {
        // Only a press that both starts and ends on the backdrop closes it, so
        // a drag that happens to finish outside the panel does not.
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="issue"
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Export this résumé as a PDF"
      >
        <div className="issue__head">
          <Sheet size={17} />
          <h2 className="issue__title">Export</h2>
          <span className="rail__spacer" />
          <button type="button" className="link" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="issue__body">
          <p className="issue__intro">
            This renders the document with a real browser and hands you the
            file. It runs on your machine, so it takes a few seconds.
          </p>

          <div className="issue__templates" role="radiogroup" aria-label="Template">
            <button
              type="button"
              role="radio"
              aria-checked={template === 'ats'}
              className={`issue__template${template === 'ats' ? ' issue__template--on' : ''}`}
              onClick={() => setTemplate('ats')}
            >
              <span className="issue__radio">
                <Check size={10} />
              </span>
              <span>
                <span className="issue__name">For an applicant tracking system</span>
                <span className="issue__what">
                  One flowing column. Placement is ignored, which is what makes
                  the text extract cleanly for a parser.
                </span>
              </span>
            </button>

            <button
              type="button"
              role="radio"
              aria-checked={template === 'canvas'}
              className={`issue__template${template === 'canvas' ? ' issue__template--on' : ''}`}
              onClick={() => setTemplate('canvas')}
            >
              <span className="issue__radio">
                <Check size={10} />
              </span>
              <span>
                <span className="issue__name">As laid out</span>
                <span className="issue__what">
                  Exactly what is on the canvas, page for page. For sending to a
                  person rather than to a parser.
                </span>
              </span>
            </button>
          </div>

          {/* Posted before you act on it, not sprung afterwards. */}
          {losing && (
            <p className="issue__warn">
              <Caution size={13} />{' '}
              {placed > 0
                ? `${placed} placed ${placed === 1 ? 'element' : 'elements'} and every position will be left out of this file.`
                : 'Every position on the canvas will be left out of this file.'}{' '}
              The document itself is not changed.
            </p>
          )}

          {issued && (
            <p className={`issue__result${issued.ok ? '' : ' issue__result--bad'}`}>
              {issued.ok ? <Check size={13} /> : <Cross size={13} />}{' '}
              {issued.ok ? `${issued.filename} — ${issued.message}` : issued.message}
            </p>
          )}
        </div>

        <div className="issue__foot">
          <button
            type="button"
            className="ctl ctl--primary"
            onClick={issue}
            disabled={running}
          >
            {running ? 'Rendering…' : issued?.ok ? 'Export again' : 'Export PDF'}
          </button>

          {issued?.ok && issued.href && (
            <>
              <a className="ctl" href={issued.href} download={issued.filename}>
                Save
              </a>
              <a className="ctl" href={issued.href} target="_blank" rel="noreferrer">
                Open
              </a>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default IssueStation;
