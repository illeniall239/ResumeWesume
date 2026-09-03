/**
 * Getting rid of a résumé you no longer want.
 *
 * The endpoint existed from the beginning and nothing in the interface reached
 * it, so a document could be created and never removed.
 *
 * It asks first, because there is no undo for this one: undo reverses a batch
 * *within* a document, and a deleted document has no op log left to reverse.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RecentSheet } from '@/register/recent-sheet';
import type { DocumentResponse } from '@/contracts/doc';

vi.mock('@/lib/api', () => ({
  deleteDocument: vi.fn(() => Promise.resolve()),
}));

const sheet = {
  id: 'doc_1',
  title: 'Rao Muhammad Hamza',
  version: 4,
  hash: 'abc',
  doc: {} as never,
  updated_at: null,
} as DocumentResponse;

function draw(over: Partial<Parameters<typeof RecentSheet>[0]> = {}) {
  const onDeleted = vi.fn();
  const onError = vi.fn();
  render(
    <RecentSheet document={sheet} onDeleted={onDeleted} onError={onError} {...over} />
  );
  return { onDeleted, onError };
}

describe('removing a résumé from the register', () => {
  it('opens the résumé by default, and offers no destructive control by mistake', () => {
    draw();

    expect(screen.getByRole('link')).toHaveAttribute('href', '/studio/doc_1');
    expect(screen.queryByText('Delete this résumé?')).toBeNull();
  });

  it('asks before it deletes', () => {
    draw();

    fireEvent.click(screen.getByLabelText(/Delete Rao/));

    expect(screen.getByText('Delete this résumé?')).toBeTruthy();
  });

  it('backing out leaves it alone', async () => {
    const { onDeleted } = draw();
    const { deleteDocument } = await import('@/lib/api');

    fireEvent.click(screen.getByLabelText(/Delete Rao/));
    fireEvent.click(screen.getByText('Keep'));

    expect(deleteDocument).not.toHaveBeenCalled();
    expect(onDeleted).not.toHaveBeenCalled();
    expect(screen.getByRole('link')).toBeTruthy();
  });

  it('the second press is the one that deletes', async () => {
    const { onDeleted } = draw();
    const { deleteDocument } = await import('@/lib/api');

    fireEvent.click(screen.getByLabelText(/Delete Rao/));
    fireEvent.click(screen.getByText('Delete'));

    await vi.waitFor(() => expect(deleteDocument).toHaveBeenCalledWith('doc_1'));
    await vi.waitFor(() => expect(onDeleted).toHaveBeenCalledWith('doc_1'));
  });

  it('a failure is reported and the résumé stays', async () => {
    const { deleteDocument } = await import('@/lib/api');
    vi.mocked(deleteDocument).mockRejectedValueOnce(new Error('offline'));
    const { onDeleted, onError } = draw();

    fireEvent.click(screen.getByLabelText(/Delete Rao/));
    fireEvent.click(screen.getByText('Delete'));

    await vi.waitFor(() => expect(onError).toHaveBeenCalledWith('offline'));
    expect(onDeleted).not.toHaveBeenCalled();
    // Back to the resting state, so it can be tried again.
    expect(screen.getByRole('link')).toBeTruthy();
  });
});
