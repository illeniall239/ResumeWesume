"""Running the Agent SDK on a loop that can start a subprocess.

The Claude Agent SDK works by spawning the Claude Code CLI and talking to it
over pipes. On Windows that needs a ``ProactorEventLoop``, and a dev server
started with ``--reload`` does not have one:

    uvicorn/loops/asyncio.py
        if sys.platform == "win32" and not use_subprocess:
            return asyncio.ProactorEventLoop
        return asyncio.SelectorEventLoop

``use_subprocess`` is true under ``--reload``, so the server loop is a
``SelectorEventLoop`` -- and a selector loop raises bare ``NotImplementedError``
from ``_make_subprocess_transport``. Measured, not assumed:

    Selector -> NotImplementedError (no subprocess support)
    Proactor -> ok

Telling people not to use ``--reload`` is not a fix, so the SDK gets its own
loop on its own thread and everything else stays where it was.

**What crosses the boundary, and how.** Nothing in this application is
thread-safe by accident. ``TurnChannel.emit`` puts onto an ``asyncio.Queue``,
which is not thread-safe, and the repository's sessions belong to the loop that
opened them. So the worker thread never touches either directly: it marshals
every call back to the server loop, and the SDK thread does nothing but drive
the SDK.

Ordering survives the trip. Both ``call_soon_threadsafe`` and
``run_coroutine_threadsafe`` append to the target loop's callback queue in the
order the calling thread submitted them, so events arrive in the order they were
produced and the sequence numbers -- assigned on the server loop, in one place --
stay contiguous.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


def loop_can_spawn(loop: asyncio.AbstractEventLoop | None = None) -> bool:
    """Whether this loop can start a subprocess.

    Asked by capability rather than by platform: the answer depends on which
    loop implementation is running, and on Windows that varies with how the
    server was started.
    """
    target = loop or asyncio.get_event_loop_policy().get_event_loop()
    return not isinstance(target, asyncio.SelectorEventLoop)


class MainLoop:
    """A handle on the server loop, usable from the worker thread.

    Every method here is safe to call off-loop; that is the entire point of the
    class. Anything not exposed here is not safe to reach from the SDK thread.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def run_sync(self, function: Callable[..., Any], *args: Any) -> None:
        """Fire a synchronous callback on the server loop and do not wait.

        For emitting events: the caller has nothing to learn from the result,
        and blocking the SDK on each token would serialise the stream against
        the server loop for no benefit.
        """
        self._loop.call_soon_threadsafe(function, *args)

    async def run_async(self, coroutine: Awaitable[T]) -> T:
        """Run a coroutine on the server loop and await its result here.

        Used for tool calls, which touch the database and must run on the loop
        that owns those sessions. The await is in the worker loop, so the SDK
        stays responsive while the edit is applied.
        """
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)  # type: ignore[arg-type]
        return await asyncio.wrap_future(future)


async def run_with_subprocess_support(work: Callable[[], Awaitable[T]]) -> T:
    """Run ``work`` on a loop that can spawn a subprocess.

    ``work`` is a zero-argument callable returning a coroutine rather than a
    coroutine itself, because a coroutine created on one loop and never awaited
    there produces a "coroutine was never awaited" warning if the thread fails
    to start. Building it inside the worker keeps its whole life on one loop.

    When the running loop is already capable this stays put -- there is no
    reason to pay for a thread on Linux, on macOS, or on a Windows server
    started without ``--reload``.
    """
    if loop_can_spawn(asyncio.get_running_loop()):
        return await work()

    main = asyncio.get_running_loop()
    done: asyncio.Future[T] = main.create_future()

    def worker() -> None:
        # `asyncio.Runner` rather than a bare `run_until_complete`, because the
        # SDK leaves background tasks and async generators running when the
        # stream ends -- it reads the CLI's pipes on its own tasks. Closing the
        # loop underneath them raised "Event loop is closed" from inside the SDK
        # and printed "Task was destroyed but it is pending" twice. Runner
        # cancels outstanding tasks and shuts generators down before closing,
        # which is the same teardown uvicorn does for the server loop.
        #
        # The factory is explicit rather than the policy default: the policy is
        # what produced a selector loop in the first place, and this thread
        # exists precisely because that loop cannot do the job.
        factory = (
            asyncio.ProactorEventLoop  # type: ignore[attr-defined]
            if sys.platform == "win32"
            else asyncio.new_event_loop
        )
        try:
            with asyncio.Runner(loop_factory=factory) as runner:
                result = runner.run(work())
        except BaseException as error:  # noqa: BLE001 -- re-raised on the server loop
            main.call_soon_threadsafe(_settle_error, done, error)
        else:
            main.call_soon_threadsafe(_settle_value, done, result)

    thread = threading.Thread(
        target=worker, name="claude-agent-sdk", daemon=True
    )
    thread.start()
    return await done


def _settle_value(future: asyncio.Future[Any], value: Any) -> None:
    if not future.done():
        future.set_result(value)


def _settle_error(future: asyncio.Future[Any], error: BaseException) -> None:
    if not future.done():
        future.set_exception(error)
