"""PDF export via headless Chromium.

Ported from Resume-Matcher's ``app/pdf.py``, keeping the readiness discipline
that file's comments encode — each line of it was paid for by a real bug:

*Navigate with ``wait_until="load"``, not ``networkidle``.* A dev server holds
open connections (HMR websockets), so networkidle never arrives and ``goto``
hangs until the timeout.

*Wait for the print root, then for fonts.* Chromium will happily print before
webfonts resolve, producing a PDF laid out in a fallback face with different
metrics — which silently changes pagination.

The browser is launched per request rather than kept warm. That costs about a
second and buys immunity from the failure where a long-lived browser dies and
every later export fails until restart. Revisit only if export volume justifies
it.

**The synchronous Playwright API, in a worker thread.** This looks backwards in
an async service and is not: the async API launches the browser with
``asyncio.create_subprocess_exec``, and on Windows a ``SelectorEventLoop``
cannot spawn subprocesses at all — it raises a bare ``NotImplementedError``
with no message. uvicorn selects exactly that loop policy on Windows, so every
export from a running server failed while the identical call from a script
succeeded, and the empty exception message meant the user was told only "PDF
export failed."

Pinning the loop policy instead would work until something else set it back,
and would leave the failure a property of how the server happened to be
launched. The sync driver spawns Chromium with ``subprocess.Popen`` and does
not care what loop the caller is on; ``to_thread`` keeps it off the event loop,
which a browser render has to be regardless.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 30_000
_FONT_TIMEOUT_MS = 5_000
_PRINT_ROOT = "[data-print-root]"

_PAGE_FORMATS = {"A4": "A4", "LETTER": "Letter"}


def _format_for(page_size: str) -> str:
    return _PAGE_FORMATS.get((page_size or "A4").upper(), "A4")


def _render_blocking(url: str, *, page_size: str, margin: dict[str, str]) -> bytes:
    """Drive Chromium. Blocking, and must not run on the event loop."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            page = browser.new_page()
            page.goto(url, wait_until="load", timeout=_NAV_TIMEOUT_MS)
            page.wait_for_selector(_PRINT_ROOT, timeout=_NAV_TIMEOUT_MS)
            try:
                # wait_for_function, not evaluate: only the former takes a
                # timeout, and an unbounded font wait would hang the export.
                page.wait_for_function(
                    "() => !document.fonts || document.fonts.status === 'loaded'",
                    timeout=_FONT_TIMEOUT_MS,
                )
            except PlaywrightError:
                # A font that never resolves should degrade the PDF, not fail
                # the export.
                logger.warning("Fonts did not settle before print; continuing")
            return page.pdf(
                format=_format_for(page_size),
                margin=margin,
                print_background=True,
                prefer_css_page_size=False,
            )
        finally:
            browser.close()


async def render_pdf(
    url: str,
    *,
    page_size: str = "A4",
    margins_mm: dict[str, float] | None = None,
) -> bytes:
    """Render ``url`` to PDF bytes."""
    margins = margins_mm or {}
    margin = {
        "top": f"{margins.get('top', 10)}mm",
        "bottom": f"{margins.get('bottom', 10)}mm",
        "left": f"{margins.get('left', 10)}mm",
        "right": f"{margins.get('right', 10)}mm",
    }
    return await asyncio.to_thread(
        _render_blocking, url, page_size=page_size, margin=margin
    )


def describe_failure(error: Exception, url: str) -> str:
    """A message that names the likely cause instead of leaking a stack trace."""
    text = str(error)
    if "ERR_CONNECTION_REFUSED" in text or "net::ERR" in text:
        return (
            f"Could not reach the web app at {url}. Start it, or set "
            "WEB_BASE_URL to where it is running."
        )
    if "executable doesn't exist" in text.lower() or "Executable doesn't exist" in text:
        return "Chromium is not installed. Run: uv run playwright install chromium"
    if "Timeout" in text:
        return f"Timed out rendering {url}."
    if isinstance(error, NotImplementedError):
        # The Windows selector-loop failure this module now avoids. Named
        # anyway, because it arrives with an empty message and the generic
        # fallback below told the user nothing at all.
        return (
            "The browser could not be started by this server process. This is "
            "the Windows event-loop limitation; see studio/export/pdf.py."
        )
    return f"PDF export failed ({type(error).__name__})."


def failure_detail(error: Exception) -> str:
    """What to write to the server log.

    ``str(error)`` alone is not enough: the failure that motivated the threaded
    driver raised ``NotImplementedError()`` with no message, so the log line
    read "PDF export failed for <id>: " and named nothing.
    """
    return f"{type(error).__name__}: {error}".rstrip(": ")
