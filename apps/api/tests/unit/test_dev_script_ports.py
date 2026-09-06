"""The launcher has to tell the web app where the API is, twice.

There are two names because there are two callers. `API_ORIGIN` is read by
`next.config.ts` for the browser's /api/* rewrite; `INTERNAL_API_ORIGIN` is
read by `lib/api.ts` when a server component fetches, and on its own it
defaults to port 8000.

Setting only the first fails in a way that is both silent and narrow. Every
click keeps working, because clicks go through the browser. The one server
component is /print, so the only thing that breaks is PDF export -- and it
breaks by fetching a document from a port that does not have it, which the
print page catches and renders as an empty root. Chromium exports that: a
blank PDF, and nothing anywhere says why.
"""

from __future__ import annotations

import ast
from pathlib import Path

DEV = Path(__file__).resolve().parents[4] / "scripts" / "dev.py"


def env_assignments() -> dict[str, set[str]]:
    """The keys each `*_env` dict literal in `main` sets, read from the source.

    Read rather than executed: `dev.py` is stdlib-only by design and spawns
    servers on import of nothing, so parsing is both cheaper and safer than
    importing it.
    """
    tree = ast.parse(DEV.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.endswith("_env"):
            continue
        found[target.id] = {
            key.value for key in node.value.keys if isinstance(key, ast.Constant)
        }
    return found


class TestTheWebApp:
    def test_is_told_where_the_api_is_for_the_browser(self) -> None:
        assert "API_ORIGIN" in env_assignments()["web_env"]

    def test_is_told_again_for_its_own_server_side_fetches(self) -> None:
        # The one that was missing. Its absence is invisible until somebody
        # runs on a non-default port and exports a PDF.
        assert "INTERNAL_API_ORIGIN" in env_assignments()["web_env"]


class TestTheApi:
    def test_is_told_where_the_web_app_is(self) -> None:
        # PDF export runs backwards through the stack: the API drives headless
        # Chromium at the web app's own /print route.
        assert "WEB_BASE_URL" in env_assignments()["api_env"]
