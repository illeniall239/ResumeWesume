/**
 * The canvas: every version of a résumé, side by side, one of them live.
 *
 * Only the selected board is the real editor — the same `PageCanvas` the studio
 * has always shown, with the same ops, the same undo and the same pen. The rest
 * are drawn from the copies the canvas endpoint returned and are not editable;
 * clicking one selects it, and then it becomes the live one.
 *
 * That is the whole trick, and it is why nothing downstream had to change: a
 * board is a document, and at any moment exactly one document is open.
 */

'use client';

import { useEffect, useRef } from 'react';

import type { DocOp, DocumentResponse, StudioDoc } from '@/contracts/doc';
import { AgentCursor } from '@/canvas/agent-cursor';
import { PageCanvas } from '@/canvas/page-canvas';
import { useStudio } from '@/store/studio';

export function BoardPlane({
  boards,
  selected,
  onSelect,
  documentId,
  zoom,
  hostRef,
  commit,
  onEditText,
  onEditField,
  onFocusNode,
  onSplitLine,
  onRemoveLine,
}: {
  boards: DocumentResponse[];
  selected: string | null;
  onSelect: (boardId: string) => void;
  /** The board `useStudio` currently holds, which may lag `selected` by a load. */
  documentId: string | null;
  zoom: number;
  hostRef: React.RefObject<HTMLDivElement | null>;
  commit: (ops: DocOp[]) => void;
  onEditText: (nid: string, value: string) => void;
  onEditField: (target: string, value: string) => void;
  onFocusNode: (nid: string | null) => void;
  /** Enter at the end of a bullet opens the next one; Backspace closes an empty one. */
  onSplitLine: (nid: string) => void;
  onRemoveLine: (nid: string) => void;
}) {
  const doc = useStudio((state) => state.doc);
  const changed = useStudio((state) => state.changed);
  const unverified = useStudio((state) => state.unverified);
  const locked = useStudio((state) => state.locked);
  const drafts = useStudio((state) => state.drafts);

  // Bring a newly selected board into view. Selecting one at the far right of
  // a wide canvas and having nothing move is the interaction failing quietly.
  const marks = useRef(new Map<string, HTMLDivElement>());
  useEffect(() => {
    if (!selected) return;
    marks.current
      .get(selected)
      ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
  }, [selected]);

  return (
    <div className="plane">
      {boards.map((board) => {
        const live = board.id === selected && board.id === documentId && doc;
        return (
          <div
            key={board.id}
            className={`board${board.id === selected ? ' board--on' : ''}`}
            ref={(node) => {
              if (node) marks.current.set(board.id, node);
              else marks.current.delete(board.id);
            }}
          >
            <div className="board__name">
              {/* A name, and a way to reach the board it names. The whole card
                  is not the target: the live board is an editor, and a click
                  anywhere in it has to land in the text rather than on a
                  selector wrapped around it. */}
              <button
                type="button"
                className="board__pick"
                onClick={() => onSelect(board.id)}
                aria-pressed={board.id === selected}
              >
                {board.title || 'Untitled'}
              </button>
            </div>

            {live ? (
              <div className="sheet" style={{ zoom }} ref={hostRef}>
                <PageCanvas
                  doc={doc as StudioDoc}
                  changed={changed}
                  unverified={unverified}
                  locked={locked}
                  drafts={drafts}
                  editable
                  onFocusNode={onFocusNode}
                  onSplitLine={onSplitLine}
                  onRemoveLine={onRemoveLine}
                  onEditText={onEditText}
                  onEditField={onEditField}
                  interactive
                  commit={commit}
                />
                {/* Where the agent is working, while it works. */}
                <AgentCursor host={hostRef} zoom={zoom} />
              </div>
            ) : (
              // Not editable, and not interactive: a second live editor would
              // be a second document open at once, which is the one thing the
              // whole shape of this depends on not happening.
              <button
                type="button"
                className="board__still"
                onClick={() => onSelect(board.id)}
                aria-label={`Work on ${board.title || 'Untitled'}`}
              >
                <span className="sheet" style={{ zoom }}>
                  <PageCanvas doc={board.doc} editable={false} />
                </span>
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default BoardPlane;
