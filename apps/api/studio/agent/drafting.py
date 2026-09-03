"""Reading a half-written tool call, so the page can show the work happening.

A tool call arrives as a stream of JSON fragments:

    {"nid": "blt_9c21x", "value": "Cut settlement laten

By the time it balances, the edit lands all at once. Everything needed to show
it arriving was already on the wire a second earlier -- which node, and how much
of the new text has been written -- and this is the part that reads it out.

**Nothing here decides anything.** It reports a target and a partial string; the
document is not touched, no op is compiled, no version moves. A draft is a
picture of a call in flight, and a call that never balances leaves the document
exactly as it was.

The parsing is deliberately hand-rolled rather than a tolerant JSON library. The
input is *known* to be truncated -- that is the whole point -- so there is no
document to recover, only two fields to find, and a parser that returns
something plausible for the rest would be inventing text nobody wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Where the text of an edit lives, in the order to prefer them. ``value`` is
#: the common one; ``text`` is what the bullet tools use.
_TEXT_KEYS = ("value", "text")

#: What the edit is aimed at. ``target`` carries a field path
#: (``personal.phone``), ``nid`` a plain node id.
_TARGET_KEYS = ("nid", "target")

#: Matches a completed `"key": "…"` pair, which is how a target arrives: short,
#: and finished long before the text it precedes.
_COMPLETE = re.compile(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*)"')

#: Matches the *last* `"key": "` in the buffer with no closing quote yet -- the
#: string still being written.
#:
#: The trailing `\\?` matters. A fragment can end on the first half of an escape
#: sequence, and without it the pattern matched nothing at all -- so the draft
#: disappeared for that frame and the line being written blinked back to its old
#: text before carrying on.
_OPEN = re.compile(r'"(\w+)"\s*:\s*"((?:[^"\\]|\\.)*\\?)$')


@dataclass(frozen=True)
class Draft:
    """What a call in flight is about to say."""

    #: The node or field path being written to.
    target: str
    #: As much of the new text as has arrived.
    text: str


def _unescape(raw: str) -> str:
    """Undo JSON string escaping on a fragment that may end mid-escape.

    A trailing lone backslash is dropped rather than guessed at: it is the
    first half of an escape whose second half has not arrived, and rendering it
    would put a stray character on the page for one frame.
    """
    out: list[str] = []
    index = 0
    while index < len(raw):
        character = raw[index]
        if character != "\\":
            out.append(character)
            index += 1
            continue

        if index + 1 >= len(raw):
            break  # dangling escape: the rest is still in flight

        following = raw[index + 1]
        out.append(
            {
                "n": "\n",
                "t": "\t",
                "r": "\r",
                '"': '"',
                "\\": "\\",
                "/": "/",
            }.get(following, following)
        )
        index += 2
    return "".join(out)


def read(buffer: str, name: str = "") -> Draft | None:
    """The draft a partial argument buffer describes, if it describes one yet.

    Returns None until both a target and the beginning of its text have
    arrived. Nothing is shown for a call whose arguments are still only a node
    id, because a cleared line that then refills reads as a deletion.

    Also None for a call that *adds* something. An inserted bullet has no
    element on the page to type into, and the honest picture of an insertion is
    the line appearing whole when it lands.

    ``name`` is the tool being called, needed only where the arguments do not
    name their own target: ``set_personal_info`` says ``field: "phone"`` and
    means ``personal.phone``, because there is no node id for a contact detail.
    """
    if not buffer:
        return None

    pairs = {key: value for key, value in _COMPLETE.findall(buffer)}

    target = next((pairs[key] for key in _TARGET_KEYS if pairs.get(key)), "")
    field = pairs.get("field", "")

    # A field edit addresses `nid.field`, which is how the page draws it. Keyed
    # on the bare nid the draft pointed at an element that renders the entry as
    # a whole, so nothing appeared.
    if target and field and "." not in target:
        target = f"{target}.{field}"
    elif not target and field and name.endswith("personal_info"):
        target = f"personal.{field}"

    if not target:
        return None

    # A finished string first: the text is complete when the fragment after it
    # is still arriving, which happens whenever another argument follows.
    for key in _TEXT_KEYS:
        if key in pairs:
            return Draft(target=target, text=pairs[key])

    open_match = _OPEN.search(buffer)
    if open_match and open_match.group(1) in _TEXT_KEYS:
        text = _unescape(open_match.group(2))
        return Draft(target=target, text=text) if text else None

    return None
