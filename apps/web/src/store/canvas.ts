/**
 * The canvas: a résumé and the versions of it aimed at particular jobs.
 *
 * Separate from `useStudio` on purpose. This store holds every board so the
 * plane can draw them; `useStudio` holds the one that is *selected*, live and
 * editable. Keeping them apart is what lets ops, undo, the agent and export go
 * on working on a board exactly as they worked on a document — none of them
 * had to learn that a canvas exists.
 *
 * The selected board's document is therefore read from `useStudio`, never from
 * the copy here: the copy is what the server last sent, and an edit made a
 * moment ago would otherwise redraw the plane with the version before it.
 */

'use client';

import { create } from 'zustand';

import type { CanvasResponse, DocumentResponse } from '@/contracts/doc';
import { fetchCanvas, fetchDocument, isNotFound, renameCanvas } from '@/lib/api';

interface CanvasState {
  id: string | null;
  title: string;
  /** Every board, as the server last sent it, in the order they were created. */
  boards: DocumentResponse[];
  /** Which board the rails, the chat and the agent are pointed at. */
  selected: string | null;

  loading: boolean;
  error: string | null;

  /**
   * Open a canvas, or the canvas a board sits on.
   *
   * Resolves to the board that ended up selected, so the page can hand that to
   * `useStudio` — which is the store everything else in the studio reads.
   */
  open: (id: string) => Promise<string | null>;
  select: (boardId: string) => void;
  rename: (title: string) => Promise<void>;
  /** Fold a board the server has just returned back into the plane. */
  absorb: (board: DocumentResponse) => void;
  /**
   * A version the assistant started mid-turn, which the turn then moved onto.
   *
   * The page has to follow. Everything after the fork lands on the new board,
   * and a plane still showing the original would draw patches against a
   * document that never received them.
   */
  adopt: (board: DocumentResponse) => void;
  reset: () => void;
}

/**
 * The canvas's own name, when it is worth saying.
 *
 * A canvas and its first board are created together and named the same thing,
 * so on the résumé almost everybody has, printing both would put one name on
 * screen twice — the same mistake "Untitled — Centered  Centered" already was.
 * It earns its place once the boards have names of their own.
 */
export function canvasLabel(
  canvasTitle: string | null | undefined,
  boardTitle: string | null | undefined
): string | null {
  const canvas = (canvasTitle ?? '').trim();
  if (!canvas) return null;
  return canvas.toLowerCase() === (boardTitle ?? '').trim().toLowerCase() ? null : canvas;
}

const EMPTY = {
  id: null,
  title: '',
  boards: [],
  selected: null,
  loading: false,
  error: null,
};

export const useCanvas = create<CanvasState>((set, get) => ({
  ...EMPTY,

  async open(id) {
    set({ loading: true, error: null });
    try {
      let canvas: CanvasResponse;
      let wanted: string | null = null;
      try {
        canvas = await fetchCanvas(id);
      } catch (cause) {
        // Not a canvas. It may still be a board — every link written before
        // canvases existed points at one, and so does every bookmark somebody
        // already has. Following it to the canvas it sits on is the difference
        // between an old link working and an old link 404ing.
        if (!isNotFound(cause)) throw cause;
        const board = await fetchDocument(id);
        if (!board.canvas_id) throw cause;
        canvas = await fetchCanvas(board.canvas_id);
        wanted = board.id;
      }

      const boards = canvas.boards ?? [];
      const selected = wanted ?? boards[0]?.id ?? null;
      set({
        id: canvas.id,
        title: canvas.title,
        boards,
        selected,
        loading: false,
      });
      return selected;
    } catch (cause) {
      set({ loading: false, error: (cause as Error).message });
      return null;
    }
  },

  select(boardId) {
    if (get().selected === boardId) return;
    set({ selected: boardId });
  },

  async rename(title) {
    const { id } = get();
    if (!id) return;
    const clean = title.trim();
    // A blank name would leave the register with an unclickable-looking row.
    // Refused here as well as on the server, so the field snaps back rather
    // than showing an error for something nobody meant to do.
    if (!clean || clean === get().title) return;
    try {
      const canvas = await renameCanvas(id, clean);
      set({ title: canvas.title });
    } catch (cause) {
      set({ error: (cause as Error).message });
    }
  },

  absorb(board) {
    set({
      boards: get().boards.map((held) => (held.id === board.id ? board : held)),
    });
  },

  adopt(board) {
    const { boards } = get();
    set({
      boards: boards.some((held) => held.id === board.id)
        ? boards.map((held) => (held.id === board.id ? board : held))
        : [...boards, board],
      selected: board.id,
    });
  },

  reset() {
    set({ ...EMPTY });
  },
}));
