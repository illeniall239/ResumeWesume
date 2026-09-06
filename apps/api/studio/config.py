"""Settings."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

#: What the data directory is called wherever the OS keeps application data.
_APP_DIR = "ResumeWesume"

#: Where the database used to live: `./data`, relative to the process's working
#: directory. Kept only so an existing one can be adopted -- see `_adopt`.
_LEGACY_DIR = Path("data")


def default_data_dir() -> Path:
    """Where this machine keeps application data.

    Absolute, and nothing to do with where the server was launched from. It
    used to be the relative path `./data`, which meant the database was
    wherever the process happened to be started: the Makefile cds into
    `apps/api` first, so a person who ran uvicorn from the repository root, or
    from a shortcut, or from a service wrapper, got a brand-new empty database
    and every résumé appeared to have vanished -- while the real file sat
    intact one directory away.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / _APP_DIR
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_DIR
    else:
        base = os.environ.get("XDG_DATA_HOME")
        if base:
            return Path(base) / _APP_DIR.lower()
        return Path.home() / ".local" / "share" / _APP_DIR.lower()
    return Path.home() / f".{_APP_DIR.lower()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    log_level: str = "INFO"

    #: Absolute by default; set `DATA_DIR` to keep it somewhere else.
    data_dir: Path = default_data_dir()
    database_url: str = ""

    # Where the print pages live. Headless Chromium navigates here to render a
    # PDF, so it must be reachable from the API process.
    web_base_url: str = "http://localhost:3000"

    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Local-first by default: nothing leaves the machine unless the user
    # configures a cloud provider.
    llm_provider: str = "ollama"
    llm_model: str = "qwen3:14b-16k"
    llm_api_base: str = "http://localhost:11434"
    llm_api_key: str = ""

    #: How many tokens a turn may generate.
    #:
    #: 2048 was too tight and produced the worst failure this app can have: a
    #: reasoning model spends its whole budget inside its thinking block, is cut
    #: off before it writes either a reply or a tool call, and the turn ends
    #: having silently done nothing. Measured on qwen3:4b against a full-size
    #: prompt, the same instruction needs about 1,500 generated tokens to reach
    #: its first tool call and was still thinking at 2,048.
    #:
    #: 4096 is double the old ceiling and still inside the output cap of every
    #: mainstream cloud provider, so raising it cannot start failing a request
    #: that used to succeed. A model that runs away is caught by the turn's own
    #: wall-clock budget rather than by this.
    llm_max_tokens: int = 4096

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        database = self.data_dir / "studio.db"
        _adopt(database)
        return f"sqlite+aiosqlite:///{database.as_posix()}"


def _adopt(database: Path) -> None:
    """Bring a database from the old relative location, once.

    The move to an absolute path would otherwise do the very thing it exists to
    prevent: somebody upgrades, the app opens a fresh database at the new
    address, and their résumés are gone as far as they can tell.

    Only when there is nothing at the new address, so this can never overwrite
    live data, and it moves rather than copies so it cannot run twice or leave
    two databases to diverge. If the move fails -- the old file is open, or the
    volume is read-only -- the old path is left alone and said so out loud;
    losing the adoption costs a copy by hand, and getting it wrong costs the
    résumés.
    """
    if database.exists():
        return
    legacy = _LEGACY_DIR / "studio.db"
    if not legacy.is_file():
        return

    try:
        shutil.move(str(legacy), str(database))
        # SQLite keeps its write-ahead log and shared-memory file beside the
        # database. Left behind they would be applied to nothing; moved with
        # it, any committed-but-uncheckpointed work comes too.
        for suffix in ("-wal", "-shm"):
            sidecar = legacy.with_name(legacy.name + suffix)
            if sidecar.is_file():
                shutil.move(str(sidecar), str(database.with_name(database.name + suffix)))
    except OSError as error:
        logger.warning(
            "Could not move the existing database from %s to %s (%s). "
            "It is still there and unharmed; move it by hand, or set "
            "DATA_DIR to its directory.",
            legacy.resolve(),
            database,
            error,
        )
        return

    logger.info("Moved the existing database to %s", database)


settings = Settings()
