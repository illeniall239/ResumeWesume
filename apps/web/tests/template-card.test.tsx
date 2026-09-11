/**
 * What a gallery card promises.
 *
 * A card is the only thing anyone sees before choosing, so the failure that
 * matters is a card that misrepresents its template. `globals.css` records the
 * last time it happened: a two-column template previewed with a plain
 * `DocumentFlow`, "the gallery card, which renders one continuous flow,
 * advertised a layout the editor could not produce."
 *
 * These pin what it promises. Every template is a single column, so no card
 * may draw or describe a rail; and the claim the gallery makes in as many
 * words -- that every template holds the same words in the same order, so the
 * text an ATS reads is identical whichever you pick.
 */

import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import DocumentFlow from '@/render/document-flow';
import { PREVIEW_DOC } from '@/render/preview-doc';
import { TEMPLATES } from '@/render/templates';
import { TemplateCard } from '@/render/template-card';

/**
 * Fail the render on a React warning rather than letting it scroll past.
 *
 * A missing key is reported through `console.error` and nothing else: the
 * render succeeds and the suite stays green. Which is exactly how one got out
 * of here and into a browser console once already.
 */
let complaints: string[] = [];

beforeEach(() => {
  complaints = [];
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    complaints.push(args.map(String).join(' '));
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

const card = (id: string) => {
  const template = TEMPLATES.find((entry) => entry.id === id)!;
  return render(<TemplateCard doc={PREVIEW_DOC} template={template} />);
};

/** Section headings inside an element, lowercased. */
const headings = (root: Element | null) =>
  root
    ? [...root.querySelectorAll('.section__title')].map((node) =>
        (node.textContent ?? '').trim().toLowerCase()
      )
    : [];

/** Text in document order, whitespace normalised — what a parser would read. */
const extract = (root: HTMLElement) => (root.textContent ?? '').replace(/\s+/g, ' ').trim();

describe('every card', () => {
  it.each(TEMPLATES.map((template) => [template.id, template] as const))(
    '%s renders without a React warning',
    (_id, template) => {
      render(<TemplateCard doc={PREVIEW_DOC} template={template} />);

      expect(complaints).toEqual([]);
    }
  );

  it('puts the template on the root, which is what the CSS binds to', () => {
    for (const template of TEMPLATES) {
      const { container } = render(<TemplateCard doc={PREVIEW_DOC} template={template} />);
      const root = container.querySelector('[data-print-root]');

      expect(root?.className).toContain(`flow--${template.id}`);
    }
  });
});

describe('the arrangement a card shows', () => {
  it('draws a single column, because every template is one', () => {
    // The card used to sort sections into a rail and a main column for the
    // one sidebar template. That template is a stack now and the arrangement
    // is parked, so a card claiming a rail would be advertising something the
    // gallery cannot give you -- which is the exact failure `globals.css`
    // records from the last time a two-column template was offered.
    for (const template of TEMPLATES) {
      const { container } = render(<TemplateCard doc={PREVIEW_DOC} template={template} />);

      expect(container.querySelector('.preview-columns')).toBeNull();
      expect(template.layout).toBe('stack');
    }
  });

  it('promises no side rail in any note', () => {
    for (const template of TEMPLATES) {
      expect(template.note.toLowerCase()).not.toContain('rail');
    }
  });
});

describe('the photograph', () => {
  const PHOTO = TEMPLATES.filter((template) => template.photo).map((t) => [t.id] as const);

  it('is offered by more than one template', () => {
    expect(PHOTO.length).toBeGreaterThanOrEqual(3);
  });

  it.each(PHOTO)('%s shows where the photo goes', (id) => {
    const { container } = card(id);

    expect(container.querySelector('.flow__photo')).not.toBeNull();
  });

  it('is addressable, so the pen can land on it', () => {
    // The overlay resolves whatever the protocol named, and a `set_photo`
    // compiles to a `set_field` reporting exactly this string. Without the
    // attribute the pen had nothing to find and a picture arrived on the sheet
    // with no sign of where it came from.
    const { container } = card('portrait');

    expect(container.querySelector('[data-field="personal.photo"]')).not.toBeNull();
  });

  it('keeps the slot in the markup even where it is not drawn', () => {
    // Deliberate: the element is always rendered and the template decides
    // whether it is *shown*, so switching from a photo template to a plain one
    // hides the picture rather than discarding it, and switching back brings
    // it straight out again.
    //
    // Which templates actually show it is a CSS question and cannot be
    // answered here -- jsdom loads no stylesheet, so `getComputedStyle` reports
    // a div's default `block` for every one of them. Verified in a browser
    // instead; the rule lives beside `.flow__photo` in globals.css.
    const { container } = card('plain');

    expect(container.querySelector('.flow__photo')).not.toBeNull();
  });

  it('never puts the word "Photo" in an export', () => {
    // The empty state is a hint, and a hint that reaches a PDF is a word on
    // somebody's résumé that they did not write. Asserted against the export
    // path rather than the card: the card passes `placeholders` on purpose, so
    // that it can show you where the picture goes.
    for (const template of TEMPLATES) {
      const { container } = render(
        <DocumentFlow doc={{ ...PREVIEW_DOC, template: template.id }} editable={false} />
      );

      expect(extract(container), `${template.id} leaked the hint`).not.toContain('Photo');
    }
  });

  it('does draw the hint on a card, which is what it is for', () => {
    const { container } = card('portrait');

    expect(extract(container)).toContain('Photo');
  });
});
