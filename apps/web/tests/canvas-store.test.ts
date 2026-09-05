/**
 * Opening a canvas, and following an old link to one.
 *
 * `/studio/<id>` names a canvas now. It used to name a document, so every link
 * anybody has written down — a bookmark, a link pasted into a message, the
 * address bar of a tab left open — points at a board. Following those to the
 * canvas the board sits on is the difference between an old link working and
 * an old link 404ing.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    fetchCanvas: vi.fn(),
    fetchDocument: vi.fn(),
    renameCanvas: vi.fn(),
  };
});

import { ApiError, fetchCanvas, fetchDocument, renameCanvas } from '@/lib/api';
import { canvasLabel, useCanvas } from '@/store/canvas';
import type { CanvasResponse, DocumentResponse } from '@/contracts/doc';

const board = (id: string, title: string): DocumentResponse =>
  ({ id, title, version: 1, hash: 'h', doc: {}, canvas_id: 'cnv_1' }) as never;

const CANVAS = {
  id: 'cnv_1',
  title: 'Rao Muhammad Hamza',
  boards: [board('doc_1', 'General'), board('doc_2', 'Stripe')],
} as CanvasResponse;

const notFound = () => new ApiError(404, 'Canvas not found', '404: Canvas not found');

beforeEach(() => {
  vi.clearAllMocks();
  useCanvas.getState().reset();
});

describe('opening a canvas', () => {
  it('selects its first board', async () => {
    vi.mocked(fetchCanvas).mockResolvedValue(CANVAS);

    const selected = await useCanvas.getState().open('cnv_1');

    expect(selected).toBe('doc_1');
    expect(useCanvas.getState().title).toBe('Rao Muhammad Hamza');
    expect(useCanvas.getState().boards).toHaveLength(2);
  });

  it('follows a link that names a board instead', async () => {
    // Every link written before canvases existed points at one of these.
    vi.mocked(fetchCanvas).mockRejectedValueOnce(notFound());
    vi.mocked(fetchDocument).mockResolvedValue(board('doc_2', 'Stripe'));
    vi.mocked(fetchCanvas).mockResolvedValueOnce(CANVAS);

    const selected = await useCanvas.getState().open('doc_2');

    // And selects the board that was asked for, not the first one: somebody
    // following a link to a particular version means that version.
    expect(selected).toBe('doc_2');
    expect(useCanvas.getState().id).toBe('cnv_1');
  });

  it('reports a link that names nothing at all', async () => {
    vi.mocked(fetchCanvas).mockRejectedValue(notFound());
    vi.mocked(fetchDocument).mockRejectedValue(notFound());

    expect(await useCanvas.getState().open('nope')).toBeNull();
    expect(useCanvas.getState().error).toContain('404');
  });

  it('opens a canvas with nothing on it without selecting anything', async () => {
    // Real rather than broken: one exists before its first résumé arrives.
    vi.mocked(fetchCanvas).mockResolvedValue({ ...CANVAS, boards: [] });

    expect(await useCanvas.getState().open('cnv_1')).toBeNull();
    expect(useCanvas.getState().error).toBeNull();
    expect(useCanvas.getState().title).toBe('Rao Muhammad Hamza');
  });
});

describe('keeping the plane current', () => {
  it('folds an edited board back in', async () => {
    // The plane draws unselected boards from what the server last sent, so an
    // edit to the live one has to be folded back — otherwise switching away
    // and back would show the version from before the edit.
    vi.mocked(fetchCanvas).mockResolvedValue(CANVAS);
    await useCanvas.getState().open('cnv_1');

    useCanvas.getState().absorb({ ...board('doc_1', 'General'), version: 7 } as never);

    expect(useCanvas.getState().boards[0].version).toBe(7);
    expect(useCanvas.getState().boards[1].version).toBe(1);
  });

  it('ignores a board that is not on this canvas', async () => {
    vi.mocked(fetchCanvas).mockResolvedValue(CANVAS);
    await useCanvas.getState().open('cnv_1');

    useCanvas.getState().absorb(board('doc_elsewhere', 'Other') as never);

    expect(useCanvas.getState().boards.map((b) => b.id)).toEqual(['doc_1', 'doc_2']);
  });
});

describe('renaming the canvas', () => {
  it('stores the new name', async () => {
    vi.mocked(fetchCanvas).mockResolvedValue(CANVAS);
    vi.mocked(renameCanvas).mockResolvedValue({ ...CANVAS, title: 'Job hunt' });
    await useCanvas.getState().open('cnv_1');

    await useCanvas.getState().rename('Job hunt');

    expect(renameCanvas).toHaveBeenCalledWith('cnv_1', 'Job hunt');
    expect(useCanvas.getState().title).toBe('Job hunt');
  });

  it('refuses a blank name without asking the server', async () => {
    // It would leave the register with an unclickable-looking row, and the
    // field should snap back rather than show an error for a stray blur.
    vi.mocked(fetchCanvas).mockResolvedValue(CANVAS);
    await useCanvas.getState().open('cnv_1');

    await useCanvas.getState().rename('   ');

    expect(renameCanvas).not.toHaveBeenCalled();
    expect(useCanvas.getState().title).toBe('Rao Muhammad Hamza');
  });
});

describe('when the canvas name is worth saying', () => {
  it('says nothing when it is the board name over again', () => {
    // A canvas and its first board are created together under one name, so on
    // the résumé almost everybody has this would print one name twice.
    expect(canvasLabel('Rao Muhammad Hamza', 'Rao Muhammad Hamza')).toBeNull();
    expect(canvasLabel('Rao Muhammad Hamza', '  rao muhammad hamza ')).toBeNull();
  });

  it('names it once the versions have names of their own', () => {
    expect(canvasLabel('Rao Muhammad Hamza', 'Stripe — Payments')).toBe(
      'Rao Muhammad Hamza'
    );
  });

  it('says nothing when there is no canvas name', () => {
    expect(canvasLabel(null, 'Stripe')).toBeNull();
    expect(canvasLabel('   ', 'Stripe')).toBeNull();
  });
});
