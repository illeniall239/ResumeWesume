/**
 * A document as sheets of paper with elements placed on them.
 *
 * Pages are real containers here, not an overlay: each `.canvas-page` is a
 * fixed-size sheet and every element is absolutely positioned inside it from
 * its stored rect. That is the opposite of the flowing renderer this replaces,
 * where sheets were painted *behind* one continuous column and spacers pushed
 * content across the boundary.
 *
 * Two properties are load-bearing and easy to lose:
 *
 * **Elements render in reading order, not paint order.** Chromium's PDF text
 * layer follows DOM order, so a page whose DOM is z-ordered extracts as
 * gibberish however it looks. Paint order is restored with `z-index` instead,
 * which costs nothing and keeps the text layer sane. See `reading-order.ts`.
 *
 * **Text stays real DOM.** A canvas-based renderer would be simpler to write
 * and would take `contentEditable`, the change flash, the node addressing and
 * Chromium's selectable text layer with it.
 */

'use client';

import { useCallback } from 'react';

import type { AnyElement, DocOp, PageNode, StudioDoc } from '@/contracts/doc';
import DocumentFlow, { type DocumentFlowProps } from '@/render/document-flow';

import { SelectionOverlay } from './overlay';
import { isRotatable } from './geometry';
import { canDeletePage, canMovePage, deletePage, movePage } from './pages';
import { readingOrder } from './reading-order';
import { useSelection } from './selection';
import { useView } from './view';
import { useDrag } from './use-drag';
import { useMeasuredRects } from './use-measured-rects';
import { useMarquee } from './use-marquee';
import { pageSpec, ptToPx } from './units';
import { ArrowDown, ArrowUp, Cross } from '@/ui/marks';

type ElementProps = Omit<DocumentFlowProps, 'doc' | 'root' | 'exclude'> & {
  doc: StudioDoc;
  /** Refs claimed by some frame, so a section frame does not redraw a pulled-out entry. */
  claimed: ReadonlySet<string>;
};

function ElementView({
  element,
  zIndex,
  selected,
  editing,
  onPointerDown,
  onDoubleClick,
  ...rest
}: {
  element: AnyElement;
  zIndex: number;
  selected: boolean;
  /** True for the one element whose text is live. */
  editing: boolean;
  onPointerDown?: (event: React.PointerEvent) => void;
  onDoubleClick?: (event: React.MouseEvent) => void;
} & ElementProps) {
  const { doc, claimed, ...flow } = rest;
  const rect = element.rect;
  const style: React.CSSProperties = {
    left: ptToPx(rect.x),
    top: ptToPx(rect.y),
    width: ptToPx(rect.w),
    zIndex,
    // An autogrowing frame takes its height from its text and is given no
    // minimum at all. The stored height is the server's guess, refined by the
    // reflow pass -- applying it as a floor holds a two-line entry open at the
    // guessed 88pt forever, which showed up as tall empty gaps between the
    // education entries. A fixed frame is sized as asked.
    minHeight:
      'autogrow' in element && element.autogrow === 'height'
        ? undefined
        : ptToPx(rect.h),
    transform: element.rotation ? `rotate(${element.rotation}deg)` : undefined,
    opacity: 'style' in element ? element.style.opacity : 1,
    // The element's own alignment. Stored since the schema was written and
    // never read here, so a footer placed against the right edge of the page
    // still had its words starting at the left of its box -- which is what
    // "it was not all the way to the right" meant, and no amount of moving the
    // box could fix.
    textAlign: 'style' in element ? element.style.align : undefined,
  };

  if (!element.visible) return null;

  if ('ref' in element) {
    return (
      <div
        className={`element element--frame${selected ? ' element--selected' : ''}`}
        style={style}
        data-element={element.nid}
        data-ref={element.ref}
        data-autogrow={element.autogrow}
        onPointerDown={onPointerDown}
        onDoubleClick={onDoubleClick}
      >
        {/* Editable only while this element is the one being edited. A frame
            whose text is always live has no surface left to grab: its `<p>`
            fills it, every press lands on a caret, and the box cannot be
            selected, moved or deleted. */}
        <DocumentFlow
          doc={doc}
          root={element.ref}
          exclude={claimed}
          {...flow}
          editable={flow.editable && editing}
          // Hints stay on whether or not this frame is the one being edited:
          // an empty field that renders nothing leaves nothing to double-click,
          // which is how a brand-new résumé arrived as a blank sheet.
          placeholders={flow.editable}
        />
      </div>
    );
  }

  if ('asset' in element) {
    return (
      <div
        className={`element element--image${selected ? ' element--selected' : ''}`}
        style={style}
        data-element={element.nid}
        onPointerDown={onPointerDown}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={`/api/v1/assets/${element.asset}`}
          alt={element.alt}
          style={{ objectFit: element.fit, height: ptToPx(rect.h) }}
        />
      </div>
    );
  }

  return (
    <div
      className={`element element--shape element--${element.shape}`}
      style={{
        ...style,
        height: ptToPx(rect.h),
        background: element.shape === 'line' ? undefined : element.fill ?? undefined,
        borderColor: element.stroke ?? undefined,
        borderWidth: element.stroke_width ? ptToPx(element.stroke_width) : undefined,
        borderStyle: element.stroke ? 'solid' : undefined,
      }}
      data-element={element.nid}
      onPointerDown={onPointerDown}
    />
  );
}

export function PageView({
  page,
  interactive,
  commit,
  ...rest
}: {
  page: PageNode;
  /** False for the print route, which must render no handles or guides. */
  interactive?: boolean;
  commit?: (ops: DocOp[]) => void;
} & ElementProps) {
  const spec = pageSpec(page.size, page.orientation);
  // Reading order for the DOM, paint order restored with z-index.
  const order = readingOrder(page);
  const paint = new Map(page.elements.map((element, index) => [element.nid, index]));

  const selected = useSelection((state) => state.selected);
  const editing = useSelection((state) => state.editing);
  const dragging = useSelection((state) => state.dragging);
  // Every pointer conversion below divides by this. The print surface renders
  // at 1 because it is not being looked at, it is being measured.
  const zoom = useView((state) => (interactive ? state.zoom : 1));

  const context = useCallback(
    () => ({
      siblings: page.elements.map((element) => ({
        nid: element.nid,
        rect: element.rect,
      })),
      page: { width: spec.width, height: spec.height, margin: spec.margin },
      zoom,
    }),
    [page.elements, spec, zoom]
  );

  const drag = useDrag(context, commit ?? (() => {}));
  const marquee = useMarquee(
    useCallback(
      () => page.elements.map((element) => ({ nid: element.nid, rect: element.rect })),
      [page.elements]
    ),
    zoom
  );
  const onPage = page.elements.filter((element) => selected.includes(element.nid));
  // Measured, not stored: an autogrow frame's stored height is an estimate the
  // browser is free to disagree with, and selection chrome drawn from it does
  // not match the element it is selecting.
  const measured = useMeasuredRects(
    onPage,
    `${selected.join(',')}|${page.elements.length}|${dragging}`
  );

  const targets = (nid?: string) => {
    // Read live rather than from the render snapshot: the press handler that
    // calls this has just changed the selection, and React has not re-rendered
    // yet -- so the closed-over value is one gesture behind.
    const held = useSelection.getState().selected;
    const chosen = nid && !held.includes(nid) ? [nid] : held;
    return page.elements
      .filter((element) => chosen.includes(element.nid))
      .map((element) => ({
        nid: element.nid,
        rect: element.rect,
        rotation: element.rotation,
        // Images and shapes carry no `pinned` field and need none: the reflow
        // only ever manages frames. Reporting them as already pinned keeps the
        // gesture from emitting an op that would be rejected.
        pinned: 'pinned' in element ? element.pinned : true,
      }));
  };

  return (
    <div
      className={`canvas-page${dragging ? ' canvas-page--dragging' : ''}`}
      data-page={page.nid}
      style={{
        width: ptToPx(spec.width),
        height: ptToPx(spec.height),
        background: page.background ?? undefined,
      }}
      onPointerMove={
        interactive
          ? (event) => {
              drag.move(event);
              marquee.move(event);
            }
          : undefined
      }
      onPointerUp={
        interactive
          ? (event) => {
              drag.end(event);
              marquee.end(event);
            }
          : undefined
      }
      onPointerDown={
        interactive
          ? (event) => {
              // A press on bare paper starts a rubber band; one on an element
              // stops propagating before it reaches here. Without Shift it
              // also clears, so clicking empty paper deselects.
              if (event.target !== event.currentTarget) return;
              if (!event.shiftKey) useSelection.getState().clear();
              marquee.begin(event);
            }
          : undefined
      }
    >
      {order.map((element) => (
        <ElementView
          key={element.nid}
          element={element}
          zIndex={paint.get(element.nid) ?? 0}
          selected={selected.includes(element.nid)}
          editing={editing === element.nid}
          onPointerDown={
            interactive
              ? (event) => {
                  // Hands off only while this element is the one being edited,
                  // so the caret, a text drag-select and a double-click inside
                  // the words all behave as they would anywhere else. Every
                  // other press selects the box, including one that landed on
                  // text: a frame filled edge to edge by its own `<p>` was
                  // otherwise impossible to pick up at all.
                  if (useSelection.getState().editing === element.nid) return;
                  event.stopPropagation();
                  // Pressing something already selected keeps the group, so a
                  // multi-selection can be dragged as one. Replacing it here
                  // would mean a group could only ever be moved by grabbing
                  // the selection border.
                  const held = useSelection.getState().selected;
                  if (event.shiftKey || !held.includes(element.nid)) {
                    useSelection.getState().select(element.nid, event.shiftKey);
                  } else {
                    useSelection.getState().stopEditing();
                  }
                  drag.begin(event, targets(element.nid), 'move');
                }
              : undefined
          }
          onDoubleClick={
            interactive && 'ref' in element
              ? (event) => {
                  event.stopPropagation();
                  useSelection.getState().beginEditing(element.nid);
                  caretAt(event.clientX, event.clientY);
                }
              : undefined
          }
          {...rest}
        />
      ))}

      {interactive && onPage.length > 0 && (
        <SelectionOverlay
          rects={measured}
          rotatable={onPage.length > 0 && onPage.every((e) => isRotatable(e.nid))}
          onHandleDown={(event, handle) => drag.begin(event, targets(), handle)}
          onRotateDown={(event) => drag.begin(event, targets(), 'rotate')}
        />
      )}
    </div>
  );
}

/**
 * Put the caret where the user double-clicked.
 *
 * Deferred a frame because the text is not editable yet: `beginEditing` has
 * just been called and React has not re-rendered, so focusing now lands on a
 * plain `<p>` and does nothing. `caretRangeFromPoint` is what makes this feel
 * like editing text rather than being dropped at the start of a paragraph;
 * where it is missing, focusing the line is a fair second best.
 */
function caretAt(clientX: number, clientY: number): void {
  requestAnimationFrame(() => {
    const under = document.elementFromPoint(clientX, clientY);
    // The editable itself, not its node: most of a resume is fields, which
    // carry `data-field` rather than `data-nid` because they belong to an
    // entry that already has one.
    const line = under?.closest<HTMLElement>('[contenteditable]');
    if (!line) return;
    line.focus();

    const owner = document as Document & {
      caretRangeFromPoint?: (x: number, y: number) => Range | null;
    };
    const range = owner.caretRangeFromPoint?.(clientX, clientY);
    if (!range) return;
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
  });
}

/**
 * The number and the controls beside a page.
 *
 * Outside the sheet, not on it: anything drawn inside `.canvas-page` competes
 * with the resume for space and lands in the PDF. Delete carries its own
 * reason when it is unavailable, because "this page holds the only copy of
 * some of your resume" is far more useful than a button that does nothing.
 */
function PageControls({
  doc,
  page,
  index,
  commit,
}: {
  doc: StudioDoc;
  page: PageNode;
  index: number;
  commit: (ops: DocOp[]) => void;
}) {
  const removable = canDeletePage(doc, page.nid);

  return (
    <div className="page-controls">
      <span className="page-controls__number">{index + 1}</span>
      <button
        type="button"
        title="Move page up"
        disabled={!canMovePage(doc, page.nid, -1)}
        onClick={() => commit(movePage(doc, page.nid, -1))}
      >
        <ArrowUp size={13} />
      </button>
      <button
        type="button"
        title="Move page down"
        disabled={!canMovePage(doc, page.nid, 1)}
        onClick={() => commit(movePage(doc, page.nid, 1))}
      >
        <ArrowDown size={13} />
      </button>
      {/* Shown only when it can actually do something. On a resume that flows
          top-to-bottom every page holds content, so this was a ✕ on every page
          that was permanently dead: deleting a page takes its frames with it,
          the content they render is then covered by nothing, and the coverage
          gate refuses the batch. Moving that content to the previous page
          first does not help either -- the reflow finds it does not fit and
          adds the page straight back. A control that can never fire reads as
          broken, so it appears on the blank page you actually want to remove
          and nowhere else. */}
      {removable.allowed && (
        <button
          type="button"
          className="page-controls__delete"
          title="Delete this page"
          onClick={() => commit(deletePage(doc, page.nid))}
        >
          <Cross size={13} />
        </button>
      )}
    </div>
  );
}

/** Every page of a document, stacked. */
export function PageCanvas({
  doc,
  interactive,
  commit,
  ...flow
}: {
  doc: StudioDoc;
  interactive?: boolean;
  commit?: (ops: DocOp[]) => void;
} & Omit<DocumentFlowProps, 'doc' | 'root' | 'exclude'>) {
  // A ref is claimed when some frame is bound to it. A section frame excludes
  // claimed entries so a job pulled onto the canvas is not also drawn inside
  // the section it came from.
  const claimed = new Set(
    doc.pages.flatMap((page) =>
      page.elements.filter((element) => 'ref' in element).map((element) => element.ref)
    )
  );

  return (
    <div className="canvas">
      {doc.pages.map((page, index) => (
        <div className="page-shell" key={page.nid}>
          {interactive && commit && (
            <PageControls doc={doc} page={page} index={index} commit={commit} />
          )}
          <PageView
            page={page}
            doc={doc}
            claimed={claimed}
            interactive={interactive}
            commit={commit}
            {...flow}
          />
        </div>
      ))}
    </div>
  );
}
