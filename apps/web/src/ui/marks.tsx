/**
 * The marks.
 *
 * Every symbol on the board is drawn here, at one stroke weight, from one
 * grid. Before this the app leaned on typed characters -- a ✓ for applied, a ✕
 * for delete, a ▾ for a menu, a bare `·` for running -- which are not an icon
 * set: they are whatever glyph the user's fallback font happens to carry, at
 * whatever weight and optical size that font drew it, aligned to a text
 * baseline rather than to the control around them. On this machine `✕` and `↑`
 * come from different fonts entirely.
 *
 * These are the drawing-office equivalents of what they replace, kept
 * deliberately plain: a check, a struck cross, a query, a caret. Nothing here
 * is decorative, and nothing is drawn that does not name a state or an action.
 *
 * `currentColor` throughout, so a mark takes the state colour of the row it
 * sits in and there is never a second place to keep those in step.
 */

interface MarkProps {
  size?: number;
  className?: string;
}

/** 1.5 at 16px. Scaled with the icon so a 12px mark is not a hairline. */
const STROKE = 1.5;

function Frame({
  size = 16,
  className,
  /**
   * Side of the artwork's own coordinate box.
   *
   * Sixteen for everything drawn here. A mark taken from outside is kept on
   * the grid it was drawn on rather than rescaled by hand -- refitting a path
   * with arcs in it to a different box by eye is how an icon ends up a
   * half-pixel off its own centre. The stroke below is normalised for it, so
   * a 24-grid mark still renders at exactly the weight of a 16-grid one.
   */
  grid = 16,
  children,
}: MarkProps & { grid?: number; children: React.ReactNode }) {
  return (
    <svg
      className={`mark${className ? ` ${className}` : ''}`}
      width={size}
      height={size}
      viewBox={`0 0 ${grid} ${grid}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={(STROKE * grid) / size}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

/** Applied, accepted, read. */
export function Check(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M3 8.5 6.3 12 13 4.5" />
    </Frame>
  );
}

/** Refused, removed, could not be read. */
export function Cross(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </Frame>
  );
}

/**
 * Throwing something away.
 *
 * A bin rather than a cross, because a cross is the mark this app already uses
 * for a rejected edit and for closing a dialog -- neither of which destroys
 * anything. What deleting a résumé does is not a dismissal, and the glyph
 * should not be borrowed from one.
 *
 * Drawn on the same 16 grid as the rest: a lid with a handle over it, and a
 * body with two staves. Nothing narrower reads as a bin at 13px.
 */
export function Bin(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M2.5 4.5h11" />
      <path d="M6.5 4.5V3a.5.5 0 01.5-.5h2a.5.5 0 01.5.5v1.5" />
      <path d="M4 4.5l.6 8.2a.9.9 0 00.9.8h5a.9.9 0 00.9-.8L12 4.5" />
      <path d="M6.8 7v4M9.2 7v4" />
    </Frame>
  );
}

/** Awaiting a decision. The state the stamp block is in. */
export function Query(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M5.6 5.6a2.4 2.4 0 1 1 2.9 2.35c-.55.13-.5.6-.5 1.05" />
      <path d="M8 12.2v.01" />
    </Frame>
  );
}

/** In progress. A ring rather than a dot: it is a thing not yet closed. */
export function Running(props: MarkProps) {
  return (
    <Frame {...props}>
      <circle cx="8" cy="8" r="3.4" />
    </Frame>
  );
}

/** Waiting its turn. Open, unmarked. */
export function Pending(props: MarkProps) {
  return (
    <Frame {...props}>
      <circle cx="8" cy="8" r="3.4" strokeDasharray="1.6 2" />
    </Frame>
  );
}

/** Not applicable to this drawing. */
export function NotApplicable(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M4 8h8" />
    </Frame>
  );
}

export function Caret(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M4 6.5 8 10.5 12 6.5" />
    </Frame>
  );
}

export function Plus(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M8 3.5v9M3.5 8h9" />
    </Frame>
  );
}

export function Minus(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M3.5 8h9" />
    </Frame>
  );
}

export function ArrowUp(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M8 12.5v-9M4.5 7 8 3.5 11.5 7" />
    </Frame>
  );
}

export function ArrowDown(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M8 3.5v9M4.5 9l3.5 3.5L11.5 9" />
    </Frame>
  );
}

/** Undo. The revision arrow: back around, not back along. */
export function Undo(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M3.2 7.2h6.3a3.4 3.4 0 0 1 0 6.8H6" />
      <path d="M5.8 4.4 3 7.2 5.8 10" />
    </Frame>
  );
}

export function Redo(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M12.8 7.2H6.5a3.4 3.4 0 0 0 0 6.8H10" />
      <path d="M10.2 4.4 13 7.2 10.2 10" />
    </Frame>
  );
}

/** A caution posted on the drawing, not sprung as an interrupt. */
export function Caution(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M8 2.8 14.2 13H1.8L8 2.8Z" />
      <path d="M8 6.6v3M8 11.4v.01" />
    </Frame>
  );
}

/**
 * Settings. Drawn as a slider rather than a cog: these are choices with
 * positions -- which provider, which model -- not machinery to tinker with,
 * and a stroked cog at 13px turns to mush anyway.
 */
export function Sliders(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M2.5 4.5h11M2.5 11.5h11" />
      <path d="M6 2.8v3.4M10 9.8v3.4" />
    </Frame>
  );
}

/** The issued sheet. */
export function Sheet(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M3.5 2.2h6L12.5 5.2v8.6h-9V2.2Z" />
      <path d="M9.3 2.4v3h3.1" />
    </Frame>
  );
}

/**
 * The revision cloud, as a mark.
 *
 * Five bumps around a closed region -- the same silhouette board.css draws in
 * gradient around a changed run of text, at a size where a scalloped edge is
 * still legible.
 */
export function Cloud(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M4.4 11.6a1.8 1.8 0 0 1 .1-3.3 2 2 0 0 1 1.6-3 2.1 2.1 0 0 1 3.8-.5 1.9 1.9 0 0 1 2.8 2.2 1.9 1.9 0 0 1-.5 3.6 1.9 1.9 0 0 1-3 1.6 2 2 0 0 1-3.4-.4 1.8 1.8 0 0 1-1.4-.2Z" />
    </Frame>
  );
}

/* --- what you can put on a page -------------------------------------------
   The insert strip's marks. Each is the thing itself rather than a metaphor
   for it: a box is a box, a line is a line. The one exception is Text, where
   the thing itself is a letter -- so it is the typesetter's T-bar, which is
   what every drawing program uses and what nobody has to learn. */

/** A text block. */
export function TypeMark(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M3.2 4.4V3h9.6v1.4M8 3v10M6 13h4" />
    </Frame>
  );
}

/** A placed picture. */
export function Picture(props: MarkProps) {
  return (
    <Frame {...props}>
      <rect x="2.4" y="3.2" width="11.2" height="9.6" rx="1.2" />
      <circle cx="6" cy="6.4" r="1" />
      <path d="M13.6 10.4 10.4 7.6 4 12.8" />
    </Frame>
  );
}

/** A headshot: the picture that belongs to the résumé rather than the page. */
export function Portrait(props: MarkProps) {
  return (
    <Frame {...props}>
      <circle cx="8" cy="5.8" r="2.6" />
      <path d="M3 13.4a5 5 0 0 1 10 0" />
    </Frame>
  );
}

/** A rectangle. */
export function Box(props: MarkProps) {
  return (
    <Frame {...props}>
      <rect x="2.6" y="2.6" width="10.8" height="10.8" rx="1.2" />
    </Frame>
  );
}

/** An ellipse. */
export function Ellipse(props: MarkProps) {
  return (
    <Frame {...props}>
      <circle cx="8" cy="8" r="5.4" />
    </Frame>
  );
}

/** A rule. */
export function Line(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M2.6 8h10.8" />
    </Frame>
  );
}

/** Another sheet. */
export function PagePlus(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M3.6 2.4h5.2l3.6 3.6v7.6H3.6V2.4Z" />
      <path d="M8.6 2.6v3.2h3.4" />
      <path d="M6.2 10.4h3.6M8 8.6v3.6" />
    </Frame>
  );
}

/** Something clipped to the message. */
export function Clip(props: MarkProps) {
  return (
    <Frame {...props}>
      <path d="M12.7 7.6 7.9 12.4a3 3 0 0 1-4.3-4.3l5.2-5.2a2 2 0 0 1 2.9 2.9l-5.2 5.2a1 1 0 0 1-1.4-1.4l4.5-4.5" />
    </Frame>
  );
}

/** Looking for something. A glass, not a question mark. */
export function Search(props: MarkProps) {
  return (
    <Frame {...props}>
      <circle cx="7.2" cy="7.2" r="4.2" />
      <path d="M10.3 10.3 13.5 13.5" />
    </Frame>
  );
}

/**
 * The pen, where the agent is working.
 *
 * Supplied artwork, kept on its own 24 grid rather than refitted to the 16 the
 * rest of this file is drawn on: the outline is a single path carrying arcs
 * and bezier joins, and rescaling that by eye is how a mark ends up a half
 * pixel off its own centre. `Frame` normalises the stroke for the grid, so it
 * renders at exactly the weight of every other symbol here.
 *
 * Drawn nib-down-left, which the overlay depends on: the nib is what lands on
 * the coordinate, and the barrel trails up and to the right, clear of the
 * words underneath. The nib tip sits at (3, 21) of 24 -- `agent-cursor` and
 * `board.css` carry those fractions, so a change to this path is a change
 * there too.
 */
export function Pen(props: MarkProps) {
  return (
    <Frame {...props} grid={24}>
      <path d="M15.4998 5.49994L18.3282 8.32837M3 20.9997L3.04745 20.6675C3.21536 19.4922 3.29932 18.9045 3.49029 18.3558C3.65975 17.8689 3.89124 17.4059 4.17906 16.9783C4.50341 16.4963 4.92319 16.0765 5.76274 15.237L17.4107 3.58896C18.1918 2.80791 19.4581 2.80791 20.2392 3.58896C21.0202 4.37001 21.0202 5.63634 20.2392 6.41739L8.37744 18.2791C7.61579 19.0408 7.23497 19.4216 6.8012 19.7244C6.41618 19.9932 6.00093 20.2159 5.56398 20.3879C5.07171 20.5817 4.54375 20.6882 3.48793 20.9012L3 20.9997Z" />
    </Frame>
  );
}
