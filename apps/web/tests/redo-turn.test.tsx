/**
 * Putting a turn back after undoing it.
 *
 * Undo on its own is a trapdoor. Taking the offer is the only way to find out
 * what a turn actually did, and a fourteen-edit turn undone by mistake is
 * fourteen edits to type back by hand — which is precisely the cost the undo
 * button exists to remove, pointed the other way.
 *
 * It needs no second mechanism: a revert already writes the state it replaced
 * as a snapshot of its own, recorded as the inverse of its own op. So redo is
 * the same call at the other id, and the two ids swap places on every press.
 */

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, revertToCheckpoint: vi.fn() };
});

import { revertToCheckpoint } from '@/lib/api';
import { Revision } from '@/chat/chat-panel';
import { useChat, type ChatMessage } from '@/store/chat';
import { useStudio } from '@/store/studio';

const DOC = { schema_version: 1, sections: [], personal: {}, pages: [] };

function message(over: Partial<ChatMessage> = {}): ChatMessage {
  return { id: 'a1', role: 'assistant', text: 'Done.', activity: [], ...over };
}

/** The server's answer to a revert: the document, and the way back out of it. */
function reverts(to: string, version: number, wayBack: string) {
  vi.mocked(revertToCheckpoint).mockImplementation(async (_id, checkpoint) => {
    if (checkpoint !== to) throw new Error(`unexpected checkpoint ${checkpoint}`);
    return {
      id: 'doc-1',
      title: 'R',
      version,
      hash: `h${version}`,
      doc: DOC,
      updated_at: '',
      redo_checkpoint: wayBack,
    } as never;
  });
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

describe('the way back out of an undo', () => {
  it('is recorded when the turn is undone', async () => {
    // From the revert itself. Looked up later it would be a second search
    // through the log for something the server had just written.
    reverts('cp-before', 5, 'cp-after');
    useChat.setState({
      messages: [
        message({ checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-before' }] }),
      ],
    });

    await useChat.getState().undoTurn('a1');

    expect(useChat.getState().messages[0].redo).toEqual([
      { boardId: 'doc-1', checkpointId: 'cp-after' },
    ]);
  });

  it('puts the turn back and moves the sheet with it', async () => {
    reverts('cp-before', 5, 'cp-after');
    useChat.setState({
      messages: [
        message({ checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-before' }] }),
      ],
    });
    await useChat.getState().undoTurn('a1');

    reverts('cp-after', 6, 'cp-before-again');
    await useChat.getState().redoTurn('a1');

    expect(revertToCheckpoint).toHaveBeenLastCalledWith('doc-1', 'cp-after');
    expect(useChat.getState().messages[0].reverted).toBe(false);
    expect(useStudio.getState().version).toBe(6);
    // As with undo: the server document moves too, or the next render rebuilds
    // from a stale base and the redo looks like it never happened.
    expect(useStudio.getState().serverDoc).toBe(useStudio.getState().doc);
  });

  it('can be pressed back and forth rather than once each', async () => {
    // Each press records its own way back, so the pair is a switch. Written as
    // two separate actions they drifted immediately: the first redo forgot to
    // record where it came from, and a turn could be put back exactly once
    // before both buttons stopped doing anything.
    reverts('cp-before', 5, 'cp-after');
    useChat.setState({
      messages: [
        message({ checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-before' }] }),
      ],
    });

    await useChat.getState().undoTurn('a1');
    reverts('cp-after', 6, 'cp-before-2');
    await useChat.getState().redoTurn('a1');

    expect(useChat.getState().messages[0].checkpoints).toEqual([
      { boardId: 'doc-1', checkpointId: 'cp-before-2' },
    ]);

    reverts('cp-before-2', 7, 'cp-after-2');
    await useChat.getState().undoTurn('a1');

    expect(useChat.getState().messages[0].reverted).toBe(true);
    expect(useChat.getState().messages[0].redo).toEqual([
      { boardId: 'doc-1', checkpointId: 'cp-after-2' },
    ]);
  });

  it('does not unmark the turn when the redo failed', async () => {
    // The mirror of the same rule on undo: a sheet still showing the reverted
    // state under a message that no longer says so is worse than no mark.
    useChat.setState({
      messages: [
        message({
          reverted: true,
          redo: [{ boardId: 'doc-1', checkpointId: 'cp-after' }],
        }),
      ],
    });
    vi.mocked(revertToCheckpoint).mockRejectedValue(new Error('409: gone'));

    await useChat.getState().redoTurn('a1');

    expect(useChat.getState().messages[0].reverted).toBe(true);
  });

  it('puts back every board the turn reached', async () => {
    // A turn can move between versions. Putting back only the one on screen
    // would leave the other quietly reverted.
    vi.mocked(revertToCheckpoint).mockImplementation(
      async (id, checkpoint) =>
        ({
          id,
          title: 'R',
          version: 6,
          hash: 'h6',
          doc: DOC,
          updated_at: '',
          redo_checkpoint: `back-${checkpoint}`,
        }) as never
    );
    useChat.setState({
      messages: [
        message({
          reverted: true,
          redo: [
            { boardId: 'doc-1', checkpointId: 'cp-a' },
            { boardId: 'doc-2', checkpointId: 'cp-b' },
          ],
        }),
      ],
    });

    await useChat.getState().redoTurn('a1');

    expect(vi.mocked(revertToCheckpoint).mock.calls).toEqual([
      ['doc-1', 'cp-a'],
      ['doc-2', 'cp-b'],
    ]);
  });
});

describe('the control', () => {
  it('offers redo exactly where undo is no longer the thing to offer', () => {
    const { container } = render(
      <Revision
        message={message({
          reverted: true,
          redo: [{ boardId: 'doc-1', checkpointId: 'cp-after' }],
          checkpoints: [{ boardId: 'doc-1', checkpointId: 'cp-before' }],
        })}
      />
    );
    expect(screen.queryByText('Undo this turn')).toBeNull();
    expect(container.textContent).toContain('Redo this turn');
  });

  it('offers neither on a turn that changed nothing', () => {
    const { container } = render(<Revision message={message()} />);
    expect(container.textContent).not.toContain('Undo this turn');
    expect(container.textContent).not.toContain('Redo this turn');
  });

  it('says so when the turn reached more than one version', () => {
    const { container } = render(
      <Revision
        message={message({
          reverted: true,
          redo: [
            { boardId: 'doc-1', checkpointId: 'cp-a' },
            { boardId: 'doc-2', checkpointId: 'cp-b' },
          ],
        })}
      />
    );
    expect(container.textContent).toContain('Redo this turn everywhere');
  });
});
