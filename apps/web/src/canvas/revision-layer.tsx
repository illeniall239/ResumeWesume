/**
 * Revision tags, and the readings they supersede.
 *
 * The signature move of this world: a region the assistant changed is ringed
 * by a cloud and given a numbered delta, and the same number appears on the
 * schedule row that made the change. That is how a drawing office connects a
 * mark on the sheet to its entry in the revision block -- by matching numbers,
 * not by tracing a line -- and it reads at rest, where a connection that exists
 * only under the pointer does not.
 *
 * **The two readings are compared in the schedule, not on the sheet.** An
 * earlier version opened the superseded text over the document. There is no
 * placement that works: the document cannot reflow to make room, the margin is
 * a 38px page inset, and negative offsets inside a scrolling pane are
 * unreachable -- so every position hid some part of the résumé in order to
 * explain another part. Obscuring the artifact of record is the one thing an
 * annotation may not do. The mark stays here, in place; the comparison moved
 * to the row, which had the space for it.
 *
 * **Drawn as an overlay, never inside the node.** Two hard reasons, both
 * discovered the expensive way in this codebase already:
 *
 * 1. Every changed node is `contentEditable`, and `Editable`'s blur commits
 *    `textContent`. Anything rendered inside a node would therefore be read
 *    back as the user's own words and written into the résumé the first time
 *    they clicked in and out. The existing `data-placeholder` hint is drawn by
 *    CSS for exactly this reason.
 * 2. `DocumentFlow` is the component headless Chromium prints. Adding elements
 *    to it puts them in the PDF. Layout truth for the file an employer reads
 *    is not something a piece of editing chrome may touch.
 *
 * So this measures `[data-nid]` and paints beside it, the same way `overlay`
 * already paints selection handles.
 */

'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { useStudio } from '@/store/studio';

interface Placement {
  nid: string;
  top: number;
  left: number;
  right: number;
  bottom: number;
}

/** How far left of the text the tag sits, in unzoomed px. */
const TAG_GUTTER = 26;

export function RevisionLayer({
  host,
  zoom,
}: {
  /** The element the document is rendered into. Coordinates are relative to it. */
  host: React.RefObject<HTMLDivElement | null>;
  zoom: number;
}) {
  const changed = useStudio((state) => state.changed);
  const superseded = useStudio((state) => state.superseded);
  const marks = useStudio((state) => state.marks);
  const spotlight = useStudio((state) => state.spotlight);
  const doc = useStudio((state) => state.doc);

  const [places, setPlaces] = useState<Placement[]>([]);
  const frame = useRef<number | null>(null);

  /**
   * Where each marked node currently sits.
   *
   * Both rects come from inside the same zoomed container, so dividing by
   * `zoom` cancels the scale and returns coordinates in the host's own space --
   * which is what an absolutely positioned child of that host needs.
   */
  const measure = useCallback(() => {
    const root = host.current;
    if (!root) {
      setPlaces([]);
      return;
    }

    const base = root.getBoundingClientRect();
    const wanted = [...changed, ...(spotlight ? [spotlight] : [])];
    const next: Placement[] = [];

    for (const nid of new Set(wanted)) {
      const element = root.querySelector(`[data-nid="${CSS.escape(nid)}"]`);
      if (!element) continue;
      const rect = element.getBoundingClientRect();
      // A node scrolled out of its frame, or one a coverage rule left
      // unrendered, measures as nothing. Marking a zero-height point on the
      // page would put a tag against the sheet's top-left corner.
      if (!rect.height) continue;
      next.push({
        nid,
        top: (rect.top - base.top) / zoom,
        left: (rect.left - base.left) / zoom,
        right: (rect.right - base.left) / zoom,
        bottom: (rect.bottom - base.top) / zoom,
      });
    }
    setPlaces(next);
  }, [host, changed, spotlight, zoom]);

  // Before paint, so a tag never shows for one frame at the wrong place.
  useLayoutEffect(() => {
    measure();
  }, [measure, doc]);

  // The document reflows under this: a font loading, a frame growing as the
  // assistant adds a bullet, the window resizing. Coalesced to one measurement
  // per frame -- a ResizeObserver on a résumé fires in bursts.
  useEffect(() => {
    const root = host.current;
    if (!root) return;

    const schedule = () => {
      if (frame.current !== null) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = null;
        measure();
      });
    };

    const observer = new ResizeObserver(schedule);
    observer.observe(root);
    window.addEventListener('resize', schedule);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [host, measure]);

  if (!places.length) return null;

  return (
    <div className="revision-layer" aria-hidden={false}>
      {places.map((place) => {
        const was = superseded.get(place.nid);
        const mark = marks.get(place.nid);
        const lit = spotlight === place.nid;

        return (
          <div key={place.nid}>
            {lit && (
              <div
                className="revision-layer__lit"
                style={{
                  top: place.top - 2,
                  left: place.left - 3,
                  width: place.right - place.left + 6,
                  height: place.bottom - place.top + 4,
                }}
              />
            )}

            {/* The revision delta: the same number the schedule row carries.
                A drawing cross-references by matching numbers, not by tracing
                a line across the sheet, and a number is legible at rest where
                a hover-only connection is not.

                Only a region with a previous reading gets one. An inserted
                node was clouded by the same batch but has nothing underneath,
                and a tag that opens onto emptiness is worse than no tag. */}
            {changed.has(place.nid) && was && (
              <span
                className="revision-tag"
                style={{ top: place.top - 4, left: place.left - TAG_GUTTER }}
                aria-hidden="true"
              >
                {mark}
              </span>
            )}

          </div>
        );
      })}
    </div>
  );
}

export default RevisionLayer;
