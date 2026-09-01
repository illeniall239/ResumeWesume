/**
 * Giving jsdom a layout.
 *
 * jsdom runs no layout engine: every `getBoundingClientRect` returns zeros, so
 * anything that measures the page — the drag hook, the marquee, the reflow
 * pass — is untestable as written. That is exactly the gap that let two layout
 * bugs reach a browser earlier in this project.
 *
 * The fix is small: the canvas already writes each element's geometry into the
 * DOM as inline `left/top/width`, so a stub can read those back and return a
 * real rect. Nothing in the code under test has to know.
 */

const PT_TO_PX = 96 / 72;

/** Where the stubbed first page sits on screen. Deliberately not the origin. */
export const PAGE_ORIGIN = { x: 40, y: 24 };

/** Space between stacked sheets, as the canvas lays them out. */
export const PAGE_GAP = 16;

/** The top of the nth stacked page, in client pixels. */
export function pageTop(index: number, height: number): number {
  return PAGE_ORIGIN.y + index * (height + PAGE_GAP);
}

function pixels(value: string): number {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Make `getBoundingClientRect` report what the inline styles say.
 *
 * Elements are positioned from their stored rect, so their own styles are the
 * truth. Pages report their declared size; anything else reports a zero-origin
 * box, which is what an unpositioned node would measure anyway.
 *
 * Returns a restore function; call it in `afterEach`.
 */
export function stubLayout(): () => void {
  const original = Element.prototype.getBoundingClientRect;

  Element.prototype.getBoundingClientRect = function stubbed(this: Element): DOMRect {
    const node = this as HTMLElement;
    const style = node.style;

    // A page sits at a non-zero offset, as it does in a browser. This is not
    // cosmetic: with the origin at (0,0), page coordinates and client
    // coordinates coincide, and a bug that measures an angle between the two
    // spaces passes every test while being visibly wrong on screen. That
    // exact bug shipped once -- a quarter turn of the pointer rotated a shape
    // by 8 degrees.
    if (node.classList.contains('canvas-page')) {
      const width = pixels(style.width);
      const height = pixels(style.height);
      // Sheets stack down the canvas, so each one sits below the last. Giving
      // them all the same origin would make every page occupy the same screen
      // space, and a drag that crosses from one to the next -- which is decided
      // by which page is under the pointer -- could not be tested at all.
      const index = [...document.querySelectorAll('.canvas-page')].indexOf(node);
      const top = PAGE_ORIGIN.y + Math.max(0, index) * (height + PAGE_GAP);
      return {
        x: PAGE_ORIGIN.x,
        y: top,
        left: PAGE_ORIGIN.x,
        top,
        width,
        height,
        right: PAGE_ORIGIN.x + width,
        bottom: top + height,
        toJSON: () => ({}),
      } as DOMRect;
    }

    // Everything else reports client coordinates, which is what the browser
    // does: an element's box includes its own page's offset -- its own, not the
    // first one's, or every element would report as though it were on page one.
    const sheet = node.closest<HTMLElement>('.canvas-page');
    const origin = sheet
      ? sheet.getBoundingClientRect()
      : ({ left: PAGE_ORIGIN.x, top: PAGE_ORIGIN.y } as DOMRect);
    const x = pixels(style.left) + origin.left;
    const y = pixels(style.top) + origin.top;
    const width = pixels(style.width);
    // Frames autogrow, so a height is not always declared; fall back to a
    // plausible one so a zero never silently removes an element from a
    // calculation that filters on it.
    const height = pixels(style.height) || pixels(style.minHeight) || 20;

    return {
      x,
      y,
      left: x,
      top: y,
      width,
      height,
      right: x + width,
      bottom: y + height,
      toJSON: () => ({}),
    } as DOMRect;
  };

  return () => {
    Element.prototype.getBoundingClientRect = original;
  };
}

/** Points to the CSS pixels the canvas renders them as. */
export function pt(value: number): number {
  return value * PT_TO_PX;
}
