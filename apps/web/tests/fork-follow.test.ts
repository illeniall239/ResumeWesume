/**
 * Following the turn onto a version it has just started.
 *
 * When the assistant forks, everything after that lands on the copy. A page
 * still showing the original would draw patches against a document that never
 * received them — the ops name nodes that exist on both, so nothing would look
 * obviously wrong until the compare-and-set failed or, worse, did not.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, fetchDocument: vi.fn() };
});

import { fetchDocument } from '@/lib/api';
import { applyEvent, useChat, type ChatMessage } from '@/store/chat';
import { useCanvas } from '@/store/canvas';
import { useStudio } from '@/store/studio';
import type { DocumentResponse } from '@/contracts/doc';

const DOC = { schema_version: 1, sections: [], personal: {}, pages: [], unverified: [] };

const board = (id: string, title: string): DocumentResponse =>
  ({
    id,
    title,
    version: 1,
    hash: 'h',
    doc: DOC,
    canvas_id: 'cnv_1',
    job_description: 'Stripe. Payments.',
  }) as never;

/** Run one `board_forked` event through the reducer. */
function fork(boardId = 'doc_new', title = 'Stripe — Payments') {
  let held: ChatMessage = {
    id: 'a1',
    role: 'assistant',
    text: '',
    status: 'streaming',
    activity: [
      { callId: 'c1', name: 'fork_board', tier: 'A', status: 'running' },
    ],
  };
  applyEvent(
    {
      v: 1,
      seq: 3,
      ts: '',
      turn_id: 't1',
      type: 'board_forked',
      call_id: 'c1',
      board_id: boardId,
      title,
      from_board: 'doc_1',
    },
    (change) => {
      held = change(held);
    },
    () => {},
    'a1'
  );
  return held;
}

beforeEach(() => {
  vi.clearAllMocks();
  useChat.setState({ messages: [], streaming: false, error: null, confirm: null });
  useCanvas.setState({
    id: 'cnv_1',
    title: 'Alex Morgan',
    boards: [board('doc_1', 'Alex Morgan')],
    selected: 'doc_1',
    loading: false,
    error: null,
  });
  useStudio.setState({
    documentId: 'doc_1',
    doc: DOC as never,
    serverDoc: DOC as never,
    version: 1,
    error: null,
  });
});

describe('when the assistant starts a version', () => {
  it('puts it on the plane and selects it', async () => {
    vi.mocked(fetchDocument).mockResolvedValue(board('doc_new', 'Stripe — Payments'));

    fork();

    await vi.waitFor(() => {
      expect(useCanvas.getState().boards.map((b) => b.id)).toEqual(['doc_1', 'doc_new']);
    });
    expect(useCanvas.getState().selected).toBe('doc_new');
  });

  it('opens it as the live document', async () => {
    // Everything after the fork lands here, so this is the document the ops
    // have to be compare-and-set against.
    vi.mocked(fetchDocument).mockResolvedValue({
      ...board('doc_new', 'Stripe — Payments'),
      version: 7,
      hash: 'h7',
    } as never);

    fork();

    await vi.waitFor(() => expect(useStudio.getState().documentId).toBe('doc_new'));
    expect(useStudio.getState().version).toBe(7);
    expect(useStudio.getState().hash).toBe('h7');
    expect(useStudio.getState().title).toBe('Stripe — Payments');
  });

  it('reads the copy in full rather than assembling it from the event', async () => {
    // The event carries a name and an id. The copy has its own version, hash
    // and unverified marks, and the next patch is compare-and-set against
    // exactly those.
    vi.mocked(fetchDocument).mockResolvedValue(board('doc_new', 'Stripe — Payments'));

    fork();

    await vi.waitFor(() => expect(fetchDocument).toHaveBeenCalledWith('doc_new'));
    expect(useStudio.getState().jobDescription).toBe('Stripe. Payments.');
  });

  it('drops work that was in flight on the version being left', async () => {
    // Local ops name nodes on the original. Replayed onto the copy they would
    // land as edits nobody asked that version for.
    vi.mocked(fetchDocument).mockResolvedValue(board('doc_new', 'Stripe — Payments'));
    useStudio.setState({
      local: [{ op: 'set_text', nid: 'blt_x', value: 'typed a moment ago' }] as never,
      changed: new Set(['blt_x']),
    });

    fork();

    await vi.waitFor(() => expect(useStudio.getState().documentId).toBe('doc_new'));
    expect(useStudio.getState().local).toEqual([]);
    expect(useStudio.getState().pending).toEqual([]);
    expect(useStudio.getState().changed.size).toBe(0);
  });

  it('marks the step as done, naming the version', () => {
    vi.mocked(fetchDocument).mockResolvedValue(board('doc_new', 'Stripe — Payments'));

    const message = fork();

    expect(message.activity[0].status).toBe('applied');
    expect(message.activity[0].label).toBe('started Stripe — Payments');
  });

  it('says so rather than failing when the copy cannot be opened', async () => {
    // The version exists on the server either way; it will be there on the
    // next load. An error over a résumé that is fine would be worse.
    vi.mocked(fetchDocument).mockRejectedValue(new Error('offline'));

    fork();

    await vi.waitFor(() => expect(useStudio.getState().error).toMatch(/Reload to see it/));
    expect(useStudio.getState().documentId).toBe('doc_1');
  });
});
