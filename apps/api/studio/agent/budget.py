"""Per-turn limits.

A bulkhead, and one governing rule: **a limit stops the turn only when there is
nothing good left to do.** Anything else finishes the work and says what it did.

That rule is the result of watching the previous design fail. Every limit here
was written against a real failure, and each was a fixed number standing in for
"this is more than I expected". Then a perfectly reasonable request -- tailor a
template for a different role -- tripped three of them in sequence, and each one
stopped the turn two thirds through. The user got a resume that was neither
tailored nor original, under the message "Some changes could not be applied".

A half-finished document is worse than either outcome it sits between, and the
turn already takes a checkpoint: the whole thing is one undo. So a cap that
blocks work to prevent something undo already handles is trading a certain cost
for an uncertain benefit. Those became warnings.

**Hard stops.** Pathology, where continuing produces nothing:

* ``wall_clock`` — a model that stalls mid-generation. Without this a turn hangs
  rather than fails, and the user watches a spinner forever.
* ``max_repairs`` — a model that cannot fix its own malformed call and retries
  the identical thing.
* ``max_stalled_rounds`` — rounds that apply nothing, which is the read-tool
  loop: call a search, ignore the result, call it again. Counted *consecutively*
  and reset by any applied edit, so a turn making progress is never cut off for
  taking a while.
* ``max_iterations`` — a ceiling far above any real turn, in case the two above
  somehow both miss.

**Advisory.** Reported, never blocking, because the work is worth more finished
than stopped and the checkpoint makes it reversible:

* ``max_touch_ratio`` — the runaway rewrite. Still measured, still surfaced, and
  now the user decides with a completed document in front of them.
* ``max_ops`` — how many edits one turn made.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    def __init__(self, message: str, *, limit: str) -> None:
        super().__init__(message)
        self.limit = limit


@dataclass
class TurnBudget:
    #: A ceiling, not a working limit. A turn that genuinely needs thirty rounds
    #: is doing real work; one that needs sixty is broken in a way the stall
    #: counter should already have caught.
    max_iterations: int = 40
    #: Consecutive rounds that applied nothing. This is the read-tool loop, and
    #: it is the number that actually ends a runaway.
    max_stalled_rounds: int = 3
    max_repairs: int = 3
    max_repairs_per_call: int = 2
    #: Advisory. Beyond this share of the document's text nodes, a turn is
    #: rewriting rather than editing -- worth saying, never worth stopping for.
    max_touch_ratio: float = 0.4
    #: Advisory. How many edits before a turn is worth remarking on.
    max_ops: int = 12
    wall_clock_seconds: float = 300.0

    iterations: int = field(default=0, init=False)
    stalled: int = field(default=0, init=False)
    ops_applied: int = field(default=0, init=False)
    repairs: int = field(default=0, init=False)
    touched: set[str] = field(default_factory=set, init=False)
    notices: list[str] = field(default_factory=list, init=False)
    started: float = field(default_factory=time.monotonic, init=False)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def for_scaffold(self) -> "TurnBudget":
        """Nothing to lift any more, and that is the point.

        This used to raise three caps so that tailoring a template could finish.
        Once those caps stopped blocking work at all, the special case stopped
        existing: a scaffold turn and an ordinary one now run under exactly the
        same limits, and the only difference between them is what gets *said*
        afterwards. Kept as a seam because scaffolding is a real distinction the
        loop still makes; it just no longer needs a different budget.
        """
        return self

    def start_iteration(self) -> None:
        self.iterations += 1
        if self.iterations > self.max_iterations:
            raise BudgetExceeded(
                "Stopped: the assistant kept going without finishing.",
                limit="iterations",
            )
        if self.elapsed > self.wall_clock_seconds:
            raise BudgetExceeded(
                f"Stopped after {self.wall_clock_seconds:.0f}s, which usually means "
                "the model stalled rather than that the work was long.",
                limit="wall_clock",
            )

    def end_iteration(self, applied_this_round: int) -> None:
        """Close a round, and stop only if nothing is happening.

        A round that changed something resets the counter however long the turn
        has run: a model working steadily through a resume is doing exactly what
        was asked, and cutting it off mid-way is the failure this whole module
        was rewritten to stop causing. Rounds that change nothing are the
        read-tool loop, and three in a row is not a slow turn, it is a stuck one.
        """
        if applied_this_round > 0:
            self.stalled = 0
            return
        self.stalled += 1
        if self.stalled > self.max_stalled_rounds:
            raise BudgetExceeded(
                "Stopped: the assistant kept looking things up without changing "
                "anything. Naming what to change -- a section, or words that "
                "appear in the document -- usually settles it.",
                limit="stalled",
            )

    def record_ops(self, count: int, touched: list[str]) -> None:
        """Count applied work. Never raises: finishing beats stopping short."""
        self.ops_applied += count
        self.touched.update(touched)

    def record_repair(self) -> None:
        self.repairs += 1
        if self.repairs > self.max_repairs:
            raise BudgetExceeded(
                "Gave up after repeated malformed tool calls.", limit="repairs"
            )

    def review(self, total_nodes: int) -> list[str]:
        """What is worth telling the user about the size of this turn.

        Reported once, at the end, with the finished document in front of them
        and a single undo behind it. That is a better position to judge from
        than a half-rewritten resume and a message saying the rest was refused.
        """
        notices: list[str] = []

        if self.ops_applied > self.max_ops:
            notices.append(
                f"This turn made {self.ops_applied} changes. Undo reverses the "
                "whole turn at once if it went further than you wanted."
            )

        # Ratios are meaningless on a nearly empty document, and editing both
        # bullets of a two-bullet resume is entirely normal.
        if total_nodes >= 5:
            ratio = len(self.touched) / total_nodes
            if ratio > self.max_touch_ratio:
                notices.append(
                    f"This turn rewrote {ratio:.0%} of the resume rather than "
                    "editing part of it. Worth reading before you send it."
                )

        self.notices = notices
        return notices

    def snapshot(self) -> dict[str, float | int]:
        return {
            "iterations": self.iterations,
            "ops_applied": self.ops_applied,
            "repairs": self.repairs,
            "touched": len(self.touched),
            "elapsed_ms": int(self.elapsed * 1000),
        }
