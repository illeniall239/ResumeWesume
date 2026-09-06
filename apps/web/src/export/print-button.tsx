/**
 * Printing the sheet.
 *
 * Most people print a CV, and the only way to do it was to export a PDF, find
 * it, open it and print that -- or press Ctrl+P in the studio and get the
 * chrome: two rails, the conversation, the insert strip, the résumé somewhere
 * among them.
 *
 * It prints the print route, in a hidden frame. That route is exactly what the
 * PDF export renders, so the page that comes out of a printer and the file
 * that comes out of Export are the same document -- not two renderings kept in
 * step by hand. It also means the studio needs no print stylesheet of its own:
 * there is nothing on that page to hide.
 *
 * A frame rather than a new tab. A tab that opens, flashes a résumé and calls
 * `print()` reads as a popup, and if the dialog is cancelled the tab is left
 * behind for the person to close.
 */

'use client';

import { useEffect, useRef, useState } from 'react';

import { printUrl } from '@/lib/api';

/** Long enough that a slow render is not mistaken for a failure to load. */
const GIVE_UP_AFTER = 20_000;

/** How long the frame stays alive if the browser never says printing ended. */
const LET_GO_AFTER = 60_000;

export function PrintButton({
  documentId,
  disabled,
  onError,
}: {
  documentId: string;
  disabled?: boolean;
  /** Where a failure is shown: the board's notice row, with the others. */
  onError: (message: string | null) => void;
}) {
  const [running, setRunning] = useState(false);
  const frame = useRef<HTMLIFrameElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** Take the frame down and stop waiting on it. */
  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    frame.current?.remove();
    frame.current = null;
    setRunning(false);
  };

  useEffect(() => clear, []);

  function print() {
    if (running) return;
    setRunning(true);
    onError(null);

    const host = document.createElement('iframe');
    // Off-screen rather than `display: none`: a frame that is not laid out has
    // no layout to print, and Safari in particular hands back a blank page.
    host.setAttribute(
      'style',
      'position:fixed;right:0;bottom:0;width:1px;height:1px;opacity:0;border:0;'
    );
    host.setAttribute('aria-hidden', 'true');
    host.src = printUrl(documentId);
    frame.current = host;

    host.onload = () => {
      // The wait is over the moment the frame is ready: everything after this
      // is the browser's dialog, which is not ours to time out. Keeping the
      // timer running past here meant a person who left the dialog open got
      // "took too long" for a print that was working.
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;

      const view = host.contentWindow;
      if (!view) {
        onError('The résumé could not be prepared for printing.');
        clear();
        return;
      }

      // The frame outlives the call. `print()` blocks until the dialog closes
      // in most browsers and returns immediately in others, and pulling the
      // frame out from under an open dialog cancels the job -- so it is taken
      // down on `afterprint`, or on a grace period where that never fires.
      view.addEventListener('afterprint', clear, { once: true });
      view.focus();
      view.print();
      // And the button is done waiting either way, so it never sticks on
      // "Preparing…" in a browser that reports nothing back.
      setRunning(false);
      timer.current = setTimeout(clear, LET_GO_AFTER);
    };

    host.onerror = () => {
      onError('The résumé could not be prepared for printing.');
      clear();
    };

    timer.current = setTimeout(() => {
      if (!frame.current) return;
      onError('Preparing the résumé for printing took too long.');
      clear();
    }, GIVE_UP_AFTER);

    document.body.append(host);
  }

  return (
    <button
      type="button"
      className="ctl"
      onClick={print}
      disabled={disabled || running}
      title="Print this résumé"
    >
      {running ? 'Preparing…' : 'Print'}
    </button>
  );
}

export default PrintButton;
