"""Bring the whole app up with one command.

    uv run scripts/dev.py

Setting this up used to be four commands across two terminals, plus knowing
that `make` is not on Windows -- which is most people, and was true of the
machine this was written on. Two processes is a real constraint of the design
(PDF export runs backwards through the stack: the API drives headless Chromium
at the web app's own /print route), so the answer is not one process. It is one
command that runs two.

Deliberately stdlib only, and deliberately not part of either workspace. It has
to run *before* anything is installed, which rules out importing anything that
installation would provide.

What it does, in order: check the tools it cannot install for you, install what
it can, start both servers, wait until they actually answer, and open the app.
Ctrl+C stops both.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web"

#: How long to wait for a server to answer before giving up on it. Generous
#: because the first `next dev` compile on a cold cache is genuinely slow, and
#: a timeout that fires during a normal first run teaches people to distrust
#: the script.
READY_TIMEOUT = 180.0

# Two servers' worth of output passes through this process, and `next dev`
# prints box-drawing characters and check marks. Decoded with a Windows console
# code page those raise UnicodeEncodeError on the way back out, which kills the
# thread forwarding them and leaves a server running with no visible log. UTF-8
# with replacement everywhere; this script's own glyphs stay ASCII regardless,
# because a setup script that renders as mojibake looks broken before it has
# done anything.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # pragma: no cover - older or redirected streams
        pass

#: ANSI, and only where it will render. A log prefix is worth colouring because
#: the whole point is telling two interleaved streams apart at a glance.
if os.name == "nt":
    # Windows 10+ terminals understand ANSI once it is switched on; older ones
    # would print the escapes literally, so this is asked for rather than
    # assumed.
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.SetConsoleMode(kernel.GetStdHandle(-11), 7)
    except Exception:  # pragma: no cover - cosmetic only
        pass

COLOUR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOUR else text


def say(message: str) -> None:
    print(f"{paint('==>', '38;5;208')} {message}", flush=True)


def fail(message: str, *, fix: str = "") -> None:
    """Stop, having said what is wrong and what to do about it.

    Every exit from this script names the remedy. A setup script that reports
    a problem without one is a worse error message than the raw failure it was
    written to replace.
    """
    print(f"\n{paint('!!', '31')} {message}", file=sys.stderr)
    if fix:
        print(f"  {fix}\n", file=sys.stderr)
    raise SystemExit(1)


# --- the tools this cannot install for you ----------------------------------


def require_tools() -> tuple[str, str]:
    """`uv` and `npm`, or a message saying where to get the missing one."""
    uv = shutil.which("uv")
    if not uv:
        fail(
            "uv is not installed. It manages the Python side.",
            fix="Install it from https://docs.astral.sh/uv/ and run this again.",
        )

    # `npm` is `npm.cmd` on Windows, which `which` finds and bare `npm` in a
    # subprocess without a shell does not.
    npm = shutil.which("npm")
    if not npm:
        fail(
            "npm is not installed. It builds and serves the web app.",
            fix="Install Node.js 20 or newer from https://nodejs.org and run this again.",
        )
    return uv, npm


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def require_ports(api_port: int, web_port: int) -> None:
    """Refuse a port that is taken, rather than letting the server explain.

    uvicorn's answer to a bound port is a traceback, and Next's is to quietly
    move to the next free one -- so the API would be where it was told and the
    web app somewhere else, which fails later as a CORS error that says nothing
    about ports.
    """
    for name, port in (("The API", api_port), ("The web app", web_port)):
        if not port_is_free(port):
            fail(
                f"{name} cannot start: port {port} is already in use.",
                fix=(
                    "Something else is on it -- most likely this app, already "
                    f"running. Stop it, or pass --api-port/--web-port."
                ),
            )


# --- installing -------------------------------------------------------------


def run(command: list[str], *, cwd: Path, what: str) -> None:
    say(what)
    result = subprocess.run(command, cwd=cwd)
    if result.returncode != 0:
        fail(
            f"{what} failed.",
            fix=f"Run it directly to see why:  cd {cwd.relative_to(ROOT)} && "
            + " ".join(command),
        )


def install_if_needed(uv: str, npm: str, *, force: bool) -> None:
    """Whatever is missing, and nothing that is not.

    Checked by looking for what installation produces rather than by keeping a
    marker file: a marker goes stale the moment somebody deletes a directory by
    hand, and then the script confidently skips the step that would have fixed
    it.
    """
    api_ready = (API / ".venv").exists() and not force
    if not api_ready:
        run([uv, "sync", "--extra", "dev"], cwd=API, what="Installing the API")
        # Only after a sync. It is idempotent, but it prints a paragraph and
        # touches the network to decide it has nothing to do.
        run(
            [uv, "run", "playwright", "install", "chromium"],
            cwd=API,
            what="Installing the browser that renders PDFs",
        )

    if not (WEB / "node_modules").exists() or force:
        run([npm, "install"], cwd=WEB, what="Installing the web app")


# --- running ----------------------------------------------------------------


def spawn(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    """Start a server in its own process group.

    The group matters on the way down. `next dev` forks a compiler of its own,
    and killing only the process we launched leaves that child holding the
    port -- so the next run of this script reports the port as busy and blames
    the user for it.
    """
    creation = 0
    preexec = None
    if os.name == "nt":
        creation = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        preexec = os.setsid

    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creation,
        preexec_fn=preexec,
    )


def pump(process: subprocess.Popen, label: str, colour: str) -> threading.Thread:
    """Stream one server's output under a prefix, on a thread of its own."""

    def forward() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            print(f"{paint(label, colour)} {line.rstrip()}", flush=True)

    thread = threading.Thread(target=forward, daemon=True)
    thread.start()
    return thread


def answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except urllib.error.HTTPError:
        # A 404 is still a server. This only asks whether anything is home.
        return True
    except Exception:
        return False


def wait_until_up(url: str, *, what: str, watch: list[subprocess.Popen]) -> bool:
    """Poll until it answers, or until one of the servers dies.

    Watching the processes is what keeps this from being a three-minute wait
    on a server that exited in the first second.
    """
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        for process in watch:
            if process.poll() is not None:
                return False
        if answers(url):
            return True
        time.sleep(0.4)
    say(f"{what} did not answer within {READY_TIMEOUT:.0f}s. Leaving it running.")
    return False


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            # /T for the tree, which is the whole reason for the process group.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except Exception:  # pragma: no cover - it is already going away
        process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="dev.py", description="Run ResumeWesume: both servers, one command."
    )
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=3000)
    parser.add_argument(
        "--reinstall",
        action="store_true",
        help="Install again even if it looks installed. Fixes a stale checkout.",
    )
    parser.add_argument(
        "--no-open", action="store_true", help="Do not open a browser."
    )
    args = parser.parse_args()

    uv, npm = require_tools()
    require_ports(args.api_port, args.web_port)
    install_if_needed(uv, npm, force=args.reinstall)

    web_url = f"http://localhost:{args.web_port}"
    api_url = f"http://127.0.0.1:{args.api_port}"

    # Each has to be told where the other is, and neither address is a
    # constant once a port has moved.
    #
    # The API drives headless Chromium at the web app's own /print route to
    # render a PDF, so it needs WEB_BASE_URL. The web app never calls the API
    # cross-origin -- `next.config.ts` rewrites /api/* to API_ORIGIN, so the
    # browser only ever talks to one host and there is no CORS in development.
    #
    # API_ORIGIN is the name that config actually reads. Passing the plausible
    # NEXT_PUBLIC_API_BASE instead left the rewrite on its default port while
    # the API was on the one that was asked for: both servers came up, both
    # reported themselves ready, and every request through the app answered
    # 500. Ports that are wrong together are silent; ports that are wrong
    # separately fail like this.
    api_env = {**os.environ, "WEB_BASE_URL": web_url}
    web_env = {**os.environ, "API_ORIGIN": api_url}

    say("Starting the API and the web app. Ctrl+C stops both.")
    servers = [
        (
            "api",
            "38;5;110",
            spawn(
                [
                    uv, "run", "uvicorn", "studio.main:app",
                    "--reload", "--port", str(args.api_port),
                ],
                cwd=API,
                env=api_env,
            ),
        ),
        (
            "web",
            "38;5;208",
            spawn(
                # `--` hands the rest to `next dev` rather than to npm.
                [npm, "run", "dev", "--", "--port", str(args.web_port)],
                cwd=WEB,
                env=web_env,
            ),
        ),
    ]
    processes = [process for _, _, process in servers]
    for label, colour, process in servers:
        pump(process, f"{label} |", colour)

    try:
        if wait_until_up(
            f"{api_url}/api/v1/documents", what="The API", watch=processes
        ) and wait_until_up(web_url, what="The web app", watch=processes):
            print()
            say(f"Ready: {paint(web_url, '1')}")
            if not args.no_open:
                webbrowser.open(web_url)

        # Whichever exits first takes the other with it: one server alone is
        # never a working app, and a half-dead pair that keeps printing looks
        # like it is still fine.
        while all(process.poll() is None for process in processes):
            time.sleep(0.3)
        for label, _, process in servers:
            if process.poll() is not None:
                say(f"The {label} stopped (exit {process.returncode}). Stopping the other.")
                break
    except KeyboardInterrupt:
        print()
        say("Stopping.")
    finally:
        for process in processes:
            stop(process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
