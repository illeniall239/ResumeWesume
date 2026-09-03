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

import type { Template } from '@/contracts/doc';

export interface TemplateInfo {
  id: Template;
  name: string;
  /** What it is for, in the terms of someone choosing one. */
  note: string;
}

export const TEMPLATES: TemplateInfo[] = [
  {
    id: 'plain',
    name: 'Plain',
    note: 'Ruled headings, nothing decorative. Safe everywhere.',
  },
  {
    id: 'ruled',
    name: 'Ruled',
    note: 'Heavier rules and more air. Formal.',
  },
  {
    id: 'compact',
    name: 'Compact',
    note: 'Tighter set. Use it when you need a page to hold more.',
  },
  {
    id: 'book',
    name: 'Book',
    note: 'Serif throughout. Academia, law, writing.',
  },
  {
    id: 'centered',
    name: 'Centered',
    note: 'Your name as a masthead, headings across the page.',
  },
  {
    id: 'banner',
    name: 'Banner',
    note: 'Section headings as a solid bar. Impossible to skim past.',
  },
  {
    id: 'bold',
    name: 'Bold',
    note: 'A large name and no rules. Reads from across a desk.',
  },
  {
    id: 'quiet',
    name: 'Quiet',
    note: 'No rules at all. Space does the work.',
  },
];

export const DEFAULT_TEMPLATE: Template = 'plain';
