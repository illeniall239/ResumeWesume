"""Per-turn limits.

A bulkhead. An agent loop with a confused model can otherwise spend unbounded
time and money, or rewrite a whole resume when asked to fix one line. Each limit
below exists because its absence produces a specific, observed failure:

* ``max_iterations`` — a model that calls a read tool, ignores the result and
  calls it again, forever.
* ``max_ops`` — a "tighten this bullet" turn that decides to rewrite forty.
* ``max_repairs`` — a model that cannot fix its own malformed call and retries
  the identical thing.
* ``max_touch_ratio`` — the runaway rewrite. This is the one that protects the
  user's document rather than the bill, and it trips a rollback rather than a
  warning.
* ``wall_clock`` — a local model that stalls mid-generation.
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
    max_iterations: int = 6
    max_ops: int = 12
    max_repairs: int = 3
    max_repairs_per_call: int = 2
    # Beyond this share of the document's text nodes, a turn is rewriting rather
    # than editing.
    max_touch_ratio: float = 0.4
    wall_clock_seconds: float = 300.0

    iterations: int = field(default=0, init=False)
    ops_applied: int = field(default=0, init=False)
    repairs: int = field(default=0, init=False)
    touched: set[str] = field(default_factory=set, init=False)
    started: float = field(default_factory=time.monotonic, init=False)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def start_iteration(self) -> None:
        self.iterations += 1
        if self.iterations > self.max_iterations:
            raise BudgetExceeded(
                f"Stopped after {self.max_iterations} rounds without settling.",
                limit="iterations",
            )
        if self.elapsed > self.wall_clock_seconds:
            raise BudgetExceeded(
                f"Stopped after {self.wall_clock_seconds:.0f}s.", limit="wall_clock"
            )

    def record_ops(self, count: int, touched: list[str]) -> None:
        self.ops_applied += count
        self.touched.update(touched)
        if self.ops_applied > self.max_ops:
            raise BudgetExceeded(
                f"Stopped after {self.max_ops} edits in one turn.", limit="ops"
            )

    def record_repair(self) -> None:
        self.repairs += 1
        if self.repairs > self.max_repairs:
            raise BudgetExceeded(
                "Gave up after repeated malformed tool calls.", limit="repairs"
            )

    def check_touch_ratio(self, total_nodes: int) -> None:
        """Trip if a turn is rewriting rather than editing."""
        if total_nodes < 5:
            # Ratios are meaningless on a nearly empty document, and editing
            # both bullets of a two-bullet resume is entirely normal.
            return
        ratio = len(self.touched) / total_nodes
        if ratio > self.max_touch_ratio:
            raise BudgetExceeded(
                f"This turn touched {ratio:.0%} of the resume, which looks like a "
                "runaway rewrite rather than an edit.",
                limit="touch_ratio",
            )

    def snapshot(self) -> dict[str, float | int]:
        return {
            "iterations": self.iterations,
            "ops_applied": self.ops_applied,
            "repairs": self.repairs,
            "touched": len(self.touched),
            "elapsed_ms": int(self.elapsed * 1000),
        }
