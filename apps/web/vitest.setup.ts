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
