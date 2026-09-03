/**
 * How an advisory line is drawn.
 *
 * The engine reports rather than refuses: it applies a large rewrite and then
 * says so, instead of blocking it. Every one of those lines arrives as a
 * `warning` event, and they were all mapped to `status: 'rejected'` -- the same
 * cross as an edit that failed. So the work landed and the sidebar said it had
 * not, which undoes the whole point of reporting.
 *
 * The model line was worse. "claude-sonnet-5" is not a statement about the
 * resume at all, and it wore the failure mark too.
 */

import { describe, expect, it } from 'vitest';

import { applyEvent, type ChatMessage } from '@/store/chat';

/** Run one event through the reducer and return the message it produced. */
function reduce(event: Record<string, unknown>): ChatMessage {
  let message: ChatMessage = { id: 'a1', role: 'assistant', text: '', activity: [] };
  const patch = (change: (current: ChatMessage) => ChatMessage) => {
    message = change(message);
  };
  applyEvent(
    event as never,
    patch as never,
    (() => {}) as never,
    'a1'
  );
  return message;
}

describe('advisory notices', () => {
  it('a guard notice is a note, not a rejection', () => {
    const message = reduce({
      type: 'warning',
      seq: 7,
      source: 'scale',
      message: 'This turn rewrote 150% of the resume rather than editing part of it.',
    });

    expect(message.activity).toHaveLength(1);
    expect(message.activity[0].status).toBe('note');
    expect(message.activity[0].detail).toContain('150%');
  });

  it('grounding and identity notices are notes too', () => {
    for (const source of ['grounding', 'identity', 'quality']) {
      const message = reduce({ type: 'warning', seq: 1, source, message: 'read this' });
      expect(message.activity[0].status).toBe('note');
    }
  });

  it('the model goes on the message and not into the activity list', () => {
    const message = reduce({
      type: 'warning',
      seq: 2,
      source: 'model',
      message: 'claude-sonnet-5',
    });

    expect(message.model).toBe('claude-sonnet-5');
    // The bug: rendered as activity it took the same mark as a failed edit.
    expect(message.activity).toHaveLength(0);
  });
});
