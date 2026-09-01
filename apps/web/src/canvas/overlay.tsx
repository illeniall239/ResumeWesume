/**
 * Selection handles and alignment guides, drawn over the page.
 *
 * One absolutely-positioned layer with `pointer-events: none`, re-enabled only
 * on the handles themselves. That is what lets a click pass straight through to
 * the `contentEditable` text underneath: an overlay that swallowed clicks would
 * make every bullet uneditable, which is most of what the app is for.
 */

'use client';

import type { Rect } from '@/contracts/doc';

import { HANDLES, bounds, type Handle } from './geometry';
import { useSelection } from './selection';
import { ptToPx } from './units';

const HANDLE_POSITION: Record<Handle, { left: string; top: string; cursor: string }> = {
  nw: { left: '0%', top: '0%', cursor: 'nwse-resize' },
  n: { left: '50%', top: '0%', cursor: 'ns-resize' },
  ne: { left: '100%', top: '0%', cursor: 'nesw-resize' },
  e: { left: '100%', top: '50%', cursor: 'ew-resize' },
  se: { left: '100%', top: '100%', cursor: 'nwse-resize' },
  s: { left: '50%', top: '100%', cursor: 'ns-resize' },
  sw: { left: '0%', top: '100%', cursor: 'nesw-resize' },
  w: { left: '0%', top: '50%', cursor: 'ew-resize' },
};

export function SelectionOverlay({
  rects,
  rotatable = false,
  onHandleDown,
  onRotateDown,
}: {
  rects: Rect[];
  /** True when everything selected may be turned. */
  rotatable?: boolean;
  onHandleDown: (event: React.PointerEvent, handle: Handle) => void;
  onRotateDown: (event: React.PointerEvent) => void;
}) {
  // Subscribed here rather than passed down. A drag deliberately never
  // re-renders React -- it paints transforms straight onto the moving nodes --
  // so a guide handed in as a prop from a parent that is not re-rendering
  // never arrives. This component is the one thing that must update mid-
  // gesture, and it is cheap: a handful of absolutely-positioned divs.
  const guides = useSelection((state) => state.guides);
  const box = bounds(rects);

  return (
    <div className="overlay" aria-hidden>
      {guides.map((guide, index) => (
        <div
          key={`${guide.axis}-${guide.at}-${index}`}
          className={`guide guide--${guide.axis}`}
          style={
            guide.axis === 'x'
              ? {
                  left: ptToPx(guide.at),
                  top: ptToPx(guide.from),
                  height: ptToPx(guide.to - guide.from),
                }
              : {
                  top: ptToPx(guide.at),
                  left: ptToPx(guide.from),
                  width: ptToPx(guide.to - guide.from),
                }
          }
        />
      ))}

      {/* Every selected element gets an outline; the handles go on the union,
          so dragging a corner resizes the group as one shape. */}
      {rects.map((rect, index) => (
        <div
          key={index}
          className="marker"
          style={{
            left: ptToPx(rect.x),
            top: ptToPx(rect.y),
            width: ptToPx(rect.w),
            height: ptToPx(rect.h),
          }}
        />
      ))}

      {box && (
        <div
          className="selection"
          style={{
            left: ptToPx(box.x),
            top: ptToPx(box.y),
            width: ptToPx(box.w),
            height: ptToPx(box.h),
          }}
        >
          {HANDLES.map((handle) => (
            <span
              key={handle}
              className="handle"
              data-handle={handle}
              style={{
                left: HANDLE_POSITION[handle].left,
                top: HANDLE_POSITION[handle].top,
                cursor: HANDLE_POSITION[handle].cursor,
              }}
              onPointerDown={(event) => onHandleDown(event, handle)}
            />
          ))}

          {/* Only for images and shapes. A rotated contentEditable breaks
              caret positioning in every browser, so offering the handle on a
              text frame would promise something that does not work. */}
          {rotatable && (
            <span
              className="handle handle--rotate"
              data-handle="rotate"
              title="Drag to rotate; hold Shift for 15° steps"
              onPointerDown={(event) => onRotateDown(event)}
            />
          )}
        </div>
      )}
    </div>
  );
}
