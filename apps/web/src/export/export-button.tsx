/**
 * Issuing the sheet: one press, one file.
 *
 * This was a dialog. It asked which of two templates to render -- a flowing
 * single column "for an applicant tracking system", or the canvas as laid out
 * -- then made you press Export, then made you press Save. Three decisions and
 * two presses to get the thing you had already asked for by pressing a button
 * called Export PDF.
 *
 * The choice is gone with it. A résumé in this app is a canvas: you place
 * things on it and you can see where they are, and the flowing template threw
 * every one of those positions away. It was also the *default*, so the file
 * most people got was the one that did not look like their document -- the
 * dialog carried a warning about that, which is a fair sign the option should
 * not have existed.
 *
 * What the dialog was right about is kept. The API drives headless Chromium to
 * the print route, which takes seconds, so the button says it is working; and
 * the file is fetched rather than linked, so a failure is reported instead of
 * ending in a blank tab.
 */

'use client';

import { useEffect, useRef, useState } from 'react';

import { pdfUrl } from '@/lib/api';
import { useStudio } from '@/store/studio';

/** A filename someone can find again, from the document's own title. */
export function filenameFor(title: string): string {
  const stem =
    title
      .trim()
      .replace(/[^\w\s-]/g, '')
      .replace(/\s+/g, '-')
      .slice(0, 60) || 'resume';
  return `${stem}.pdf`;
}

export function ExportButton({
  documentId,
  title,
  disabled,
  onError,
}: {
  documentId: string;
  title: string;
  disabled?: boolean;
  /** Where a failure is shown: the board's notice row, with the others. */
  onError: (message: string | null) => void;
}) {
  const [state, setState] = useState<'idle' | 'running' | 'done'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    []
  );

  async function issue() {
    setState('running');
    onError(null);
    try {
      // The PDF is rendered from the server's saved copy, so a manual edit
      // still sitting in a focused field -- or mid-save -- would be missing
      // from the file. Commit whatever is focused, then wait for the save to
      // land before asking for the render.
      if (typeof document !== 'undefined') {
        (document.activeElement as HTMLElement | null)?.blur?.();
      }
      await useStudio.getState().settle();
      const response = await fetch(pdfUrl(documentId));
      if (!response.ok) {
        // The API's own message names the actual failure -- the web app
        // unreachable from the API process is the common one, and a generic
        // "export failed" would send the user hunting in the wrong place.
        const detail = await response.text().catch(() => '');
        throw new Error(detail.slice(0, 400) || `The export failed (${response.status}).`);
      }

      const blob = await response.blob();
      if (!blob.size) throw new Error('The export produced an empty file.');

      // A blob URL is a live handle into this document, so it is released as
      // soon as the download has taken it. Without that, a session of repeated
      // exports pins every PDF it ever made in memory.
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = filenameFor(title);
      link.click();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);

      // Said, then unsaid. The render takes seconds and the browser's own
      // download shelf is the only other sign it worked -- and that is easy to
      // miss on a second export, where nothing else on screen changes.
      setState('done');
      timer.current = setTimeout(() => setState('idle'), 2_500);
    } catch (cause) {
      setState('idle');
      onError((cause as Error).message);
    }
  }

  return (
    <button
      type="button"
      className="ctl ctl--primary"
      onClick={() => void issue()}
      disabled={disabled || state === 'running'}
      title="Export a PDF"
    >
      {state === 'running' ? 'Rendering…' : state === 'done' ? 'Saved' : 'Export PDF'}
    </button>
  );
}

export default ExportButton;
