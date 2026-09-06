/**
 * The job this résumé is aimed at.
 *
 * A property of the document, not of a message. Tailoring is a conversation --
 * you ask, you read it back, you ask again -- and a posting carried on a single
 * turn survived exactly one exchange, after which every follow-up worked with
 * no idea what the sheet was being aimed at. So it is kept once and sent with
 * every turn from then on.
 *
 * It arrives by being pasted into the chat; see `readsAsAPosting`. There was a
 * button and a dialog here, which asked somebody to declare that the next thing
 * they did was aiming rather than editing -- a step in front of the thing they
 * were going to do anyway. What is left is the answer to "what is this aimed
 * at", and one press to stop.
 */

'use client';

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

  // Nothing at all until there is a posting: an empty line above the field
  // would be a control asking to be used, and the way in is the field itself.
  if (!documentId || !jobDescription) return null;

  return (
    <div className="aim">
      <span className="aim__label">Aimed at</span>
      {/* Named, not labelled. "Which job is this sheet aimed at" is the
          question, and a generic word answers it for nobody with more than one
          application open. */}
      <span className="aim__name" title={headline(jobDescription)}>
        {headline(jobDescription)}
      </span>
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
  );
}

export default TargetJob;
