/**
 * A template changes how a résumé is set, never what it says.
 *
 * That is the promise the gallery makes in as many words -- "every template
 * holds the same words in the same order, so the text an applicant tracking
 * system reads is identical whichever you pick" -- and a claim the interface
 * makes has to be enforced somewhere a stylesheet cannot quietly break it.
 *
 * The check is on the rendered DOM rather than on the CSS, because that is
 * what a parser sees: headless Chromium prints this markup, and an extractor
 * reads the text in document order out of the result.
 */

import { render, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import DocumentFlow from '@/render/document-flow';
import { PREVIEW_DOC } from '@/render/preview-doc';
import { TEMPLATES } from '@/render/templates';

afterEach(cleanup);

/** Text in document order, whitespace normalised. */
function extract(container: HTMLElement): string {
  return (container.textContent ?? '').replace(/\s+/g, ' ').trim();
}

describe('templates', () => {
  it('offers between six and nine, which is what the gallery is sized for', () => {
    expect(TEMPLATES.length).toBeGreaterThanOrEqual(6);
    expect(TEMPLATES.length).toBeLessThanOrEqual(9);
  });

  it('names every template exactly once', () => {
    const ids = TEMPLATES.map((template) => template.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  /**
   * The exact claim the gallery makes, held to the rendered markup.
   *
   * Worth knowing what this does and does not cover. It proves the document
   * order every template ships is identical, which is the thing a template
   * could plausibly break and the thing an ATS depends on. It cannot see CSS:
   * generated content is real text in a PDF, and three early drafts of these
   * templates changed the extracted text without changing this DOM at all --
   * a bullet glyph swapped for an en dash, and a centred heading that a PDF
   * extractor read out of order because it sorts runs by position. Those are
   * fixed and the rules that caused them carry comments in `globals.css`;
   * this test is the floor, not the ceiling.
   */
  it('renders identical text in every template', () => {
    const baseline = (() => {
      const { container } = render(
        <DocumentFlow doc={{ ...PREVIEW_DOC, template: 'plain' }} editable={false} />
      );
      const text = extract(container);
      cleanup();
      return text;
    })();

    expect(baseline).toContain('Alex Morgan');

    for (const template of TEMPLATES) {
      const { container } = render(
        <DocumentFlow doc={{ ...PREVIEW_DOC, template: template.id }} editable={false} />
      );
      expect(extract(container), `${template.id} changed the extracted text`).toBe(
        baseline
      );
      cleanup();
    }
  });

  it('puts the template on the root, where a canvas frame and the print route both find it', () => {
    for (const template of TEMPLATES) {
      const { container } = render(
        <DocumentFlow doc={{ ...PREVIEW_DOC, template: template.id }} editable={false} />
      );
      const root = container.querySelector('[data-print-root]');
      expect(root).not.toBeNull();
      expect(root?.className).toBe(`flow flow--${template.id}`);
      cleanup();
    }
  });

  it('falls back to plain for a document saved before templates existed', () => {
    // `template` is required by the contract because the server always sends
    // it, but a document read from an older store or a hand-written fixture
    // may not carry one, and an undefined class name is a rendering bug.
    const legacy = { ...PREVIEW_DOC } as Record<string, unknown>;
    delete legacy.template;

    const { container } = render(
      <DocumentFlow doc={legacy as unknown as typeof PREVIEW_DOC} editable={false} />
    );
    expect(container.querySelector('[data-print-root]')?.className).toBe(
      'flow flow--plain'
    );
  });
});

/**
 * A new résumé has to be something you can start typing into.
 *
 * The defect these pin, in the order a person met them: choosing a template
 * opened a blank sheet, because a document created with no content has no
 * sections and every section renders only when it has something in it. Giving
 * it a skeleton exposed the next two, both of which had been there all along
 * and were invisible while every test document already had words in it.
 */
describe('a starter résumé', () => {
  const empty = {
    ...PREVIEW_DOC,
    summary: { nid: 'sum_4k2wp', text: '', style: 'plain' as const },
    experience: [{ ...PREVIEW_DOC.experience[0], title: '', company: '', location: '', years: '' }],
    education: [{ ...PREVIEW_DOC.education[0], degree: '', institution: '', years: '' }],
    personal: { ...PREVIEW_DOC.personal, name: '', title: '' },
  };

  it('shows what belongs in a field that has nothing in it yet', () => {
    const { container } = render(
      <DocumentFlow doc={empty} editable={false} placeholders />
    );
    const hints = [...container.querySelectorAll('[data-placeholder]')].map((node) =>
      node.getAttribute('data-placeholder')
    );
    expect(hints).toContain('Your name');
    expect(hints).toContain('Job title');
    expect(hints).toContain('Degree');
    expect(hints).toContain('A sentence or two about you');
  });

  it('draws no hints without being asked, so a PDF never says "Your name"', () => {
    const { container } = render(<DocumentFlow doc={empty} editable={false} />);
    expect(container.querySelectorAll('[data-placeholder]')).toHaveLength(0);
  });

  it('separates a company from its location when both are only hints', () => {
    const { container } = render(
      <DocumentFlow doc={empty} editable={false} placeholders />
    );
    // Without this the line read "CompanyLocation".
    expect(container.querySelector('.entry__org')?.textContent).toBe(', ');
  });

  it('gives an education entry pulled onto the canvas its editing props', () => {
    // `renderRoot`'s education branch was the only one of four that did not
    // spread props, so an education entry on a canvas could not be typed into,
    // never highlighted when the assistant rewrote it, and never reported focus
    // -- which is what refuses the agent on a node the user is holding.
    const nid = empty.education[0].nid;
    const { container } = render(
      <DocumentFlow doc={empty} root={nid} editable placeholders />
    );
    const degree = container.querySelector(`[data-field="${nid}.degree"]`);
    expect(degree).not.toBeNull();
    expect(degree?.getAttribute('contenteditable')).toBe('plaintext-only');
  });
});

/**
 * The card and the document it creates are the same résumé.
 *
 * This is the promise a gallery makes by showing you something before you
 * click it, and it is only true while both come from one fixture. An earlier
 * version rendered `PREVIEW_DOC` on the card and created a document from a
 * second sample in the legacy shape, which differed in its jobs, its projects
 * and its skills -- so what you clicked was not what you got.
 */
describe('what a card promises', () => {
  it('renders the same document the studio will open', () => {
    for (const template of TEMPLATES) {
      // What the card draws.
      const card = render(
        <DocumentFlow doc={{ ...PREVIEW_DOC, template: template.id }} editable={false} />
      );
      const shown = extract(card.container);
      cleanup();

      // What `create` posts, built the same way the home screen builds it.
      const created = { ...PREVIEW_DOC, template: template.id };
      const opened = render(<DocumentFlow doc={created} editable={false} />);
      expect(extract(opened.container), `${template.id} card and document differ`).toBe(
        shown
      );
      expect(created.template).toBe(template.id);
      cleanup();
    }
  });

  it('carries real content, so a chosen template is visible immediately', () => {
    // The defect this pins: a template opened onto an empty page, because the
    // document created behind it had no sections to set.
    expect(PREVIEW_DOC.experience.length).toBeGreaterThan(0);
    expect(PREVIEW_DOC.education.length).toBeGreaterThan(0);
    expect(PREVIEW_DOC.skills.length).toBeGreaterThan(0);
    expect(PREVIEW_DOC.summary?.text).toBeTruthy();
    expect(PREVIEW_DOC.personal.name).toBeTruthy();
  });
});
