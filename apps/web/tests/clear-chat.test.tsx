/**
 * Emptying the conversation.
 *
 * The one irreversible act in the studio. Everything else here is an op with
 * an inverse or a turn with a checkpoint, and the app's habit is to do the
 * thing and offer the way back afterwards — which is why this one is the
 * exception that asks first. There is nothing to restore a transcript from.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, clearCanvasMessages: vi.fn().mockResolvedValue(undefined) };
});

import { clearCanvasMessages } from '@/lib/api';
import { ClearChat } from '@/chat/clear-chat';
import { useChat, type ChatMessage } from '@/store/chat';

const said: ChatMessage[] = [
  { id: 'u1', role: 'user', text: 'tighten that bullet', activity: [] },
  { id: 'a1', role: 'assistant', text: 'Done.', activity: [], status: 'ok' },
];

beforeEach(() => {
  vi.clearAllMocks();
  useChat.setState({ messages: said, streaming: false, error: null, confirm: null });
});

describe('the offer', () => {
  it('is not made when there is nothing to clear', () => {
    // An empty conversation already says what to do with it, and a control
    // that empties nothing is a control that appears broken.
    useChat.setState({ messages: [] });
    const { container } = render(<ClearChat canvasId="cnv_1" />);
    expect(container.firstChild).toBeNull();
  });

  it('says what it will and will not touch', () => {
    render(<ClearChat canvasId="cnv_1" />);
    const button = screen.getByRole('button', { name: 'Clear chat' });
    expect(button.getAttribute('title')).toContain('résumé is not touched');
  });

  it('stands down while a turn is in flight', async () => {
    // Clearing cancels the turn, and being cancelled by a button labelled
    // "clear chat" is not what anybody pressing it meant. Stop is on the bar
    // for that.
    useChat.setState({ streaming: true });
    render(<ClearChat canvasId="cnv_1" />);

    const button = screen.getByRole<HTMLButtonElement>('button', { name: 'Clear chat' });
    expect(button.disabled).toBe(true);

    fireEvent.click(button);
    expect(screen.queryByText('Clear the conversation?')).toBeNull();
  });
});

describe('the question', () => {
  it('is asked before anything is deleted', async () => {
    render(<ClearChat canvasId="cnv_1" />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Clear chat' }));
    });

    expect(screen.getByText('Clear the conversation?')).toBeTruthy();
    expect(clearCanvasMessages).not.toHaveBeenCalled();
    expect(useChat.getState().messages).toHaveLength(2);
  });

  it('leaves everything alone when the answer is Keep', async () => {
    render(<ClearChat canvasId="cnv_1" />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Clear chat' }));
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Keep' }));
    });

    expect(clearCanvasMessages).not.toHaveBeenCalled();
    expect(useChat.getState().messages).toHaveLength(2);
    // And back to the offer, rather than to nothing: the question was
    // answered, not abandoned.
    expect(screen.getByRole('button', { name: 'Clear chat' })).toBeTruthy();
  });

  it('empties the conversation, on the server too, when the answer is Clear', async () => {
    render(<ClearChat canvasId="cnv_1" />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Clear chat' }));
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    });

    expect(useChat.getState().messages).toEqual([]);
    // On the server as well, or it comes back on the next page load and the
    // control reads as having done nothing.
    expect(clearCanvasMessages).toHaveBeenCalledWith('cnv_1');
  });

  it('clears on screen even when the server refuses', async () => {
    // The delete resurfaces on reload rather than leaving somebody staring at
    // a conversation they asked to be rid of.
    vi.mocked(clearCanvasMessages).mockRejectedValueOnce(new Error('offline'));
    render(<ClearChat canvasId="cnv_1" />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Clear chat' }));
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    });

    expect(useChat.getState().messages).toEqual([]);
  });
});
