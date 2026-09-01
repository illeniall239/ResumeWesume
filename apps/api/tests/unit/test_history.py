"""Choosing which version an undo or redo reverses.

Pure bookkeeping over the op log's version groups, so it is tested without a
database. The sequences below are the ones that go wrong in practice: undo
after redo, redo after a fresh edit, and long alternating runs.
"""

from __future__ import annotations

from studio.doc.history import VersionGroup, version_to_redo, version_to_undo


def log(*actors: str) -> list[VersionGroup]:
    """Version groups numbered from 1, in the order they were committed."""
    return [VersionGroup(index + 1, actor) for index, actor in enumerate(actors)]


class TestUndo:
    def test_nothing_to_undo_on_an_empty_log(self) -> None:
        assert version_to_undo([]) is None

    def test_undoes_the_latest_edit(self) -> None:
        assert version_to_undo(log("user", "user")) == 2

    def test_a_second_undo_reaches_the_edit_before(self) -> None:
        # v3 undid v2, so the next undo must reach v1 rather than repeating v2.
        assert version_to_undo(log("user", "user", "undo")) == 1

    def test_undoes_an_agent_turn_like_any_other_edit(self) -> None:
        assert version_to_undo(log("user", "agent")) == 2

    def test_everything_undone_leaves_nothing(self) -> None:
        assert version_to_undo(log("user", "user", "undo", "undo")) is None

    def test_a_redo_puts_an_edit_back_within_reach(self) -> None:
        # v1 v2, undo v2, redo it. The next undo should target v2 again.
        assert version_to_undo(log("user", "user", "undo", "redo")) == 2

    def test_a_long_alternating_sequence_lands_correctly(self) -> None:
        # user user | undo(v2) undo(v1) | redo(v1) redo(v2)
        # Both edits are back, so the next undo is v2.
        assert version_to_undo(log("user", "user", "undo", "undo", "redo", "redo")) == 2

    def test_an_edit_after_an_undo_is_undone_first(self) -> None:
        assert version_to_undo(log("user", "user", "undo", "user")) == 4


class TestRedo:
    def test_nothing_to_redo_on_an_empty_log(self) -> None:
        assert version_to_redo([]) is None

    def test_nothing_to_redo_before_any_undo(self) -> None:
        assert version_to_redo(log("user", "user")) is None

    def test_redoes_the_latest_undo(self) -> None:
        assert version_to_redo(log("user", "user", "undo")) == 3

    def test_a_second_redo_reaches_the_undo_before(self) -> None:
        assert version_to_redo(log("user", "user", "undo", "undo", "redo")) == 3

    def test_everything_redone_leaves_nothing(self) -> None:
        assert version_to_redo(log("user", "undo", "redo")) is None

    def test_a_new_edit_clears_the_redo_stack(self) -> None:
        """The conventional rule, and a safety one.

        Without it a redo would resurrect work from before the branch and
        silently overwrite the edit the user just made.
        """
        assert version_to_redo(log("user", "user", "undo", "user")) is None


class TestRoundTrip:
    def test_undo_and_redo_walk_the_same_stack(self) -> None:
        """Simulate a session: each operation appends its own version."""
        entries = log("user", "user", "user")

        assert version_to_undo(entries) == 3
        entries.append(VersionGroup(4, "undo"))
        assert version_to_undo(entries) == 2
        entries.append(VersionGroup(5, "undo"))
        assert version_to_undo(entries) == 1

        # Now walk back out.
        assert version_to_redo(entries) == 5
        entries.append(VersionGroup(6, "redo"))
        assert version_to_redo(entries) == 4
        entries.append(VersionGroup(7, "redo"))
        assert version_to_redo(entries) is None

        # And everything is undoable again.
        assert version_to_undo(entries) == 3
