/**
 * How long ago, in the words a person would use.
 *
 * The register lists documents by when they last changed rather than by how
 * many writes they have taken: "3 hours ago" says which résumé you were
 * working on, and "173 writes" is a fact about the engine.
 */

import { describe, expect, it } from 'vitest';

import { timeAgo } from '@/lib/when';

const NOW = Date.parse('2026-09-03T12:00:00Z');
const ago = (ms: number) => new Date(NOW - ms).toISOString();

describe('timeAgo', () => {
  it('rounds the last minute to "just now"', () => {
    expect(timeAgo(ago(5_000), NOW)).toBe('just now');
    expect(timeAgo(ago(59_000), NOW)).toBe('just now');
  });

  it('counts minutes, then hours, then days', () => {
    expect(timeAgo(ago(3 * 60_000), NOW)).toBe('3 min ago');
    expect(timeAgo(ago(3 * 3_600_000), NOW)).toBe('3 hours ago');
    expect(timeAgo(ago(3 * 86_400_000), NOW)).toBe('3 days ago');
  });

  it('says the singular properly', () => {
    expect(timeAgo(ago(3_600_000), NOW)).toBe('1 hour ago');
    expect(timeAgo(ago(86_400_000), NOW)).toBe('yesterday');
  });

  it('falls back to a date past a week', () => {
    // Elapsed time stops meaning anything and the date means more.
    const old = timeAgo(ago(30 * 86_400_000), NOW);
    expect(old).not.toContain('ago');
    expect(old).toMatch(/\d/);
  });

  it('reads a bare timestamp as UTC, not local', () => {
    // The server sends UTC with no zone marker. Read as local time, a document
    // saved a minute ago showed as hours old -- or in the future -- depending
    // on which side of UTC you are.
    expect(timeAgo('2026-09-03T11:30:00', NOW)).toBe('30 min ago');
    expect(timeAgo('2026-09-03T11:30:00Z', NOW)).toBe('30 min ago');
  });

  it('never says something happened in the future', () => {
    // A clock a few seconds ahead of the server is ordinary.
    expect(timeAgo(new Date(NOW + 4_000).toISOString(), NOW)).toBe('just now');
  });

  it('says nothing when there is nothing to say', () => {
    expect(timeAgo(null)).toBe('');
    expect(timeAgo(undefined)).toBe('');
    expect(timeAgo('not a date')).toBe('');
  });
});
