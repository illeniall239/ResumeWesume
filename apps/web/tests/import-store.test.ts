/**
 * Applying import events to state.
 *
 * `applyImportEvent` is a pure function precisely so this file can exist: there
 * is no fetch mocking anywhere in this codebase, so the wire protocol would
 * otherwise be tested only through a running server, which is to say not at
 * all. Events here are written by hand, exactly as the backend emits them.
 */

import { describe, expect, it } from 'vitest';

import { applyImportEvent, type ImportData } from '@/store/import';
import type { StreamEvent } from '@/stream/ndjson';

function event(type: string, fields: Record<string, unknown> = {}): StreamEvent {
  return { v: 1, seq: 1, ts: '', turn_id: 'imp_1', type, ...fields };
}

function blank(): ImportData {
  return {
    status: 'uploading',
    filename: '',
    error: null,
    warnings: [],
    columns: 1,
    pages: 0,
    sections: [],
    title: '',
    resumeData: null,
    doc: null,
    sourceText: '',
    parsed: 0,
    failed: 0,
  };
}

/** Fold a sequence of events, the way the store does. */
function run(events: StreamEvent[], from: ImportData = blank()): ImportData {
  return events.reduce(
    (state, next) => ({ ...state, ...applyImportEvent(state, next) }),
    from
  );
}

const FOUND = [
  event('section_found', { key: 'contact', chars: 90, order: 0, needs_model: false }),
  event('section_found', { key: 'experience', chars: 700, order: 1, needs_model: true }),
  event('section_found', { key: 'skills', chars: 60, order: 2, needs_model: false }),
];

describe('applyImportEvent', () => {
  it('moves to parsing and records what the file was', () => {
    const state = run([
      event('import_started', {
        filename: 'cv.pdf',
        pages: 2,
        columns: 2,
        warnings: ['Only the first 10 pages were read.'],
      }),
    ]);
    expect(state.status).toBe('parsing');
    expect(state.filename).toBe('cv.pdf');
    expect(state.pages).toBe(2);
    expect(state.columns).toBe(2);
    expect(state.warnings).toHaveLength(1);
  });

  it('builds the whole checklist before anything is parsed', () => {
    const state = run(FOUND);
    expect(state.sections.map((section) => section.key)).toEqual([
      'contact',
      'experience',
      'skills',
    ]);
    expect(state.sections.every((section) => section.status === 'pending')).toBe(true);
  });

  it('marks which sections need a model, so the UI can show the rest resolving at once', () => {
    const state = run(FOUND);
    expect(state.sections.map((section) => section.needsModel)).toEqual([
      false,
      true,
      false,
    ]);
  });

  it('walks a section from pending through running to parsed', () => {
    const state = run([
      ...FOUND,
      event('section_started', { key: 'experience' }),
      event('section_parsed', {
        key: 'experience',
        data: { entries: [{ company: 'Northwind' }] },
        source_text: 'EXPERIENCE\nNorthwind',
        ms: 21000,
      }),
    ]);
    const experience = state.sections.find((section) => section.key === 'experience');
    expect(experience?.status).toBe('parsed');
    expect(experience?.ms).toBe(21000);
    expect(experience?.sourceText).toContain('Northwind');
  });

  it('marks one section failed and leaves the others untouched', () => {
    const state = run([
      ...FOUND,
      event('section_parsed', { key: 'contact', data: { name: 'Alex' } }),
      event('section_failed', {
        key: 'experience',
        code: 'timeout',
        message: 'This section took too long to read and was skipped.',
        source_text: 'EXPERIENCE\nNorthwind',
      }),
    ]);
    const byKey = Object.fromEntries(
      state.sections.map((section) => [section.key, section])
    );
    expect(byKey.experience.status).toBe('failed');
    expect(byKey.experience.code).toBe('timeout');
    expect(byKey.contact.status).toBe('parsed');
    expect(byKey.skills.status).toBe('pending');
  });

  it('keeps the source text of a failed section, so nothing is lost silently', () => {
    const state = run([
      ...FOUND,
      event('section_failed', {
        key: 'experience',
        code: 'no_json',
        source_text: 'EXPERIENCE\nNorthwind Systems',
      }),
    ]);
    expect(
      state.sections.find((section) => section.key === 'experience')?.sourceText
    ).toContain('Northwind Systems');
  });

  it('tells two unclassified sections apart by their headings', () => {
    // Both arrive as `other`; only the heading distinguishes them, so matching
    // on key alone would show one of them twice and lose the other.
    const state = run([
      event('section_found', { key: 'other', heading: 'VOLUNTEERING', order: 3 }),
      event('section_found', { key: 'other', heading: 'PUBLICATIONS', order: 4 }),
      event('section_skipped', { key: 'other', heading: 'PUBLICATIONS', source_text: 'A paper' }),
    ]);
    const byHeading = Object.fromEntries(
      state.sections.map((section) => [section.heading, section])
    );
    expect(state.sections).toHaveLength(2);
    expect(byHeading.PUBLICATIONS.status).toBe('skipped');
    expect(byHeading.VOLUNTEERING.status).toBe('pending');
  });

  it('lands in review with everything the confirm step needs', () => {
    const state = run([
      ...FOUND,
      event('import_ready', {
        title: 'Alex Morgan',
        resume_data: { personalInfo: { name: 'Alex Morgan' } },
        doc: { personal: { name: 'Alex Morgan' } },
        source_text: 'ALEX MORGAN',
        parsed: 2,
        failed: 1,
      }),
    ]);
    expect(state.status).toBe('review');
    expect(state.title).toBe('Alex Morgan');
    expect(state.resumeData).toEqual({ personalInfo: { name: 'Alex Morgan' } });
    expect(state.doc).not.toBeNull();
    expect(state.failed).toBe(1);
  });

  it('surfaces a fatal error rather than sitting on a spinner', () => {
    const state = run([
      event('error', {
        code: 'no_text_layer',
        message: 'This PDF has no text layer.',
        fatal: true,
      }),
    ]);
    expect(state.status).toBe('error');
    expect(state.error).toBe('This PDF has no text layer.');
  });

  it('ignores an update for a section it never saw', () => {
    // A reattached stream can begin part-way through. Dropping the update is
    // better than throwing inside a fetch loop with nowhere to report it.
    const state = run([event('section_parsed', { key: 'experience', data: {} })]);
    expect(state.sections).toEqual([]);
  });

  it('ignores an event type it does not know', () => {
    const before = run(FOUND);
    const after = { ...before, ...applyImportEvent(before, event('heartbeat')) };
    expect(after.sections).toEqual(before.sections);
    expect(after.status).toBe(before.status);
  });

  it('does not duplicate a section if section_found arrives twice', () => {
    const state = run([...FOUND, ...FOUND]);
    expect(state.sections).toHaveLength(3);
  });
});
