/**
 * How long ago something happened, in the words a person would use.
 *
 * The register lists documents by when they last changed rather than by how
 * many writes they have taken. "3 hours ago" says which résumé you were
 * working on; "173 writes" is a fact about the engine, not about you.
 *
 * Coarse on purpose. A register is scanned, not read, and the difference
 * between 41 and 43 minutes has never decided anything -- so each step is the
 * largest unit that still distinguishes one entry from another.
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '';

  // The server sends UTC without a zone marker, which `Date` reads as local
  // time -- so a document saved a minute ago showed as hours old, or in the
  // future, depending on which side of UTC you are.
  const stamp = /(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`;
  const at = Date.parse(stamp);
  if (Number.isNaN(at)) return '';

  const ago = now - at;
  // A clock a few seconds ahead of the server is ordinary, and "in 4 seconds"
  // is a worse answer than "just now".
  if (ago < MINUTE) return 'just now';
  if (ago < HOUR) {
    const minutes = Math.floor(ago / MINUTE);
    return `${minutes} min ago`;
  }
  if (ago < DAY) {
    const hours = Math.floor(ago / HOUR);
    return hours === 1 ? '1 hour ago' : `${hours} hours ago`;
  }
  if (ago < 7 * DAY) {
    const days = Math.floor(ago / DAY);
    return days === 1 ? 'yesterday' : `${days} days ago`;
  }

  // Past a week the elapsed time stops meaning anything and the date means
  // more.
  return new Date(at).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
  });
}
