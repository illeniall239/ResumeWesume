/**
 * Undoing a whole agent turn.
 *
 * Ctrl+Z reverses one committed batch, and the agent commits one per tool
 * call — so undoing a fourteen-edit turn by hand is fourteen presses, and
 * people stop halfway. The turn's starting point is not recoverable from the
 * op log afterwards either: fourteen versions by an agent look exactly like
 * fourteen hand edits. So the loop takes a snapshot before its first mutation
 * and streams the id on `done`, and this is what carries it to a control.
 */

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, revertToCheckpoint: vi.fn() };
});

import { revertToCheckpoint } from '@/lib/api';
import { Revision } from '@/chat/chat-panel';
import { applyEvent, useChat, type ChatMessage } from '@/store/chat';
import { useStudio } from '@/store/studio';

const DOC = { schema_version: 1, sections: [], personal: {}, pages: [] };

function message(over: Partial<ChatMessage> = {}): ChatMessage {
  return { id: 'a1', role: 'assistant', text: 'Done.', activity: [], ...over };
}

/** A `done` event, run through the reducer, returning the patched message. */
function afterDone(extra: Record<string, unknown>): ChatMessage {
  let held = message({ status: 'streaming' });
  applyEvent(
    { v: 1, seq: 9, ts: '', turn_id: 't1', type: 'done', doc_version: 4, hash: 'h', ...extra },
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
  useStudio.setState({
    documentId: 'doc-1',
    doc: DOC as never,
    serverDoc: DOC as never,
    version: 4,
    error: null,
    saving: false,
  });
});

describe('remembering where a turn began', () => {
  it('keeps the checkpoint of a turn that changed something', () => {
    expect(
      afterDone({
        checkpoints: [{ board_id: 'doc-1', checkpoint_id: 'cp-1' }],
        applied: 3,
      }).checkpoints
    ).toEqual([{ boardId: 'doc-1', checkpointId: 'cp-1' }]);
  });

  it('keeps nothing for a turn that changed nothing', () => {
    // The server takes a checkpoint for every turn, answers included. Offering
    // to undo a turn that only answered a question would restore a document
    // identical to the current one: a new version, a fresh history entry, and
    // no visible effect — which reads as a broken control.
    expect(
      afterDone({
        checkpoints: [{ board_id: 'doc-1', checkpoint_id: 'cp-1' }],
        applied: 0,
      }).checkpoints
    ).toBeUndefined();
  });

  it('has nothing to offer when the server sends no snapshot at all', () => {
    expect(afterDone({ applied: 2 }).checkpoints).toEqual([]);
  });
});

describe('undoing the turn', () => {
  it('reverts to the checkpoint and marks the turn', async () => {
    vi.mocked(revertToCheckpoint).mockResolvedValue({
      id: 'doc-1',
      title: 'R',
      version: 5,
      hash: 'h2',
      doc: DOC as never,
      updated_at: '',
    } as never);
    useChat.setState({ messages: [message({ checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-1' }] })] });

    await useChat.getState().undoTurn('a1');

    expect(revertToCheckpoint).toHaveBeenCalledWith('doc-1', 'cp-1');
    expect(useChat.getState().messages[0].reverted).toBe(true);
    // The server document has to move too, or the next render rebuilds `doc`
    // from a stale base and the revert appears not to have happened.
    expect(useStudio.getState().version).toBe(5);
    expect(useStudio.getState().serverDoc).toBe(useStudio.getState().doc);
  });

  it('drops work that was in flight during the turn', async () => {
    // Local ops name nodes the reverted document may no longer have. Replayed
    // on top they are a rejection at best and a mangled sheet at worst.
    vi.mocked(revertToCheckpoint).mockResolvedValue({
      id: 'doc-1',
      title: 'R',
      version: 5,
      hash: 'h2',
      doc: DOC as never,
      updated_at: '',
    } as never);
    useStudio.setState({
      local: [{ op: 'set_text', nid: 'gone', value: 'x' }] as never,
      changed: new Set(['gone']),
    });
    useChat.setState({ messages: [message({ checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-1' }] })] });

    await useChat.getState().undoTurn('a1');

    expect(useStudio.getState().local).toEqual([]);
    expect(useStudio.getState().pending).toEqual([]);
    expect(useStudio.getState().changed.size).toBe(0);
  });

  it('does not mark the turn when the revert failed', async () => {
    // A line saying "reverted" over a sheet that still carries the changes is
    // worse than no line at all.
    vi.mocked(revertToCheckpoint).mockRejectedValue(new Error('409: gone'));
    useChat.setState({ messages: [message({ checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-1' }] })] });

    await useChat.getState().undoTurn('a1');

    expect(useChat.getState().messages[0].reverted).toBeUndefined();
    expect(useStudio.getState().error).toContain('409');
  });

  it('will not revert the same turn twice', async () => {
    useChat.setState({ messages: [message({ checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-1' }], reverted: true })] });
    await useChat.getState().undoTurn('a1');
    expect(revertToCheckpoint).not.toHaveBeenCalled();
  });

  it('does nothing for a turn that has no checkpoint', async () => {
    useChat.setState({ messages: [message()] });
    await useChat.getState().undoTurn('a1');
    expect(revertToCheckpoint).not.toHaveBeenCalled();
  });
});

describe('the offer, in the transcript', () => {
  const shown = () => screen.queryByRole('button', { name: /undo this turn/i });

  it('is made on a turn that changed the document', () => {
    render(<Revision message={message({ status: 'ok', checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-1' }] })} />);
    expect(shown()).toBeInTheDocument();
  });

  it('is not made on a turn that only answered', () => {
    render(<Revision message={message({ status: 'ok' })} />);
    expect(shown()).not.toBeInTheDocument();
  });

  it('is replaced by a statement once taken', () => {
    render(<Revision message={message({ status: 'ok', checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-1' }], reverted: true })} />);
    expect(shown()).not.toBeInTheDocument();
    expect(screen.getByText(/as it was before this/i)).toBeInTheDocument();
  });

  it('is unavailable while the document is mid-write', () => {
    // A revert lands as a whole new version; racing it against a save in
    // flight is how two clients end up disagreeing about which one won.
    useStudio.setState({ saving: true });
    render(<Revision message={message({ status: 'ok', checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-1' }] })} />);
    expect(shown()).toBeDisabled();
  });
});
