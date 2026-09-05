/**
 * The template name beside the document's name in the studio's bar.
 *
 * One rule, and it exists because of one collision: documents made before that
 * bar existed carry the template inside their *name* -- `Untitled — Centered`,
 * because there was nowhere else to put it -- so stating it again beside them
 * printed "Untitled — Centered  Centered" across the top of the sheet.
 */

import { describe, expect, it } from 'vitest';

import { templateLabel } from '@/render/templates';

describe('the template beside a document name', () => {
  it('names the template a sheet is set in', () => {
    expect(templateLabel('Untitled', 'centered')).toBe('Centered');
    expect(templateLabel('Rao Muhammad Hamza', 'badge')).toBe('Badge');
  });

  it('says nothing when the name already ends with it', () => {
    // The collision this exists for.
    expect(templateLabel('Untitled — Centered', 'centered')).toBeNull();
    expect(templateLabel('Untitled — Badge', 'badge')).toBeNull();
  });

  it('does not care about case or trailing space in the name', () => {
    expect(templateLabel('untitled — centered  ', 'centered')).toBeNull();
    expect(templateLabel('UNTITLED — CENTERED', 'centered')).toBeNull();
  });

  it('still names it when the name merely mentions it in passing', () => {
    // Only a name *ending* in the template is the machine-made one. A résumé
    // called "Centered layouts I have known" is a name somebody chose.
    expect(templateLabel('Centered layouts I have known', 'centered')).toBe('Centered');
  });

  it('does not confuse one template for another', () => {
    expect(templateLabel('Untitled — Centered', 'plain')).toBe('Plain');
  });

  it('says nothing at all when there is no template to name', () => {
    // The document has not loaded yet, which is most of the first second.
    expect(templateLabel('Untitled', null)).toBeNull();
    expect(templateLabel('Untitled', undefined)).toBeNull();
  });

  it('survives a document with no name', () => {
    expect(templateLabel(null, 'centered')).toBe('Centered');
    expect(templateLabel('', 'centered')).toBe('Centered');
  });
});
