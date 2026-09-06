/**
 * A canvas in the register: its versions, its name, and getting rid of it.
 *
 * This replaces the per-document card. The register lists canvases now — a
 * résumé and the versions of it aimed at particular jobs — and those belong on
 * one row rather than scattered through the list as unrelated documents.
 *
 * Deleting still asks first, and for a stronger reason than before: a canvas
 * takes every board with it, and there is no undo. Undo reverses a batch
 * *within* a document, and a deleted canvas has no op log left to reverse.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CanvasCard } from '@/register/canvas-card';
import { PREVIEW_DOC } from '@/render/preview-doc';
import type { CanvasResponse, DocumentResponse } from '@/contracts/doc';

vi.mock('@/lib/api', () => ({
  deleteCanvas: vi.fn(() => Promise.resolve()),
}));

const board = (id: string, title: string): DocumentResponse =>
  ({
    id,
    title,
    version: 4,
    hash: 'abc',
    // A real document, because the card renders one: the sheet on it is the
    // same `DocumentFlow` the studio and the PDF use, and the list endpoint
    // returns every board in full so there is nothing to fetch.
    doc: PREVIEW_DOC,
    updated_at: null,
  }) as DocumentResponse;

const canvas = (boards: DocumentResponse[]): CanvasResponse =>
  ({
    id: 'cnv_1',
    title: 'Rao Muhammad Hamza',
    boards,
    updated_at: null,
  }) as CanvasResponse;

function draw(boards = [board('doc_1', 'Rao Muhammad Hamza')]) {
  const onDeleted = vi.fn();
  const onError = vi.fn();
  render(
    <CanvasCard canvas={canvas(boards)} onDeleted={onDeleted} onError={onError} />
  );
  return { onDeleted, onError };
}

describe('what a canvas card shows', () => {
  it('opens the canvas, and offers no destructive control by mistake', () => {
    draw();

    // The canvas, which is what this card is. The studio follows a board id to
    // the canvas it sits on as well, so old links keep working — but a card
    // for a canvas should name one.
    expect(screen.getByRole('link')).toHaveAttribute('href', '/studio/cnv_1');
    expect(screen.queryByText(/Delete this/)).toBeNull();
  });

  it('says nothing about versions when there is only one', () => {
    // "1 version" is a fact about the database. The card should read exactly
    // as it did before canvases existed, for the résumé that has only one.
    draw();
    expect(screen.queryByText(/versions/)).toBeNull();
  });

  it('counts the versions once there are several', () => {
    draw([board('doc_1', 'General'), board('doc_2', 'Stripe'), board('doc_3', 'Datadog')]);
    expect(screen.getByText('3 versions')).toBeInTheDocument();
  });

  it('draws three sheets and counts the rest', () => {
    // A row of eleven thumbnails at this size is a texture rather than a set
    // of documents, and the fourth onwards are all the same shape anyway.
    const many = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) => board(id, id));
    const { container } = render(
      <CanvasCard canvas={canvas(many)} onDeleted={vi.fn()} onError={vi.fn()} />
    );
    expect(container.querySelectorAll('.reg-doc__paper')).toHaveLength(3);
    expect(screen.getByText('+3')).toBeInTheDocument();
  });

  it('says so plainly when a canvas has nothing on it yet', () => {
    // Real, not broken: one exists before its first résumé arrives from an
    // import or a template.
    draw([]);
    expect(screen.getByText(/nothing on this canvas yet/i)).toBeInTheDocument();
    expect(screen.queryByRole('link')).toBeNull();
  });
});

describe('removing a canvas', () => {
  it('asks before it deletes', () => {
    draw();
    fireEvent.click(screen.getByLabelText(/Delete Rao/));
    expect(screen.getByText('Delete this?')).toBeTruthy();
  });

  it('says how much goes when there is more than one version', () => {
    // The stakes are higher than they were for a single document, and the
    // question should say so rather than let somebody find out afterwards.
    draw([board('doc_1', 'General'), board('doc_2', 'Stripe')]);
    fireEvent.click(screen.getByLabelText(/Delete Rao/));
    expect(screen.getByText('Delete this and all 2 versions?')).toBeTruthy();
  });

  it('backing out leaves it alone', async () => {
    const { onDeleted } = draw();
    const { deleteCanvas } = await import('@/lib/api');

    fireEvent.click(screen.getByLabelText(/Delete Rao/));
    fireEvent.click(screen.getByText('Keep'));

    expect(deleteCanvas).not.toHaveBeenCalled();
    expect(onDeleted).not.toHaveBeenCalled();
    expect(screen.getByRole('link')).toBeTruthy();
  });

  it('the second press is the one that deletes', async () => {
    const { onDeleted } = draw();
    const { deleteCanvas } = await import('@/lib/api');

    fireEvent.click(screen.getByLabelText(/Delete Rao/));
    fireEvent.click(screen.getByText('Delete'));

    await vi.waitFor(() => expect(deleteCanvas).toHaveBeenCalledWith('cnv_1'));
    await vi.waitFor(() => expect(onDeleted).toHaveBeenCalledWith('cnv_1'));
  });

  it('a failure is reported and the canvas stays', async () => {
    const { deleteCanvas } = await import('@/lib/api');
    vi.mocked(deleteCanvas).mockRejectedValueOnce(new Error('offline'));
    const { onDeleted, onError } = draw();

    fireEvent.click(screen.getByLabelText(/Delete Rao/));
    fireEvent.click(screen.getByText('Delete'));

    await vi.waitFor(() => expect(onError).toHaveBeenCalledWith('offline'));
    expect(onDeleted).not.toHaveBeenCalled();
    // Back to the resting state, so it can be tried again.
    expect(screen.getByRole('link')).toBeTruthy();
  });
});

describe('the way to be rid of a résumé', () => {
  it('is a bin, on the name row', () => {
    // A cross is the mark this app uses for a rejected edit and for closing a
    // dialog, neither of which destroys anything. And it sat among the
    // metadata, where it read as furniture belonging to the timestamp rather
    // than to the résumé it deletes.
    const { container } = render(
      <CanvasCard canvas={canvas([board('doc_1', 'Rao Muhammad Hamza')])} onDeleted={vi.fn()} onError={vi.fn()} />
    );
    const head = container.querySelector('.reg-doc__head')!;
    expect(head.querySelector('.reg-doc__name')).toBeTruthy();
    expect(head.querySelector('.reg-doc__drop')).toBeTruthy();
  });

  it('stays in the tab order while it is invisible', () => {
    // It is drawn on hover, which for anyone not using a mouse means on focus.
    // Hiding it with `visibility` or `display` would make it unfocusable, so
    // `:focus-visible` could never match and the control would be unreachable
    // without a pointer.
    const { container } = render(
      <CanvasCard canvas={canvas([board('doc_1', 'Rao Muhammad Hamza')])} onDeleted={vi.fn()} onError={vi.fn()} />
    );
    const bin = container.querySelector<HTMLButtonElement>('.reg-doc__drop')!;
    expect(bin.hidden).toBe(false);
    expect(bin.disabled).toBe(false);
    expect(bin.getAttribute('aria-label')).toMatch(/^Delete /);
  });
});
