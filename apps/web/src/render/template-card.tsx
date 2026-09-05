/**
 * What a gallery card draws.
 *
 * A template is one whole look: how the résumé is *set*, which is CSS, and how
 * the page is *arranged*, which is geometry. Most templates arrange it as a
 * single column and the plain renderer shows them exactly. The ones with a
 * side rail need the rail drawn, or the card under-sells the thing it is
 * offering -- "skills and study in a side rail" over a picture of one column.
 *
 * That failure has a precedent in the other direction, and `globals.css` still
 * records it: a two-column template previewed with a plain `DocumentFlow`,
 * "the gallery card, which renders one continuous flow, advertised a layout
 * the editor could not produce." It was removed rather than fixed. The editor
 * can produce it now -- `autolayout` places the frames and `canvas/reflow`
 * keeps each column on its own cursor -- so what is left is making the card
 * tell the truth about it.
 *
 * **Flowed, not placed.** The tempting version renders `PageCanvas` at the
 * coordinates the server writes, and it does not work: those carry *estimated*
 * heights, because the server cannot measure text. On a real document the
 * browser measures and corrects every frame on first open; a static card never
 * runs that pass, so absolutely positioned frames print on top of one another.
 * Tried, and they did. So the columns here are laid out by the browser and
 * filled with the same sections the server puts in each one -- the split and
 * the widths are the real ones, and only the vertical positions are the
 * browser's, which is the one thing about a preview that cannot mislead.
 */

import type { StudioDoc } from '@/contracts/doc';
import DocumentFlow from '@/render/document-flow';
import type { TemplateInfo } from '@/render/templates';

/**
 * Sections that go in the rail. Mirrors `_RAIL_SECTIONS` in
 * `apps/api/studio/doc/autolayout.py`, which places the real frames.
 *
 * The short, scannable ones. A rail is narrow: a list of skills or a degree
 * sits in it comfortably and a job with four bullets does not -- set at a
 * third of the width it runs to three times the lines, and the arrangement
 * stops saving any space at all.
 */
export const RAIL_SECTIONS: ReadonlySet<string> = new Set(['skills', 'education']);

const FALLBACK = ['summary', 'experience', 'education', 'projects', 'skills'];

function sectionKeys(doc: StudioDoc): string[] {
  const declared = doc.sections.filter((meta) => meta.visible);
  if (!declared.length) return FALLBACK;
  return [...declared].sort((a, b) => a.order - b.order).map((meta) => meta.key);
}

export function TemplateCard({ doc, template }: { doc: StudioDoc; template: TemplateInfo }) {
  const shown: StudioDoc = { ...doc, template: template.id, layout: template.layout };

  // A single column is what the plain renderer already draws.
  if (template.layout === 'stack') {
    return <DocumentFlow doc={shown} editable={false} placeholders />;
  }

  const keys = sectionKeys(shown);
  const rail = keys.filter((key) => RAIL_SECTIONS.has(key));
  const main = keys.filter((key) => !RAIL_SECTIONS.has(key));

  // Keyed by which column it is: the two are returned in an array below, and
  // React identifies siblings in a list by key. The keys on the `DocumentFlow`s
  // inside do not cover their wrapper.
  const column = (sections: string[], kind: 'rail' | 'main') => (
    <div key={kind} className={`preview-columns__${kind}`}>
      {sections.map((key) => (
        <DocumentFlow key={key} doc={shown} root={key} editable={false} placeholders />
      ))}
    </div>
  );

  const railFirst = template.layout === 'sidebar_left';

  return (
    <div className="preview-columns">
      {/* The header spans in every arrangement: a name is the one thing on a
          résumé that is never in a column, and `autolayout` places its frame
          full width whichever layout is chosen. */}
      <DocumentFlow doc={shown} root="personal" editable={false} placeholders />
      <div className="preview-columns__body">
        {railFirst
          ? [column(rail, 'rail'), column(main, 'main')]
          : [column(main, 'main'), column(rail, 'rail')]}
      </div>
    </div>
  );
}

export default TemplateCard;
