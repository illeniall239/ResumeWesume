/**
 * The wordmark. Text and nothing else.
 *
 * Lowercase, solid, tight. Uppercase is what broke it before: RESUMEWESUME is
 * twelve letters running together with the join lost. Set lowercase, every
 * letter in `resumewesume` sits at x-height — no ascender, no descender — so
 * the word is one unbroken bar of text and holds together at any size.
 *
 * The R in its tile is the favicon (`app/icon.svg`), and stays there. RESUME
 * and WESUME are one character apart, so that letter is the whole name
 * compressed to the part that changes — which is what a favicon needs and what
 * a bar with the name already spelled out on it does not.
 *
 * Drawn in `currentColor`, so it takes the ink of whatever it sits on: the
 * register's white bar, or the near-black board.
 */

export function Wordmark({
  size = 15,
  className,
}: {
  /** Type size in px. */
  size?: number;
  className?: string;
}) {
  return (
    <span className={`wm${className ? ` ${className}` : ''}`} style={{ fontSize: size }}>
      resumewesume
    </span>
  );
}

export default Wordmark;
