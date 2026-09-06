/**
 * Telling a pasted job posting from an instruction.
 *
 * There used to be a button and a dialog for this. It asked somebody to
 * declare, before typing anything, that the next thing they did was aiming
 * rather than editing -- when what a person actually does is paste the advert
 * into the chat and say "tailor this to it". The chat is the only way in now,
 * so the paste has to be recognised for what it is.
 *
 * Recognised, not parsed. All this decides is "is this an advert or an
 * instruction", and it is deliberately hard to trip: a posting is long, it
 * arrives as a block of lines, and it talks about the role in a vocabulary an
 * instruction does not use. Getting it wrong is visible and one press to undo
 * -- the sheet says what it is aimed at, with a cross -- which is why a plain
 * rule is preferable here to anything cleverer.
 */

/**
 * Words an advert uses and an instruction does not.
 *
 * A posting addresses you and describes a company doing the looking. Note what
 * is *not* here: "experience with", "years of experience" and the like read
 * the same on a résumé, and somebody pasting their own résumé into the chat is
 * a thing that happens.
 */
const ADVERT = [
  'responsibilities',
  'requirements',
  'qualifications',
  'we are looking for',
  "we're looking for",
  'you will',
  "you'll",
  'about the role',
  'about the job',
  'about this role',
  'job description',
  'nice to have',
  'what you bring',
  'who you are',
  'join our',
  'our team',
  'we offer',
  'benefits',
  'salary',
  'compensation',
  'equal opportunity',
  'apply now',
  'full-time',
  'the ideal candidate',
  'the successful candidate',
];

/** Shorter than this is somebody talking to the assistant. */
const SHORTEST = 400;

/** Fewer lines than this is prose, however long it ran. */
const FEWEST_LINES = 4;

/** One word in common is a coincidence; two is a register. */
const ENOUGH_MARKERS = 2;

export function readsAsAPosting(text: string): boolean {
  const body = text.trim();
  if (body.length < SHORTEST) return false;

  const lines = body.split('\n').filter((line) => line.trim().length > 0);
  if (lines.length < FEWEST_LINES) return false;

  const flat = body.toLowerCase();
  let hits = 0;
  for (const marker of ADVERT) {
    if (flat.includes(marker) && ++hits >= ENOUGH_MARKERS) return true;
  }
  return false;
}

export default readsAsAPosting;
