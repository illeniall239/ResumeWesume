/**
 * The wordmark: the sticker lockup.
 *
 * `resume` set plain, `wesume` reversed out of a coral pill. RESUME and WESUME
 * are one character apart, and the whole name is the joke of that repetition —
 * so the mark is the repetition, said out loud: the second word is picked up
 * and stuck on. Nothing is drawn; it is two runs of text and a rounded box.
 *
 * Lowercase, because uppercase broke it. RESUMEWESUME is twelve letters
 * running together with the join lost; set lowercase, every letter sits at
 * x-height with no ascender and no descender, so each half is an unbroken bar
 * of text and the pill has a clean word to hold.
 *
 * Set in the app's own face rather than a display one of its own. A mark that
 * needs a family nobody else uses is a family downloaded for twelve letters,
 * and it makes the name a guest on its own page -- the bar it sits in is set
 * in the UI face, and a wordmark in something else reads as pasted on. The
 * capsule is what makes this a mark; the letters are the app talking, which is
 * why the mark simply followed when the app changed face.
 *
 * Every dimension is in `em`, so the whole lockup — the pill's padding, its
 * radius, the gap before it — scales from the single `size` prop. A mark that
 * needed a second measurement per placement would drift between the register's
 * bar and the studio's.
 *
 * The first word keeps `currentColor`, so it takes the ink of whatever it sits
 * on; the pill carries `--brand`, the same token the one filled control is
 * filled with. Sharing it is the point: the colour that names the app is the
 * colour of the button that does the thing, and every other colour on screen
 * is a state rather than an identity.
 */

export function Wordmark({
  size = 15,
  className,
}: {
  /** Type size in px. Everything else follows from it. */
  size?: number;
  className?: string;
}) {
  return (
    <span className={`wm${className ? ` ${className}` : ''}`} style={{ fontSize: size }}>
      <span className="wm__word">resume</span>
      <span className="wm__pill">wesume</span>
    </span>
  );
}

export default Wordmark;
