/**
 * The pen, while the agent is working.
 *
 * A drafting machine's head travelling the sheet: it comes down when the
 * instruction is given, moves to a coordinate, seats, puts ink down, and moves
 * on. That is the metaphor because it is already this board's world -- the
 * pointer on a drawing is not an arrow, it is the carriage that draws.
 *
 * **Every position it takes is one the server reported.** `tool_args` names a
 * validated target before the edit is attempted, and `drafting` names the node
 * whose text is arriving, so the pen reaches a node *ahead* of the change and
 * rests there while the words land under it. It never tours sections to imply
 * it is reading them: a pointer drifting over a region the agent never touched
 * would be a fabricated claim about where the work happened, on a sheet whose
 * entire premise is that every mark on it is evidence.
 *
 * Waiting is the one state with no target, and it is drawn as waiting -- idling
 * in the margin, pen up -- because that is exactly what is true between the
 * instruction and the model's first word.
 *
 * **Drawn as an overlay, never inside the node**, for the two reasons
 * `revision-layer` documents at length and paid for once already: every
 * candidate node is `contentEditable` and would commit anything placed inside
 * it as the user's own words, and `DocumentFlow` is the component headless
 * Chromium prints.
 *
 * Hue is not used. On this board a colour is a state -- red proposes, green
 * accepts -- and a position is neither. The pen is drawn in the *paper's* inks
 * rather than the board's, because it sits on the résumé and nowhere else; in
 * board ink it was white on white, and invisible.
 */

'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { useStudio } from '@/store/studio';
import { Pen } from '@/ui/marks';

/** Where the pen is, in the host's unzoomed coordinates. */
interface Point {
  x: number;
  y: number;
  /** Height of the line it is resting on, so the upright scales with the text. */
  line: number;
}

/** How far into the margin the lifted pen sits, in unzoomed px. */
const READ_GUTTER = 22;

/**
 * Drawn size of the pen mark, in unzoomed px.
 *
 * Fixed rather than scaled to the line: a résumé sets body text and a section
 * heading only a few points apart, and a pen that grew and shrank between them
 * would read as two different pens. The nib is seated on the line instead --
 * see `--pen-line` in board.css.
 */
const PEN_SIZE = 24;

/**
 * How far past the last character the writing pen sits, in unzoomed px.
 *
 * The head is drawn around the upright, so seating it exactly on the text edge
 * put half the reticle over the final glyph. Nothing here may cover the résumé
 * -- it is the artifact of record, and a piece of editing chrome does not get
 * to hide part of it in order to point at it.
 */
const PEN_CLEARANCE = 9;

/**
 * Travel time, from the distance covered.
 *
 * A hand does not cross a page in the same time it moves to the next word, and
 * a single duration for both is what made this read as teleporting: every move
 * took 340ms, so a short hop crawled and a long one snapped. Roughly Fitts --
 * time grows with distance but far less than linearly, so a long reach is
 * quicker per pixel than a short correction, which is what a real arm does.
 */
function travelMs(from: Point | null, to: Point): number {
  if (!from) return 420;
  const distance = Math.hypot(to.x - from.x, to.y - from.y);
  return Math.min(240 + Math.sqrt(distance) * 42, 1050);
}

/**
 * Resolve whatever the protocol named to something on screen.
 *
 * The three forms are the three the wire uses, tried most specific first: a
 * field path addresses one span, a node id addresses one line, and a section
 * key addresses a region a read named. Anything unresolved returns null and
 * the pen holds its last position rather than jumping to the corner.
 */
function locate(root: HTMLElement, target: string): Element | null {
  const escaped = CSS.escape(target);
  return (
    root.querySelector(`[data-field="${escaped}"]`) ??
    root.querySelector(`[data-nid="${escaped}"]`) ??
    root.querySelector(`[data-section="${escaped}"]`) ??
    // A whole sheet. The tools that place a box, an image or a shape name the
    // page they land on and nothing finer -- the thing itself does not exist
    // yet -- so the pen goes to the page and the `touched` that follows brings
    // it the rest of the way.
    root.querySelector(`[data-page="${escaped}"]`) ??
    // `nid.field` where the field is not separately rendered: fall back to the
    // entry that holds it, which is where the change will show up anyway.
    (target.includes('.')
      ? root.querySelector(`[data-nid="${CSS.escape(target.split('.')[0])}"]`)
      : null)
  );
}

/**
 * Where the pen waits while the model is still thinking.
 *
 * Beside the node the user last had the caret in, so it picks up where they
 * left off, and otherwise beside the first line on the sheet. In the margin
 * either way: waiting is not pointing, and parking it on a word would claim
 * that word is about to change.
 */
function parkAt(root: HTMLElement, focused: string | null): Element | null {
  return (
    (focused ? root.querySelector(`[data-nid="${CSS.escape(focused)}"]`) : null) ??
    root.querySelector('[data-nid]')
  );
}

/**
 * The end of the text inside an element -- not the end of the element.
 *
 * These differ by the whole empty remainder of the column, which is most of
 * the line for a short bullet. Measuring the element put the pen out in the
 * margin, pointing at nothing, while the words it was supposedly writing
 * appeared several centimetres to its left.
 *
 * A range over the contents returns one rect per *line box of text*, so the
 * last of them ends exactly where the last character does, and it re-measures
 * correctly as the draft grows and rewraps.
 */
function textRects(element: Element): DOMRect[] {
  const range = document.createRange();
  range.selectNodeContents(element);
  const rects = Array.from(range.getClientRects()).filter((rect) => rect.height);
  range.detach();
  return rects;
}

/**
 * The point the pen should rest on.
 *
 * For a write, the end of the last line of text -- where a pen actually is
 * mid-word, and what makes it trail the words as they stream in rather than
 * sitting inertly beside them.
 *
 * For a read or a wait, just outside the left edge, because neither has an
 * insertion point and putting the pen after the text would imply one.
 */
function penPoint(
  element: Element,
  base: DOMRect,
  zoom: number,
  kind: 'waiting' | 'reading' | 'writing'
): Point | null {
  const rects = textRects(element);
  const writing = kind === 'writing';
  const box = writing && rects.length ? rects[rects.length - 1] : (rects[0] ?? element.getBoundingClientRect());
  // A node scrolled out of its frame, or one a coverage rule left unrendered,
  // measures as nothing -- and a zero-height point puts the pen on the sheet's
  // top-left corner, which reads as a bug rather than as a position.
  if (!box.height) return null;

  const x = writing ? box.right + PEN_CLEARANCE * zoom : box.left - READ_GUTTER * zoom;
  return {
    x: (x - base.left) / zoom,
    y: (box.top - base.top) / zoom,
    line: box.height / zoom,
  };
}

export function AgentCursor({
  host,
  zoom,
}: {
  /** The element the document is rendered into. Coordinates are relative to it. */
  host: React.RefObject<HTMLDivElement | null>;
  zoom: number;
}) {
  const attention = useStudio((state) => state.attention);
  const focused = useStudio((state) => state.focused);
  const doc = useStudio((state) => state.doc);
  const drafts = useStudio((state) => state.drafts);

  const [point, setPoint] = useState<Point | null>(null);
  const [travel, setTravel] = useState(420);
  /**
   * Where it came from, for the pen-up leader.
   *
   * A plotter's travel between two draws puts no ink on the paper, and a
   * toolpath preview draws that move dashed to say so. The leader is the same
   * statement: this is the route, nothing was written along it.
   */
  const [from, setFrom] = useState<Point | null>(null);
  const frame = useRef<number | null>(null);
  const last = useRef<Point | null>(null);

  const measure = useCallback(() => {
    const root = host.current;
    if (!root || !attention) {
      setPoint(null);
      return;
    }

    const element = attention.target
      ? locate(root, attention.target)
      : parkAt(root, focused);
    if (!element) return;

    const next = penPoint(element, root.getBoundingClientRect(), zoom, attention.kind);
    if (!next) return;

    // Only a real move gets a leader and a travel time. Re-measuring the same
    // node as its text grows is the pen tracking a word, not travelling to a
    // new one, and animating that would make it lag its own writing.
    const previous = last.current;
    const moved = previous && Math.hypot(next.x - previous.x, next.y - previous.y) > 44;
    if (moved) {
      setFrom(previous);
      setTravel(travelMs(previous, next));
    } else if (!previous) {
      setTravel(0);
    }
    last.current = next;
    setPoint(next);
  }, [host, attention, focused, zoom]);

  // Before paint, so the pen never shows for one frame at the wrong place.
  useLayoutEffect(() => {
    measure();
  }, [measure, doc, drafts]);

  // The document reflows under this: a frame growing as the agent adds a
  // bullet, a font loading, the window resizing. Coalesced to one measurement
  // per frame, the way the revision layer already does -- a ResizeObserver on
  // a résumé fires in bursts.
  useEffect(() => {
    const root = host.current;
    if (!root) return;

    const schedule = () => {
      if (frame.current !== null) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = null;
        measure();
      });
    };

    const observer = new ResizeObserver(schedule);
    observer.observe(root);
    window.addEventListener('resize', schedule);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [host, measure]);

  /**
   * Re-measure once the document's faces have loaded.
   *
   * The pen is positioned from the *width of the text*, so a fallback face
   * swapping for the real one moves the very thing it is pointing at -- and
   * the host does not necessarily change size when it happens, so the
   * ResizeObserver above never fires. Caught in a harness, where the pen
   * settled forty pixels short of the words every time.
   */
  useEffect(() => {
    if (!document.fonts) return;
    let live = true;
    void document.fonts.ready.then(() => {
      if (live) measure();
    });
    return () => {
      live = false;
    };
  }, [measure]);

  // The pen leaving the sheet also ends the leader, and forgets where it was:
  // the next turn's first move is an arrival, not a continuation of the last.
  useEffect(() => {
    if (!attention) {
      setFrom(null);
      last.current = null;
    }
  }, [attention]);

  // The leader is a statement about one move and expires with it.
  useEffect(() => {
    if (!from) return;
    const timer = window.setTimeout(() => setFrom(null), travel + 260);
    return () => window.clearTimeout(timer);
  }, [from, travel]);

  if (!point || !attention) return null;

  const kind = attention.kind;

  return (
    <div className="pen-layer" aria-hidden="true">
      {/* The pen-up move. Dashed, because no ink went down along it, and bowed
          to match the path the pen actually takes -- a straight leader under a
          curved travel is two different claims about the same move. */}
      {from && (
        <svg className="pen-leader" width="100%" height="100%">
          <path
            d={`M ${from.x} ${from.y + from.line / 2}
                Q ${from.x + (point.x - from.x) * 0.82} ${from.y + from.line / 2}
                  ${point.x} ${point.y + point.line / 2}`}
            style={{ animationDuration: `${travel + 260}ms` }}
          />
        </svg>
      )}

      {/*
        Two nested elements carrying one move.
        ------------------------------------------------------------------
        The arm carries x and the pen carries y, on deliberately different
        durations and curves. A single `translate(x, y)` transition can only
        interpolate in a straight line, which is what made every move read as a
        machine sliding between two coordinates; splitting the axes lets them
        arrive at different moments, and the composite path bows the way a hand
        does. It costs one extra div and no script.
      */}
      <div
        className="pen-arm"
        style={{
          transform: `translate3d(${point.x}px, 0, 0)`,
          transitionDuration: `${travel}ms`,
        }}
      >
        <div
          className={`pen pen--${kind}`}
          style={{
            transform: `translate3d(0, ${point.y}px, 0)`,
            // The vertical leg lands a little after the horizontal, which is
            // what puts the curve in the path.
            transitionDuration: `${Math.round(travel * 1.24)}ms`,
            // The upright is the height of the line it is sitting on, so the
            // pen reads as being *in* the text rather than floating near it.
            ['--pen-line' as string]: `${point.line}px`,
            // The nib offsets in board.css are fractions of this.
            ['--pen-size' as string]: `${PEN_SIZE}px`,
          }}
        >
          {/* Never perfectly still. A held pen drifts, and the two axes are
              given periods that do not divide into one another so the wander
              never repeats a visible loop.

              `Pen` is the project's own mark, on the same 16-grid and stroke
              as every other symbol on the board -- not a shape assembled here.
              An earlier version built it from four CSS boxes and a circle,
              which drew a surveyor's reticle rather than a pen. */}
          <span className="pen__hand">
            <Pen size={PEN_SIZE} className="pen__mark" />
          </span>
        </div>
      </div>
    </div>
  );
}

export default AgentCursor;
