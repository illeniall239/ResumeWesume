/**
 * What stays in the bubble once a turn is over.
 *
 * The sidebar holds the conversation; the revision block holds the document's
 * record -- same number as the mark on the sheet, with the outgoing and
 * incoming text in full. Listing every applied edit in both made the sidebar a
 * worse copy of a better record, and buried the rows that have no other home.
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Revision } from '@/chat/chat-panel';
import type { ChatMessage } from '@/store/chat';

function message(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'a1',
    role: 'assistant',
    text: 'Done.',
    activity: [
      {
        callId: 'c1',
        name: 'rewrite_text',
        tier: 'A',
        status: 'applied',
        label: 'rewrote a bullet',
      },
      {
        callId: 'c2',
        name: 'add_skill',
        tier: 'B',
        status: 'rejected',
        detail: 'Python is already in technical.',
      },
      {
        callId: 'c3',
        name: 'scale',
        tier: 'A',
        status: 'note',
        detail: 'This turn rewrote 150% of the resume.',
      },
    ],
    ...over,
  };
}

describe('the steps a finished turn keeps', () => {
  it('drops applied edits, which the revision block already records', () => {
    render(<Revision message={message({ status: 'ok' })} />);

    expect(screen.queryByText(/rewrote a bullet/)).toBeNull();
  });

  it('keeps a refusal, which has no other home', () => {
    render(<Revision message={message({ status: 'ok' })} />);

    expect(screen.getByText(/already in technical/)).toBeTruthy();
  });

  it('keeps an advisory note', () => {
    render(<Revision message={message({ status: 'ok' })} />);

    expect(screen.getByText(/rewrote 150%/)).toBeTruthy();
  });

  it('shows everything while the turn is still running', () => {
    // A bubble that sat blank for thirty seconds would read as a hang.
    render(
      <Revision
        message={message({ status: 'streaming' })}
       
       
      />
    );

    expect(screen.getByText(/rewrote a bullet/)).toBeTruthy();
  });
});

describe('reasoning', () => {
  it('is shown without a setting, and starts closed', () => {
    // It used to need a header toggle: a setting to find, and to remember
    // having found. A closed disclosure says it is there and costs one line.
    const { container } = render(
      <Revision message={message({ status: 'ok', thinking: 'weighing it up' })} />
    );

    const details = container.querySelector('details.thinking');
    expect(details).toBeTruthy();
    expect((details as HTMLDetailsElement).open).toBe(false);
    expect(screen.getByText('Reasoning')).toBeTruthy();
  });

  it('is absent when the model returned none', () => {
    const { container } = render(<Revision message={message({ status: 'ok' })} />);
    expect(container.querySelector('details.thinking')).toBeNull();
  });
});
