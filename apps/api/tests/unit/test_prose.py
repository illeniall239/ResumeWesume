"""Keeping the assistant's two thoughts apart.

A turn usually speaks twice -- once before it starts working, once after. The
stream carries those as separate runs of text with nothing between them, so
appended straight on they collided mid-sentence:

    I'll update your contact info with the phone number and email you
    provided.Your contact info is updated -- phone is now ...
"""

from __future__ import annotations

from studio.agent.prose import ProseStream
from studio.streaming import events as ev


def collect() -> tuple[ProseStream, list[str]]:
    said: list[str] = []
    stream = ProseStream(lambda event: said.append(event.text))
    return stream, said


def rendered(said: list[str]) -> str:
    return "".join(said)


class TestParagraphs:
    def test_two_utterances_are_two_paragraphs(self) -> None:
        stream, said = collect()

        stream.new_utterance()
        stream.say("I'll update your contact info.")
        stream.new_utterance()
        stream.say("Your contact info is updated.")

        assert rendered(said) == (
            "I'll update your contact info.\n\nYour contact info is updated."
        )

    def test_streaming_fragments_stay_one_paragraph(self) -> None:
        """Text arrives token by token; those are not separate thoughts."""
        stream, said = collect()

        stream.new_utterance()
        for fragment in ("I'll ", "update ", "your ", "contact info."):
            stream.say(fragment)

        assert rendered(said) == "I'll update your contact info."

    def test_a_turn_never_opens_with_a_blank_line(self) -> None:
        """The first utterance is marked like any other."""
        stream, said = collect()

        stream.new_utterance()
        stream.say("Working on it.")

        assert rendered(said) == "Working on it."
        assert not said[0].startswith("\n")

    def test_a_silent_round_leaves_no_trailing_blank(self) -> None:
        """Most turns end with work rather than a farewell.

        Marking the boundary eagerly would leave an empty line hanging under the
        last paragraph of nearly every reply.
        """
        stream, said = collect()

        stream.new_utterance()
        stream.say("Tightening that bullet.")
        stream.new_utterance()  # a round that only called tools

        assert rendered(said) == "Tightening that bullet."

    def test_three_utterances(self) -> None:
        stream, said = collect()

        for text in ("First.", "Second.", "Third."):
            stream.new_utterance()
            stream.say(text)

        assert rendered(said) == "First.\n\nSecond.\n\nThird."

    def test_empty_text_is_not_an_utterance(self) -> None:
        stream, said = collect()

        stream.new_utterance()
        stream.say("")
        stream.say("Only this.")

        assert rendered(said) == "Only this."
        assert stream.said_anything

    def test_it_emits_assistant_deltas(self) -> None:
        events: list[ev.Event] = []
        stream = ProseStream(events.append)
        stream.say("hello")

        assert isinstance(events[0], ev.AssistantDelta)
