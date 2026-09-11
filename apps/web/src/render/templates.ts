/**
 * The templates a résumé can be set in.
 *
 * Names and one-line descriptions only. What each one actually *looks* like
 * lives in `globals.css` under `.flow--<id>`, and the gallery on the home
 * screen shows a real render rather than a description of one -- the same
 * decision the import review makes when it previews with the real
 * `DocumentFlow` instead of a summary of fields.
 *
 * The ids are the generated `Template` union, so this list cannot drift into
 * offering something the server would reject.
 */

import type { Layout, Template } from '@/contracts/doc';

export interface TemplateInfo {
  id: Template;
  name: string;
  /** What it is for, in the terms of someone choosing one. */
  note: string;
  /**
   * How the page is arranged, which a template carries rather than the person
   * choosing separately.
   *
   * A template is one whole look. Splitting the arrangement out as its own
   * gallery made the home screen ask two questions to describe one thing, and
   * neither answer meant anything on its own.
   */
  layout: Layout;
  /** Shows a photograph. Purely so the gallery can say so. */
  photo?: boolean;
}

export const TEMPLATES: TemplateInfo[] = [
  {
    id: 'plain',
    name: 'Plain',
    note: 'Ruled headings, nothing decorative. Safe everywhere.',
    layout: 'stack',
  },
  {
    id: 'ruled',
    name: 'Ruled',
    note: 'Heavier rules and more air. Formal.',
    layout: 'stack',
  },
  {
    id: 'compact',
    name: 'Compact',
    note: 'Tighter set. Use it when you need a page to hold more.',
    layout: 'stack',
  },
  {
    id: 'book',
    name: 'Book',
    note: 'Serif throughout. Academia, law, writing.',
    layout: 'stack',
  },
  {
    id: 'centered',
    name: 'Centered',
    note: 'Your name as a masthead, headings across the page.',
    layout: 'stack',
  },
  {
    id: 'banner',
    name: 'Banner',
    note: 'Section headings as a solid bar. Impossible to skim past.',
    layout: 'stack',
  },
  {
    id: 'bold',
    name: 'Bold',
    note: 'A large name and no rules. Reads from across a desk.',
    layout: 'stack',
  },
  {
    id: 'quiet',
    name: 'Quiet',
    note: 'No rules at all. Space does the work.',
    layout: 'stack',
  },

  /* With a photograph.
     ------------------------------------------------------------------
     The picture sits in the header, which is a single frame, so where it
     goes is ordinary CSS like everything above. What each of these adds
     beyond that is the arrangement it implies.

     Worth knowing before choosing one: a photo is expected on a résumé in
     much of Europe, Asia and Latin America, and widely discouraged in the
     US, UK, Canada and Australia, where recruiters often screen them out.
     The notes say so rather than leaving it to be found out. */
  {
    id: 'portrait',
    name: 'Portrait',
    note: 'A headshot beside your name. Single column under it.',
    layout: 'stack',
    photo: true,
  },
  {
    id: 'profile',
    name: 'Profile',
    // Was the one sidebar template. The rail is parked -- see `layout` in
    // `autolayout.py`, which still places one -- and the note now describes
    // what `.flow--profile` actually draws: a grid header with the picture
    // beside three rows of name, and a rule under it.
    note: 'Headshot beside your name and title, ruled off above the first section.',
    layout: 'stack',
    photo: true,
  },
  {
    id: 'badge',
    name: 'Badge',
    note: 'Photo centred above your name. Formal, and unmistakably yours.',
    layout: 'stack',
    photo: true,
  },
];

export const DEFAULT_TEMPLATE: Template = 'plain';

/**
 * What to print beside a document's name to say which template it is set in,
 * or `null` when the name already says it.
 *
 * The studio's bar states the template as its own fact, which is right and is
 * what the design asks for. But documents created before that bar existed
 * carry the template in their *name* -- `Untitled — Centered`, because there
 * was nowhere else to put it -- and printing the label beside one of those
 * read "Untitled — Centered  Centered".
 *
 * So the rule is simply: never say the same thing twice. It needs no migration
 * of anyone's document names, and it stops being relevant the moment a résumé
 * is given a real one.
 */
export function templateLabel(
  title: string | null | undefined,
  template: Template | null | undefined
): string | null {
  if (!template) return null;
  const name = TEMPLATES.find((entry) => entry.id === template)?.name ?? template;
  const named = (title ?? '').trim().toLowerCase();
  return named.endsWith(name.toLowerCase()) ? null : name;
}
