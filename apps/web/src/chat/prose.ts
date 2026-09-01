/**
 * Cleaning node ids out of what the assistant says to the user.
 *
 * The model is handed the document as an id-annotated outline, because ids are
 * how it addresses a node (see ADR-0001) — it reads lines like
 * `[blt_9x6q5] Built an end-to-end AI system…` and it needs to. What it must
 * not do is copy that format into its reply, and a model that has just read
 * forty lines of it reliably does.
 *
 * The ids cannot come out of the outline, so they come out here. And they are
 * removed rather than merely asked about: the system prompt does ask, and qwen3
 * ignored it on the very next turn, writing
 * `(**exp_7a8f5: System Developer @ GEO TV**) is **[blt_8fbc8]**`. Which is the
 * project's own position applied to its own UI — a prompt is advisory, so the
 * guarantee has to live somewhere the model does not control.
 *
 * Two details that a plain find-and-replace gets wrong, both taken from that
 * real reply. An id is often the whole content of a bold span, so deleting just
 * the id leaves `****` behind; and an id is often followed by a colon
 * introducing the text it labels, which leaves a sentence starting `: `.
 *
 * Streaming adds one more: deltas split anywhere, so a half-arrived `[blt_9x`
 * would flash on screen for a frame before the rest lands.
 */

/** The kind prefixes minted by `studio/doc/nodes.py`. */
const KIND = '(?:exp|edu|prj|blt|skl|sgp|cst|cit|sum)';
const ID = `${KIND}_[0-9a-z]{3,8}`;

/** An id that is the entire content of a bold or italic span: `**[blt_x]**`. */
const EMPHASISED = new RegExp(`([*_]{1,3})\\s*\\[?\\s*${ID}\\s*\\]?\\s*:?\\s*\\1`, 'gi');

/** `[blt_9x6q5] ` — bracketed, with any colon and space that followed. */
const BRACKETED = new RegExp(`\\[\\s*${ID}\\s*\\]\\s*:?\\s*`, 'gi');

/** A bare id, with a trailing colon when it was labelling what comes next. */
const BARE = new RegExp(`\\b${ID}\\b\\s*:?\\s*`, 'gi');

/**
 * A bracketed id still arriving, anchored to the end of the text.
 *
 * The leading `\s*` swallows the space before the fragment too, so nothing
 * jitters as the rest of the id lands. Safe because the whole accumulated
 * message is re-cleaned on every delta: once the id completes this no longer
 * matches, and the real spacing comes back.
 */
const PARTIAL =
  /\s*\[\s*(?:e|ex|exp|ed|edu|p|pr|prj|b|bl|blt|s|sk|skl|sg|sgp|su|sum|c|cs|cst|ci|cit)?(?:_[0-9a-z]{0,8})?$/i;

/** Brackets or parens left holding nothing once their id was removed. */
const EMPTY_WRAP = /\(\s*\)|\[\s*\]/g;

/**
 * Strip node ids from assistant prose.
 *
 * `streaming` also trims a trailing partial id, so a mid-arrival `[blt_9x` is
 * held back rather than shown and then retracted.
 */
export function stripNodeIds(text: string, { streaming = false } = {}): string {
  if (!text) return text;

  // Each pattern already consumes the whitespace that followed the id, so the
  // space *before* it is what joins the sentence back up. Replacing with a
  // space instead would leave a doubled gap inside emphasis markers, where no
  // later collapse can tell it from intentional spacing.
  let cleaned = text
    .replace(EMPHASISED, '')
    .replace(BRACKETED, '')
    .replace(BARE, '');

  if (streaming) cleaned = cleaned.replace(PARTIAL, '');

  return cleaned
    .replace(EMPTY_WRAP, '')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/ ([,.;:!?)\]])/g, '$1')
    .replace(/([([]) /g, '$1')
    .trimEnd();
}
