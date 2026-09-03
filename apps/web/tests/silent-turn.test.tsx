/**
 * A turn that brings back nothing has to say so.
 *
 * The report this pins, in the user's words: "I tried [an instruction] — 1 did
 * nothing." They were right. The model spent its whole generation budget inside
 * its reasoning block and emitted neither a reply nor a tool call, so the
 * schedule rendered a revision number with blank space under it.
 *
 * Where the provider says it was cut off, the server now emits a `truncated`
 * warning and that arrives as its own row. This covers the remaining case: a
 * turn that ended normally and still produced nothing.
 */

import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import ChatPanel from '@/chat/chat-panel';
import { useChat } from '@/store/chat';

function transcript(status: 'ok' | 'streaming' | 'cancelled', text = '', activity = []) {
  useChat.setState({
    messages: [
      { id: 'u1', role: 'user', text: 'tailor this for an AI engineer', activity: [] },
      { id: 'a1', role: 'assistant', text, activity, status },
    ],
    streaming: status === 'streaming',
    error: null,
    confirm: null,
  });
}

afterEach(() => {
  cleanup();
  useChat.setState({ messages: [] });
});

describe('a turn that returned nothing', () => {
  it('explains itself rather than rendering an empty entry', () => {
    transcript('ok');
    render(<ChatPanel documentId="doc_1" />);
    expect(screen.getByText(/returned nothing/i)).toBeInTheDocument();
  });

  it('says nothing of the sort while the turn is still running', () => {
    transcript('streaming');
    render(<ChatPanel documentId="doc_1" />);
    expect(screen.queryByText(/returned nothing/i)).toBeNull();
    expect(screen.getByText(/Working/i)).toBeInTheDocument();
  });

  it('stays quiet when the turn produced a reply', () => {
    transcript('ok', 'I tightened both bullets.');
    render(<ChatPanel documentId="doc_1" />);
    expect(screen.queryByText(/returned nothing/i)).toBeNull();
  });

  it('stays quiet when the user stopped it themselves', () => {
    transcript('cancelled');
    render(<ChatPanel documentId="doc_1" />);
    expect(screen.queryByText(/returned nothing/i)).toBeNull();
    expect(screen.getByText(/Stopped/i)).toBeInTheDocument();
  });
});
