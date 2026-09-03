"""Getting the Agent SDK onto a loop that can start a subprocess.

The SDK spawns the Claude Code CLI and talks to it over pipes. On Windows a
``SelectorEventLoop`` cannot do that -- and a dev server started with
``--reload`` has exactly that loop, because uvicorn picks the selector loop when
it needs subprocesses of its own. The failure was a bare ``NotImplementedError``
out of ``asyncio.base_events``, which says nothing about what to do next.
"""

from __future__ import annotations

import asyncio
import sys
import threading

import pytest

from studio.agent.bridge import MainLoop, loop_can_spawn, run_with_subprocess_support


class TestCapability:
    def test_a_selector_loop_cannot_spawn(self) -> None:
        loop = asyncio.SelectorEventLoop()
        try:
            assert not loop_can_spawn(loop)
        finally:
            loop.close()

    @pytest.mark.skipif(sys.platform != "win32", reason="Proactor is Windows-only")
    def test_a_proactor_loop_can(self) -> None:
        loop = asyncio.ProactorEventLoop()
        try:
            assert loop_can_spawn(loop)
        finally:
            loop.close()


class TestRunning:
    async def test_a_capable_loop_stays_put(self) -> None:
        """No thread when there is nothing to work around.

        Linux, macOS, and a Windows server started without ``--reload`` all get
        the simple path, and the awkward one stays off the common route.
        """
        here = threading.current_thread().ident
        ran_on: list[int | None] = []

        async def work() -> str:
            ran_on.append(threading.current_thread().ident)
            return "done"

        result = await run_with_subprocess_support(work)

        assert result == "done"
        if loop_can_spawn(asyncio.get_running_loop()):
            assert ran_on == [here]

    def test_a_selector_loop_gets_a_worker(self) -> None:
        """The case that was crashing, end to end."""
        started_on: list[int | None] = []

        async def work() -> str:
            started_on.append(threading.current_thread().ident)
            # The thing a selector loop cannot do.
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "print('hi')", stdout=asyncio.subprocess.PIPE
            )
            out, _ = await process.communicate()
            return out.decode().strip()

        async def main() -> str:
            assert not loop_can_spawn(asyncio.get_running_loop())
            return await run_with_subprocess_support(work)

        loop = asyncio.SelectorEventLoop()
        try:
            assert loop.run_until_complete(main()) == "hi"
        finally:
            loop.close()

        assert started_on[0] != threading.current_thread().ident

    def test_a_failure_crosses_back(self) -> None:
        """A crash in the worker must not be swallowed into a silent no-op."""

        async def work() -> None:
            raise ValueError("from the worker")

        async def main() -> None:
            await run_with_subprocess_support(work)

        loop = asyncio.SelectorEventLoop()
        try:
            with pytest.raises(ValueError, match="from the worker"):
                loop.run_until_complete(main())
        finally:
            loop.close()


class TestMarshalling:
    """Nothing here is thread-safe by accident.

    ``TurnChannel.emit`` puts onto an ``asyncio.Queue`` and the repository's
    sessions belong to the loop that opened them, so the worker never touches
    either directly.
    """

    def test_work_runs_on_the_server_loop_not_the_worker(self) -> None:
        server_thread: list[int | None] = []
        emitted: list[str] = []

        async def on_server() -> str:
            server_thread.append(threading.current_thread().ident)
            return "applied"

        async def work(server: MainLoop) -> str:
            assert threading.current_thread().name == "claude-agent-sdk"
            server.run_sync(emitted.append, "an event")
            return await server.run_async(on_server())

        async def main() -> str:
            server = MainLoop(asyncio.get_running_loop())
            return await run_with_subprocess_support(lambda: work(server))

        loop = asyncio.SelectorEventLoop()
        try:
            assert loop.run_until_complete(main()) == "applied"
        finally:
            loop.close()

        # The coroutine ran where the database lives, not on the SDK thread.
        assert server_thread[0] == threading.current_thread().ident
        assert emitted == ["an event"]
