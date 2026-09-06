"""Which version an undo or a redo should reverse.

Undo needs no new state. Every applied op is already logged with the version it
produced and its own inverse (``document_ops``), and one ``POST /ops`` is one
version, so **a version is exactly one undo unit**. What is left is bookkeeping:
deciding which version to reverse next, given that undos and redos are
themselves versions in the same log.

That decision is a pure function over the actors of the version groups, kept
here rather than inline in the repository because it is the part with edge
cases -- undo after redo after undo -- and the part worth testing without a
database.

The trick that keeps it small: **redo is just "undo the undo"**. An undo writes
real ops, so those ops have inverses too, and reversing an undo restores what it
removed. Both operations are therefore "reverse version V", and only the choice
of V differs.
"""

from __future__ import annotations

from typing import Iterable, Literal, NamedTuple

Actor = Literal["user", "agent", "undo", "redo", "revert", "layout", "system"]

#: Versions that are somebody's work, and so are what an undo reverses.
#:
#: Everything else is the engine writing down something it derived. Two of
#: those exist and both used to be counted here, with the same consequence:
#: the chooser aimed an undo at a version the person never made.
#:
#: ``layout`` is the client correcting frame heights from what the browser
#: measured. It lands after almost every edit that changes how much room a
#: line takes, so counting it cost a press per edit -- the first Ctrl+Z moved
#: geometry nobody could see and looked like a dead key -- and worse, arriving
#: after an undo it read as a fresh edit and cleared the redo stack.
#:
#: ``system`` is a drift guard rewriting the document wholesale after a turn.
#: It cannot be inverted op by op, so aiming an undo at it made the reversal
#: refuse; and since the chooser is deterministic it refused every time after,
#: which left the document permanently unable to undo anything at all.
_EDITS = frozenset({"user", "agent", "revert"})


class VersionGroup(NamedTuple):
    """One committed batch: the version it produced, and who produced it."""

    version: int
    actor: str


def _descending(groups: Iterable[VersionGroup]) -> list[VersionGroup]:
    return sorted(groups, key=lambda group: group.version, reverse=True)


def version_to_undo(groups: Iterable[VersionGroup]) -> int | None:
    """The most recent edit that is not already reversed.

    Walking newest-first, an undo shields the edit below it and a redo cancels
    an undo. Balancing those two counters is what makes repeated undo/redo
    sequences land on the right version instead of drifting.
    """
    redos = 0
    shielded = 0

    for group in _descending(groups):
        if group.actor == "redo":
            redos += 1
        elif group.actor == "undo":
            if redos > 0:
                redos -= 1          # this undo was already cancelled by a redo
            else:
                shielded += 1       # it shields one real edit below
        elif group.actor not in _EDITS:
            continue                # derived, not done: see `_EDITS`
        else:
            if shielded > 0:
                shielded -= 1
            else:
                return group.version
    return None


def version_to_redo(groups: Iterable[VersionGroup]) -> int | None:
    """The most recent undo that has not been redone.

    Returns None as soon as a real edit is met, which is what gives the
    conventional behaviour that **making a new edit clears the redo stack**.
    Without that, redo would resurrect work from before the branch and silently
    overwrite what the user just did.

    A *derived* version is not a new edit and must not clear anything. The
    layout pass commits one after almost every undo -- the text changed back,
    so the frames need re-measuring -- and counting that as a branch killed
    redo on the press immediately after every undo.
    """
    redos = 0

    for group in _descending(groups):
        if group.actor == "redo":
            redos += 1
        elif group.actor == "undo":
            if redos > 0:
                redos -= 1
            else:
                return group.version
        elif group.actor not in _EDITS:
            continue                # derived, not done: see `_EDITS`
        else:
            return None
    return None
