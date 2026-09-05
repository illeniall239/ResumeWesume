import '@testing-library/jest-dom/vitest';

/**
 * jsdom does not implement pointer capture.
 *
 * Every drag calls `setPointerCapture` on the element under the pointer, which
 * is what keeps the gesture alive when the cursor leaves that element — the
 * normal case, since a drag by definition moves away from where it started.
 * Without these stubs every interaction test throws before its first assertion,
 * and the whole DOM-touching layer stays untested.
 */
if (typeof Element !== 'undefined' && !Element.prototype.setPointerCapture) {
  Element.prototype.setPointerCapture = function setPointerCapture() {};
  Element.prototype.releasePointerCapture = function releasePointerCapture() {};
  Element.prototype.hasPointerCapture = function hasPointerCapture() {
    return false;
  };
}

/**
 * jsdom implements neither hit-testing call.
 *
 * `caretAt` (canvas/page-canvas.tsx) runs both a frame after a double-click, to
 * put the caret where the user actually clicked rather than at the start of the
 * paragraph. Being deferred is what makes their absence awkward: the throw
 * lands in a `requestAnimationFrame` callback long after the assertions have
 * passed, so vitest reports a run-level unhandled error on a suite that is
 * green, and the error names a file the failing test never mentions.
 *
 * Returning null is the honest stub, not a shortcut. It is exactly what a real
 * browser returns for a point over nothing, and `caretAt` already has to handle
 * that -- `caretRangeFromPoint` is absent in Firefox, so focusing the line is
 * the documented second best.
 */
if (typeof document !== 'undefined' && !document.elementFromPoint) {
  document.elementFromPoint = () => null;
}

/**
 * jsdom has no `scrollIntoView` either, and it has no layout to scroll.
 *
 * The schedule calls it on every message change to keep the newest entry in
 * view. A no-op is the honest stub: there is no viewport here, so "already in
 * view" is true by construction, and the assertions are about what rendered
 * rather than about where it sits.
 */
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

/**
 * jsdom has no PointerEvent at all.
 *
 * Testing Library resolves an event class as `window[EventType] || window.Event`,
 * so every `fireEvent.pointerDown` falls back to a plain `Event` -- which has
 * no `clientX`. The failure is silent: nothing throws, every drag delta is
 * simply `NaN` and the gesture computes nothing.
 *
 * `MouseEvent` is implemented and carries coordinates, so defining
 * `PointerEvent` on top of it is enough to make pointer gestures testable.
 */
if (typeof window !== 'undefined' && typeof window.PointerEvent === 'undefined') {
  class PointerEventPolyfill extends window.MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    readonly isPrimary: boolean;

    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
      this.pointerType = init.pointerType ?? 'mouse';
      this.isPrimary = init.isPrimary ?? true;
    }
  }
  window.PointerEvent = PointerEventPolyfill as unknown as typeof window.PointerEvent;
  globalThis.PointerEvent = window.PointerEvent;
}

/**
 * jsdom has no `ResizeObserver`.
 *
 * Both overlays -- the revision layer and the pen -- observe the sheet so a
 * mark stays on its node while the document reflows under it: a frame growing
 * as the agent adds a bullet, a font finishing loading, the window resizing.
 *
 * A stub that never fires is the honest one here. jsdom performs no layout, so
 * nothing can ever resize; the callback firing would be the lie. Tests that
 * care about placement measure explicitly instead.
 */
if (typeof globalThis !== 'undefined' && typeof globalThis.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

/**
 * jsdom implements `Range` but not `Range.prototype.getClientRects`.
 *
 * The pen measures the *text* inside a node rather than the node's box, because
 * those differ by the whole empty remainder of the column -- so it ranges over
 * the contents and takes the last line box. jsdom has no layout to range over.
 *
 * An empty list is the honest stub, and it is a case the caller already has to
 * handle: a node with no rendered text returns nothing in a real browser too,
 * and the pen falls back to the element's own box.
 */
if (typeof Range !== 'undefined' && !Range.prototype.getClientRects) {
  Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
  Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
}
