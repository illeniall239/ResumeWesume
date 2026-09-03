/**
 * The revision block.
 *
 * On a drawing this sits directly above the title block in the lower right of
 * the sheet, and it is part of the drawing rather than part of the
 * correspondence about it. That distinction is the whole reason this is not in
 * the assistant's sidebar: the sidebar holds the *conversation* -- what you
 * asked for, what it said back, what it is doing right now -- and this holds
 * the *document's record* of what has actually changed and what each change
 * replaced. One is a dialogue and disappears when you reload; the other
 * describes the artifact.
 *
 * Each row carries the number drawn on the sheet beside the region it changed,
 * so the two are matched by reading rather than by hovering. Opening a row
 * shows both readings in full. That comparison happens here and never over the
 * document: the page cannot reflow to make room, its margin is a 38px inset,
 * and negative offsets inside a scrolling pane cannot be reached -- so every
 * on-sheet placement hid part of the résumé in order to explain another part,
 * and obscuring the artifact of record is the one thing an annotation may not
 * do.
 */

'use client';

import { useState } from 'react';

import { readTarget } from '@/doc/read';
import { Caret } from '@/ui/marks';
import { useStudio } from '@/store/studio';

function Row({ nid, mark }: { nid: string; mark: number }) {
  const superseded = useStudio((state) => state.superseded);
  const setSpotlight = useStudio((state) => state.setSpotlight);
  const doc = useStudio((state) => state.doc);
  const [open, setOpen] = useState(false);

  const was = superseded.get(nid) ?? '';
  // `readTarget`, not `textOf`: a mark can name a field rather than a node --
  // `personal.phone` from a contact edit, `exp_7f3a2.title` from a retitled
  // job -- and looking those up as bare ids found nothing, so the row read
  // "Removed" for a change that removed nothing.
  const now = readTarget(doc, nid);
  const comparable = Boolean(was && now && was !== now);

  return (
    <div className="revrow">
      <button
        type="button"
        className="revrow__head"
        onPointerEnter={() => setSpotlight(nid)}
        onPointerLeave={() => setSpotlight(null)}
        onFocus={() => setSpotlight(nid)}
        onBlur={() => setSpotlight(null)}
        onClick={() => {
          if (comparable) {
            setOpen((value) => !value);
            return;
          }
          // Nothing to compare -- a line that was added rather than replaced.
          // Clicking still has to do something: the row's own tooltip promises
          // to show where the change landed, and an inert button that looks
          // like the expandable one beside it reads as broken.
          // The owner, not the whole target: `personal.phone` is a field
          // path, and nothing on the sheet is drawn with that as its id.
          const owner = nid.split('.')[0];
          setSpotlight(owner);
          document
            .querySelector(`[data-nid="${owner}"]`)
            ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }}
        aria-expanded={comparable ? open : undefined}
        title={comparable ? 'Show what this replaced' : 'Show where this landed'}
      >
        <span className="revrow__delta">{mark}</span>
        <span className="revrow__what">{now || 'Removed'}</span>
        {comparable && (
          <span className={`revrow__caret${open ? ' revrow__caret--open' : ''}`}>
            <Caret size={12} />
          </span>
        )}
      </button>

      {open && comparable && (
        <div className="compare">
          <div className="compare__line">
            <span className="compare__side">Was</span>
            <span className="compare__was">{was}</span>
          </div>
          <div className="compare__line">
            <span className="compare__side">Now</span>
            <span className="compare__now">{now}</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function RevisionBlock() {
  const marks = useStudio((state) => state.marks);
  const [open, setOpen] = useState(true);

  // Nothing has been issued against this document yet. An empty revision block
  // on a drawing is simply absent, not a panel saying it is empty.
  if (!marks.size) return null;

  // Newest first: the thing you just asked for is the thing you want to check.
  const rows = [...marks.entries()].sort((a, b) => b[1] - a[1]);

  return (
    <section className={`revblock${open ? ' revblock--open' : ''}`}>
      <button
        type="button"
        className="revblock__head"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="legend legend--lit">Revisions</span>
        <span className="revblock__count">{rows.length}</span>
        <span className="rail__spacer" />
        <span className={`revblock__caret${open ? ' revblock__caret--open' : ''}`}>
          <Caret size={13} />
        </span>
      </button>

      {open && (
        <div className="revblock__rows">
          {rows.map(([nid, mark]) => (
            <Row key={nid} nid={nid} mark={mark} />
          ))}
        </div>
      )}
    </section>
  );
}

export default RevisionBlock;
