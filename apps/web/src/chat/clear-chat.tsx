/**
 * Emptying the conversation.
 *
 * Its own component rather than markup in the studio page, because it holds a
 * question and its two answers: three states of its own inside a component
 * that already draws the canvas, and reachable in a test without rendering
 * the whole studio around it.
 *
 * Asked before it happens, which almost nothing else in this app is. The
 * general rule here is to act and offer the way back afterwards, because the
 * way back exists -- every edit is an op with an inverse, and every turn is a
 * checkpoint. The transcript has neither. It is the only record of what was
 * said, there is nothing to restore it from, and the résumé it discusses is
 * not touched either way.
 */

import { useState } from 'react';

import { useChat } from '@/store/chat';

export function ClearChat({ canvasId }: { canvasId: string }) {
  const forget = useChat((state) => state.forget);
  // Booleans, not the arrays behind them. This sits in the studio's top bar,
  // and a subscription to `messages` there would redraw the bar on every
  // streamed token.
  const any = useChat((state) => state.messages.length > 0);
  const streaming = useChat((state) => state.streaming);
  const [asking, setAsking] = useState(false);

  // Nothing to clear, nothing to offer. The empty conversation already says
  // what to do with it.
  if (!any) return null;

  if (asking) {
    return (
      <span className="rail__ask">
        Clear the conversation?
        <button
          type="button"
          className="rail__answer rail__answer--go"
          onClick={() => {
            setAsking(false);
            void forget(canvasId);
          }}
        >
          Clear
        </button>
        <button
          type="button"
          className="rail__answer"
          onClick={() => setAsking(false)}
        >
          Keep
        </button>
      </span>
    );
  }

  return (
    <button
      type="button"
      className="rail__clear"
      onClick={() => setAsking(true)}
      // A turn in flight is cancelled by clearing, and being cancelled by a
      // button labelled "clear chat" is not what anybody pressing it meant.
      // Stop is already on the bar for that.
      disabled={streaming}
      title={
        streaming
          ? 'Wait for the current turn to finish'
          : 'Delete this conversation. The résumé is not touched.'
      }
    >
      Clear chat
    </button>
  );
}

export default ClearChat;
