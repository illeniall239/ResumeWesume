/**
 * What a gallery card draws.
 *
 * A template is one whole look: how the résumé is *set*, which is CSS, and how
 * the page is *arranged*, which is geometry. Every template on the gallery
 * arranges it as a single column, so the plain renderer shows all of them
 * exactly and this is a thin wrapper that puts the template on the root.
 *
 * It drew a side rail too, for the one template that asked for one. That
 * template is a stack now and the arrangement is parked, so the split went
 * with it -- `globals.css` records what happens when a card keeps drawing
 * something the editor is not producing: "the gallery card, which renders one
 * continuous flow, advertised a layout the editor could not produce". The rail
 * had the same problem from the other end. It sorted the sections into two
 * divs and no stylesheet ever placed them side by side, so the promise in the
 * note was never on the card either way.
 *
 * The geometry for a rail is still in `autolayout`, which is where it would be
 * picked up from again.
 */

import type { StudioDoc } from '@/contracts/doc';
import DocumentFlow from '@/render/document-flow';
import type { TemplateInfo } from '@/render/templates';

export function TemplateCard({ doc, template }: { doc: StudioDoc; template: TemplateInfo }) {
  const shown: StudioDoc = { ...doc, template: template.id, layout: template.layout };

  return <DocumentFlow doc={shown} editable={false} placeholders />;
}

export default TemplateCard;
