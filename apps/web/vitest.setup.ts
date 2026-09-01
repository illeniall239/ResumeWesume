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
