'use client';

import { useEffect, useRef, useState } from 'react';

import { useChat, type ChatMessage, type ToolActivity } from '@/store/chat';

const SUGGESTIONS = [
  'Tighten every bullet in my most recent job',
  'Make my summary punchier',
  'Add Rust to my skills',
];

/** A tool call, rendered as it happens. */
function ActivityCard({ item }: { item: ToolActivity }) {
  const glyph =
    item.status === 'applied'
      ? '✓'
      : item.status === 'rejected'
        ? '!'
        : item.status === 'confirm'
          ? '?'
          : '·';

  return (
    <div className={`activity activity--${item.status}`}>
      <span className="activity__glyph">{glyph}</span>
      <span className="activity__body">
        <span className="activity__name">{item.label || item.name.replace(/_/g, ' ')}</span>
        {item.detail && <span className="activity__detail">{item.detail}</span>}
        {item.code && <code className="activity__code">{item.code}</code>}
      </span>
    </div>
  );
}

function Message({ message, showThinking }: { message: ChatMessage; showThinking: boolean }) {
  if (message.role === 'user') {
    return (
      <div className="msg msg--user">
        <div className="msg__body">{message.text}</div>
      </div>
    );
  }

  const idle = message.status === 'streaming' && !message.text && !message.activity.length;

  return (
    <div className="msg msg--assistant">
      {showThinking && message.thinking && (
        <details className="thinking">
          <summary>Reasoning</summary>
          <pre>{message.thinking}</pre>
        </details>
      )}
      {message.text && <div className="msg__body">{message.text}</div>}
      {idle && <div className="msg__body msg__body--muted">Thinking…</div>}
      {message.activity.length > 0 && (
        <div className="activities">
          {message.activity.map((item) => (
            <ActivityCard key={item.callId} item={item} />
          ))}
        </div>
      )}
      {message.status === 'cancelled' && <div className="msg__status">Stopped.</div>}
      {message.status === 'partial' && (
        <div className="msg__status">Some edits could not be applied.</div>
      )}
    </div>
  );
}

export function ChatPanel({ documentId }: { documentId: string }) {
  const messages = useChat((state) => state.messages);
  const streaming = useChat((state) => state.streaming);
  const error = useChat((state) => state.error);
  const confirm = useChat((state) => state.confirm);
  const showThinking = useChat((state) => state.showThinking);
  const send = useChat((state) => state.send);
  const cancel = useChat((state) => state.cancel);
  const toggleThinking = useChat((state) => state.toggleThinking);
  const approveConfirm = useChat((state) => state.approveConfirm);
  const dismissConfirm = useChat((state) => state.dismissConfirm);

  const [draft, setDraft] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  const submit = () => {
    if (!draft.trim() || streaming) return;
    send(documentId, draft.trim());
    setDraft('');
  };

  return (
    <div className="chat">
      <div className="toolbar">
        <strong>Assistant</strong>
        <span className="toolbar__spacer" />
        <button className="link" onClick={toggleThinking} type="button">
          {showThinking ? 'Hide reasoning' : 'Show reasoning'}
        </button>
      </div>

      <div className="chat__scroll">
        {messages.length === 0 && (
          <div className="empty">
            <p>Ask for a change and watch it land in the document.</p>
            <ul className="suggestions">
              {SUGGESTIONS.map((suggestion) => (
                <li key={suggestion}>
                  <button
                    className="chip"
                    type="button"
                    onClick={() => send(documentId, suggestion)}
                  >
                    {suggestion}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {messages.map((message) => (
          <Message key={message.id} message={message} showThinking={showThinking} />
        ))}

        {error && <div className="notice notice--error">{error}</div>}

        {confirm && (
          <div className="confirm">
            <strong>Confirm this change</strong>
            <p>{confirm.risk}</p>
            <pre>{JSON.stringify(confirm.args, null, 2)}</pre>
            <div className="confirm__actions">
              <button
                className="button"
                type="button"
                onClick={() => approveConfirm(documentId)}
              >
                Yes, do it
              </button>
              <button className="button" type="button" onClick={dismissConfirm}>
                No
              </button>
            </div>
          </div>
        )}

        <div ref={endRef} />
      </div>

      <div className="composer">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line. A resume instruction is
            // almost always one line, so this is the right default.
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="Tell the assistant what to change…"
          rows={3}
          disabled={streaming}
        />
        {streaming ? (
          <button className="button" type="button" onClick={cancel}>
            Stop
          </button>
        ) : (
          <button
            className="button"
            type="button"
            onClick={submit}
            disabled={!draft.trim()}
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}

export default ChatPanel;
