/**
 * The job this résumé is aimed at.
 *
 * A property of the document, not of a message. Tailoring is a conversation --
 * you ask, you read it back, you ask again -- and a posting carried on a single
 * turn survived exactly one exchange, after which every follow-up worked with
 * no idea what the sheet was being aimed at. So it is pasted once, stored, and
 * sent with every turn from then on.
 *
 * Pasted or read from a PDF, and stored exactly as it arrives. No parsing: a
 * posting has no schema worth guessing at, and the model reads a job ad far
 * better than a parser does.
 */

'use client';

import { useEffect, useRef, useState } from 'react';

import { Cross } from '@/ui/marks';
import { useStudio } from '@/store/studio';

/**
 * A posting's own name for itself.
 *
 * The first line with real words in it. Job boards put the title there
 * essentially always, and when they do not this still shows the first thing a
 * person would read — which is the honest answer to "which posting is this".
 */
export function headline(text: string | null | undefined): string {
  if (!text) return '';
  for (const line of text.split('\n')) {
    const clean = line.trim();
    if (clean.length > 2) return clean.length > 60 ? `${clean.slice(0, 59)}…` : clean;
  }
  return '';
}

export function TargetJob() {
  const jobDescription = useStudio((state) => state.jobDescription);
  const documentId = useStudio((state) => state.documentId);
  const aimAt = useStudio((state) => state.aimAt);
  const aimAtPdf = useStudio((state) => state.aimAtPdf);

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // Opened afresh each time from what is stored, so a cancelled edit leaves
  // nothing behind and a posting changed in another tab is what you see.
  useEffect(() => {
    if (open) setDraft(jobDescription ?? '');
  }, [open, jobDescription]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  if (!documentId) return null;

  const name = headline(jobDescription);

  async function save(text: string) {
    setBusy(true);
    await aimAt(text);
    setBusy(false);
    setOpen(false);
  }

  async function fromPdf(file: File) {
    setBusy(true);
    await aimAtPdf(file);
    setBusy(false);
    setOpen(false);
  }

  return (
    <>
      {/* Two states, one line. Named when there is a posting, because "which
          job is this sheet aimed at" is the question, and a generic label
          answers it for nobody with more than one application open. */}
      {jobDescription ? (
        <div className="aim">
          <span className="aim__label">Aimed at</span>
          <button
            type="button"
            className="aim__name"
            onClick={() => setOpen(true)}
            title="Read or replace the posting"
          >
            {name}
          </button>
          <button
            type="button"
            className="aim__drop"
            onClick={() => void aimAt('')}
            title="Stop aiming at this job"
            aria-label="Stop aiming at this job"
          >
            <Cross size={11} />
          </button>
        </div>
      ) : (
        <button type="button" className="aim__add" onClick={() => setOpen(true)}>
          Add the job you are applying to
        </button>
      )}

      {open && (
        <div
          className="scrim"
          // Only a press on the backdrop itself. Without the target check,
          // releasing a drag-select inside the field closes the dialog and
          // loses a posting somebody has just pasted.
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <div className="settings" role="dialog" aria-modal="true" aria-label="The job">
            <header className="settings__head">
              <h2 className="settings__title">The job you are applying to</h2>
              <span className="rail__spacer" />
              <button className="link" type="button" onClick={() => setOpen(false)}>
                Close
              </button>
            </header>

            <p className="settings__intro">
              Paste the posting. It is kept with this résumé and sent with every
              instruction, so the assistant stays on target across a whole
              conversation rather than only the turn you paste it in.
            </p>

            <textarea
              className="aim__field"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Paste the job description here…"
              spellCheck={false}
              autoFocus
            />

            <input
              ref={fileInput}
              type="file"
              accept="application/pdf,.pdf"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                // Cleared so choosing the same file twice still fires.
                event.target.value = '';
                if (file) void fromPdf(file);
              }}
            />

            <footer className="settings__foot aim__foot">
              <button
                type="button"
                className="ctl ctl--small"
                onClick={() => fileInput.current?.click()}
                disabled={busy}
              >
                Read it from a PDF
              </button>
              <span className="rail__spacer" />
              <button
                type="button"
                className="ctl ctl--primary"
                onClick={() => void save(draft)}
                disabled={busy || draft.trim() === (jobDescription ?? '').trim()}
              >
                {busy ? 'Saving…' : 'Use this'}
              </button>
            </footer>
          </div>
        </div>
      )}
    </>
  );
}

export default TargetJob;
