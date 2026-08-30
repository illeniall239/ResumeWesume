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
"""

from __future__ import annotations

import logging

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 30_000
_FONT_TIMEOUT_MS = 5_000
_PRINT_ROOT = "[data-print-root]"

_PAGE_FORMATS = {"A4": "A4", "LETTER": "Letter"}


def _format_for(page_size: str) -> str:
    return _PAGE_FORMATS.get((page_size or "A4").upper(), "A4")


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

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="load", timeout=_NAV_TIMEOUT_MS)
            await page.wait_for_selector(_PRINT_ROOT, timeout=_NAV_TIMEOUT_MS)
            try:
                # wait_for_function, not evaluate: only the former takes a
                # timeout, and an unbounded font wait would hang the export.
                await page.wait_for_function(
                    "() => !document.fonts || document.fonts.status === 'loaded'",
                    timeout=_FONT_TIMEOUT_MS,
                )
            except PlaywrightError:
                # A font that never resolves should degrade the PDF, not fail
                # the export.
                logger.warning("Fonts did not settle before print; continuing")
            return await page.pdf(
                format=_format_for(page_size),
                margin=margin,
                print_background=True,
                prefer_css_page_size=False,
            )
        finally:
            await browser.close()


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
    return "PDF export failed."
