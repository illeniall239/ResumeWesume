/**
 * What the sidebar shows for a turn that happened before this page load.
 *
 * The conversation is stored on the server, and it used to be stored narrowly:
 * role, text, status. So a reload left every past turn as a bare paragraph --
 * the reasoning gone, every tool call gone -- and the record of *how* the
 * résumé came to say what it says lasted only as long as the tab.
 *
 * These pin the mapping, which is the seam: the server sends the settled shape
 * of a finished turn, and the sidebar has to read it as the same thing the
 * reducer builds live, because it renders both through one component.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, fetchCanvasMessages: vi.fn() };
});

import { fetchCanvasMessages, type StoredMessage } from '@/lib/api';
import { useChat } from '@/store/chat';

const stored = (over: Partial<StoredMessage> = {}): StoredMessage =>
  ({
    id: '2',
    role: 'assistant',
    text: 'Tightened it.',
    status: 'ok',
    thinking: 'The second bullet is the vague one.',
    activity: [
      {
        call_id: 'call_0',
        name: 'rewrite_text',
        tier: 'A',
        status: 'applied',
        label: 'rewrote text',
        touched: ['blt_9n2wb'],
      },
      {
        call_id: 'note_12',
        name: 'quality',
        tier: 'A',
        status: 'note',
        detail: 'new figures appeared that were not in the original: 96%',
      },
    ],
    ...over,
  }) as StoredMessage;

async function loadWith(messages: StoredMessage[]) {
  vi.mocked(fetchCanvasMessages).mockResolvedValue({ messages });
  await useChat.getState().load('cnv_1');
  return useChat.getState().messages;
}

beforeEach(() => {
  vi.clearAllMocks();
  useChat.setState({ messages: [], streaming: false });
});

describe('a turn read back from the server', () => {
  it('brings its reasoning with it', async () => {
    const [message] = await loadWith([stored()]);
    expect(message.thinking).toBe('The second bullet is the vague one.');
  });

  it('brings its tool calls, in the order they ran', async () => {
    const [message] = await loadWith([stored()]);
    expect(message.activity.map((item) => item.name)).toEqual([
      'rewrite_text',
      'quality',
    ]);
  });

  it('keeps each call in the state it finished in', async () => {
    // Not the state it started in. A call left "running" in a transcript reads
    // as a turn that never came back, and the spinner beside it never stops.
    const [message] = await loadWith([stored()]);
    const [call, note] = message.activity;
    expect(call.status).toBe('applied');
    expect(call.touched).toEqual(['blt_9n2wb']);
    // An advisory note is not a failure: the engine reported rather than
    // refused, and the edit landed. Marked rejected it would say the opposite.
    expect(note.status).toBe('note');
    expect(note.detail).toContain('96%');
  });

  it('reads the server\'s snake_case into the shape the reducer builds', async () => {
    // One component draws a live turn and a restored one, so a `call_id` that
    // never became a `callId` would come out as a row keyed `undefined` --
    // which React renders once and then reuses for the next one.
    const [message] = await loadWith([stored()]);
    expect(message.activity.map((item) => item.callId)).toEqual([
      'call_0',
      'note_12',
    ]);
    expect(message.activity.every((item) => 'call_id' in item)).toBe(false);
  });
});

describe('a turn stored before any of this existed', () => {
  it('shows no tool calls rather than crashing on the missing field', async () => {
    // Null, not empty: the row genuinely does not know what its tools did. The
    // sidebar has to draw it as a turn with nothing recorded, and every reader
    // of `activity` assumes an array.
    const [message] = await loadWith([stored({ thinking: null, activity: null })]);
    expect(message.activity).toEqual([]);
    expect(message.thinking).toBeUndefined();
  });
});
