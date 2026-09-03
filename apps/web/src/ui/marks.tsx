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
  children,
}: MarkProps & { children: React.ReactNode }) {
  return (
    <svg
      className={`mark${className ? ` ${className}` : ''}`}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={(STROKE * 16) / size}
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
