"""Settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    log_level: str = "INFO"

    data_dir: Path = Path("data")
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

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{(self.data_dir / 'studio.db').as_posix()}"


settings = Settings()
