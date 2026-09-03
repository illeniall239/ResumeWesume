"""Assistant prose, with its utterances kept apart.

A turn usually speaks more than once: something before it starts working, and
something after it has finished. Those are separate utterances, and the stream
carries them as separate runs of text with nothing between them -- so appended
straight to the last one they collide mid-sentence:

    I'll update your contact info with the phone number and email you
    provided.Your contact info is updated -- phone is now ...

Two thoughts, one paragraph, no space. The fix is a blank line at the boundary
between utterances, which is what every chat interface does and what markdown
needs to render them as two paragraphs.

The boundary is *not* every chunk. Text arrives token by token and those
fragments are one utterance; the break belongs where the assistant stopped to do
something and came back -- a new round on the local path, a new assistant
message from the Agent SDK. Callers mark it; this decides whether it is needed.
"""

from __future__ import annotations

from typing import Callable

from studio.streaming import events as ev


class ProseStream:
    """Emits assistant text, inserting a break between utterances.

    ``emit`` rather than a channel because the two callers reach the channel
    differently -- one directly, one marshalled back to the server loop from the
    Agent SDK's thread.
    """

    def __init__(self, emit: Callable[[ev.Event], None]) -> None:
        self._emit = emit
        self._said = False
        self._break_pending = False

    def say(self, text: str) -> None:
        """Emit a fragment, opening a new paragraph if one is due."""
        if not text:
            return

        # Only between utterances, never before the first: a turn that opens
        # with a break would render with an empty line above its first word.
        if self._break_pending and self._said:
            self._emit(ev.AssistantDelta(text="\n\n"))
        self._break_pending = False

        self._emit(ev.AssistantDelta(text=text))
        self._said = True

    def new_utterance(self) -> None:
        """The assistant stopped to do something; what follows is a new thought.

        Deferred rather than emitted here, because a turn often ends without
        speaking again -- marking eagerly would leave a trailing blank line
        under the last paragraph of most replies.
        """
        self._break_pending = True

    @property
    def said_anything(self) -> bool:
        return self._said
