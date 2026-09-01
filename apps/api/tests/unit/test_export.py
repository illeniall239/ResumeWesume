"""PDF export: where the browser runs, and what a failure tells the user.

This module had no tests, which is how it shipped broken. Every export from a
running server failed on Windows while the identical call from a script
succeeded, because uvicorn selects a ``SelectorEventLoop`` there and that loop
cannot spawn subprocesses — Playwright's async driver launches Chromium with
``asyncio.create_subprocess_exec``, so it raised a bare ``NotImplementedError``
with no message, and the user was told only "PDF export failed."

Nothing here launches a browser. The two things worth pinning are properties,
not renders: that the blocking work happens off the event loop, and that a
failure names its own cause.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from studio.export import pdf as export
from studio.export.pdf import describe_failure, failure_detail, render_pdf

URL = "http://localhost:3000/print/doc_1"


class TestRunsOffTheEventLoop:
    async def test_the_browser_is_driven_from_a_worker_thread(self, monkeypatch) -> None:
        """The regression itself.

        Driving Chromium from the event loop thread ties the export to whatever
        loop policy the server happened to pick. Running it in a worker thread
        means the sync driver spawns the browser with ``subprocess.Popen`` and
        the loop is irrelevant.
        """
        seen: dict[str, int] = {}

        def fake(url: str, *, page_size: str, margin: dict[str, str]) -> bytes:
            seen["thread"] = threading.get_ident()
            return b"%PDF-1.4 fake"

        monkeypatch.setattr(export, "_render_blocking", fake)
        result = await render_pdf(URL)

        assert result == b"%PDF-1.4 fake"
        assert seen["thread"] != threading.get_ident()

    async def test_the_event_loop_keeps_running_during_a_render(
        self, monkeypatch
    ) -> None:
        """A render takes seconds. Blocking the loop for that long would stall
        every other request and every heartbeat on the process."""
        import time

        def slow(url: str, *, page_size: str, margin: dict[str, str]) -> bytes:
            time.sleep(0.2)
            return b"%PDF-1.4"

        monkeypatch.setattr(export, "_render_blocking", slow)

        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker = asyncio.create_task(tick())
        await render_pdf(URL)
        ticker.cancel()

        assert ticks > 3, "the loop was blocked while the browser rendered"

    async def test_margins_default_to_ten_millimetres(self, monkeypatch) -> None:
        captured: dict[str, dict[str, str]] = {}

        def fake(url: str, *, page_size: str, margin: dict[str, str]) -> bytes:
            captured["margin"] = margin
            return b""

        monkeypatch.setattr(export, "_render_blocking", fake)
        await render_pdf(URL)
        assert captured["margin"] == {
            "top": "10mm",
            "bottom": "10mm",
            "left": "10mm",
            "right": "10mm",
        }

    async def test_a_render_failure_propagates_to_the_caller(
        self, monkeypatch
    ) -> None:
        """The router turns this into a 503 with a description; swallowing it
        in the thread would hand back an empty file instead."""

        def boom(url: str, *, page_size: str, margin: dict[str, str]) -> bytes:
            raise RuntimeError("chromium crashed")

        monkeypatch.setattr(export, "_render_blocking", boom)
        with pytest.raises(RuntimeError, match="chromium crashed"):
            await render_pdf(URL)


class TestFailureMessages:
    def test_a_refused_connection_names_the_web_app(self) -> None:
        message = describe_failure(Exception("net::ERR_CONNECTION_REFUSED at ..."), URL)
        assert URL in message
        assert "Start it" in message

    def test_a_missing_browser_names_the_install_command(self) -> None:
        message = describe_failure(
            Exception("Executable doesn't exist at ...chrome.exe"), URL
        )
        assert "playwright install chromium" in message

    def test_a_timeout_names_the_url(self) -> None:
        assert URL in describe_failure(Exception("Timeout 30000ms exceeded"), URL)

    def test_the_windows_loop_failure_is_named(self) -> None:
        """It arrives with an empty message, so the generic fallback said
        nothing at all and left the user with no thread to pull."""
        message = describe_failure(NotImplementedError(), URL)
        assert "event-loop" in message
        assert message != "PDF export failed."

    def test_an_unknown_failure_still_names_its_type(self) -> None:
        assert "ValueError" in describe_failure(ValueError(""), URL)

    def test_the_log_detail_names_a_silent_exception(self) -> None:
        # The original log line read "PDF export failed for <id>: " and named
        # nothing, because str(NotImplementedError()) is empty.
        assert failure_detail(NotImplementedError()) == "NotImplementedError"
        assert failure_detail(RuntimeError("boom")) == "RuntimeError: boom"
