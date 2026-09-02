"""Provider credentials and the active model selection.

Separate from ``DocumentRepo`` because it is a different concern with a
different lifetime: a resume is user content that versions, logs its ops and
supports undo, while a key is a secret that is set, replaced and deleted with no
history at all. Keeping a key out of the ops log is not incidental -- that log
is append-only and is read back to build the undo stack, so a secret written
into it would be permanent and would travel to the browser inside a 409 rebase.

It shares the session factory rather than opening a second engine: same SQLite
file, one connection pool.

The one rule this module exists to enforce: **``api_key`` leaves here only for a
provider request.** Nothing in it is returned to a client, and ``redacted()`` is
the only shape the API layer is given to work with.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from studio.persistence.models import AppSetting, ProviderCredential

#: The setting key holding "provider/model". One string rather than two rows,
#: because half a selection is not a state worth being able to represent.
SELECTION_KEY = "llm.selection"


@dataclass(frozen=True)
class Credential:
    """What is stored for one provider. Never crosses the API boundary."""

    provider: str
    api_key: str = ""
    api_base: str | None = None

    def redacted(self) -> "RedactedCredential":
        return RedactedCredential(
            provider=self.provider,
            configured=bool(self.api_key),
            hint=_hint(self.api_key),
            api_base=self.api_base,
        )


@dataclass(frozen=True)
class RedactedCredential:
    """What a client is allowed to know: that a key exists, and its last four.

    The hint is there so someone with two accounts can tell which key is
    installed without revealing anything useful about it. Four characters of a
    high-entropy secret is not a meaningful head start, and recognising your own
    key is worth more than the theoretical loss.
    """

    provider: str
    configured: bool = False
    hint: str = ""
    api_base: str | None = None


def _hint(api_key: str) -> str:
    if not api_key:
        return ""
    tail = api_key.strip()[-4:]
    return f"••••{tail}" if len(api_key.strip()) > 4 else "••••"


@dataclass(frozen=True)
class Selection:
    """The provider and model the assistant should use."""

    provider: str
    model: str

    def encode(self) -> str:
        return f"{self.provider}/{self.model}"

    @staticmethod
    def decode(raw: str) -> "Selection | None":
        """Parse "provider/model".

        Split once from the left: a model id routinely contains a slash of its
        own (``openai/gpt-oss-120b`` on Groq, or any OpenRouter id), so
        splitting on every slash would silently truncate the model.
        """
        provider, separator, model = raw.partition("/")
        if not separator or not provider.strip() or not model.strip():
            return None
        return Selection(provider=provider.strip(), model=model.strip())


class ProviderStore:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session = session_factory

    # --- credentials ----------------------------------------------------

    async def get(self, provider: str) -> Credential | None:
        async with self._session() as session:
            row = await session.get(ProviderCredential, provider)
            if row is None:
                return None
            return Credential(
                provider=row.provider, api_key=row.api_key or "", api_base=row.api_base
            )

    async def list(self) -> list[Credential]:
        async with self._session() as session:
            rows = (
                await session.execute(select(ProviderCredential))
            ).scalars().all()
            return [
                Credential(
                    provider=row.provider,
                    api_key=row.api_key or "",
                    api_base=row.api_base,
                )
                for row in rows
            ]

    async def put(
        self, provider: str, *, api_key: str | None = None, api_base: str | None = None
    ) -> Credential:
        """Create or update one provider's credential.

        ``api_key=None`` means "leave the stored key alone", which is what lets
        the UI change a base URL without making the user retype a key it is not
        allowed to show them. An empty string is a different instruction: clear
        it.
        """
        async with self._session() as session:
            row = await session.get(ProviderCredential, provider)
            if row is None:
                row = ProviderCredential(provider=provider, api_key="", api_base=None)
                session.add(row)

            if api_key is not None:
                row.api_key = api_key.strip()
            if api_base is not None:
                row.api_base = api_base.strip() or None

            await session.commit()
            return Credential(
                provider=row.provider, api_key=row.api_key or "", api_base=row.api_base
            )

    async def forget(self, provider: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                delete(ProviderCredential).where(
                    ProviderCredential.provider == provider
                )
            )
            await session.commit()
            return bool(result.rowcount)

    # --- selection ------------------------------------------------------

    async def selection(self) -> Selection | None:
        async with self._session() as session:
            row = await session.get(AppSetting, SELECTION_KEY)
            return Selection.decode(row.value) if row and row.value else None

    async def select(self, selection: Selection) -> Selection:
        async with self._session() as session:
            row = await session.get(AppSetting, SELECTION_KEY)
            if row is None:
                row = AppSetting(key=SELECTION_KEY, value="")
                session.add(row)
            row.value = selection.encode()
            await session.commit()
            return selection


__all__ = [
    "Credential",
    "RedactedCredential",
    "Selection",
    "ProviderStore",
    "SELECTION_KEY",
]
