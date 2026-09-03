"""Database schema.

Two deliberate departures from Resume-Matcher's tables:

**One source of truth.** The old ``resumes`` row kept ``content`` (a JSON
string) and ``processed_data`` (the parsed dict) as redundant copies that every
writer had to keep in sync by hand. Here ``doc`` is the only representation;
markdown and the legacy shape are derived on demand.

**Real optimistic concurrency.** The old ``update_resume`` was a setattr loop
with no version check, so two concurrent writers silently clobbered each other.
A monotonic ``version`` plus a compare-and-set update turns that into a 409 the
client can rebase against, which is what makes it safe for the agent and the
user's cursor to edit at the same time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(300), default="Untitled resume")

    # Bumped on every committed batch. The compare-and-set target.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    doc: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Render settings (template, page size, margins). Separate from the document
    # because changing a template is not an edit to its content.
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    # The text a document was imported from, when it came from an uploaded file
    # rather than a template. Never consulted during editing: it is here so that
    # a later question about what the source actually said -- a mangled date, a
    # bullet the parser dropped -- can be answered from the original rather than
    # guessed at. Named "markdown" from when import was expected to go through a
    # markdown converter; it now holds extracted plain text. Not renamed because
    # schema creation is ``create_all`` with no migration tooling, so a rename
    # would silently no-op on existing databases and then fail at insert.
    source_markdown: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class DocumentOp(Base):
    """Append-only log of applied ops.

    Serves three purposes at once, which is why it is worth the write: it is the
    audit trail, the source of ``ops_since`` for a 409 rebase, and the undo
    stack (each row carries its own inverse).
    """

    __tablename__ = "document_ops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    # The document version this op produced.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    op: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    inverse: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    actor: Mapped[str] = mapped_column(String(16), default="user")
    turn_id: Mapped[str | None] = mapped_column(String(36), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Checkpoint(Base):
    """A full snapshot taken before a turn's first mutation.

    Undo for an AI turn restores one of these, which is why an agent turn is a
    single undo unit rather than N separate bullet edits.
    """

    __tablename__ = "checkpoints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    doc: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    label: Mapped[str] = mapped_column(String(200), default="")
    turn_id: Mapped[str | None] = mapped_column(String(36), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ProviderCredential(Base):
    """An API key for one provider, plus where to reach it.

    **Write-only from the outside.** ``api_key`` is never returned by any
    endpoint, never logged, and never sent to the browser; the API answers with
    a boolean and the last four characters, which is enough for a person to
    recognise which key is installed and useless to anyone who intercepts it.
    That asymmetry is the whole design, and it is why the column lives here
    rather than in the client's local storage.

    Stored as written, in the application's own SQLite file. That file is
    gitignored and sits on the user's machine beside the resume itself, so
    encrypting it here would protect against nothing an attacker holding the
    file could not already read -- the key would have to live next to the
    ciphertext. Real protection is the OS keychain, which is a deliberate
    non-goal for a single-user local app; see docs. What this *does* guarantee
    is that a key never leaves the machine except to the provider it belongs to.

    One row per provider, keyed by the provider id, because a second key for
    the same provider is a replacement rather than an addition.
    """

    __tablename__ = "provider_credentials"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)

    api_key: Mapped[str] = mapped_column(Text, default="")
    #: Overrides the catalogue's default. Set for a local runtime on a
    #: non-standard port, or for an OpenAI-compatible server.
    api_base: Mapped[str | None] = mapped_column(String(300), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class AppSetting(Base):
    """A single application-wide value, as a string.

    Currently one row: which provider and model the assistant uses. A table
    rather than a column on ``documents`` because the selection governs the
    importer too, and an import has no document to hang a setting on until the
    user accepts what it produced.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class Asset(Base):
    """A stored image, addressed by content hash.

    Deliberately its own table rather than a field on ``documents``. The ``doc``
    JSON is deep-copied on every op batch, rehashed by ``content_hash`` and
    walked by four drift guards; a 2MB photo living inside it would make every
    keystroke pay for itself. The document holds only an id.

    Rows are **content-addressed**: ``id`` is the sha256 of the sanitised bytes,
    so uploading the same headshot to three documents stores it once and a
    re-upload is idempotent. ``document_id`` records which document first
    introduced it -- provenance for a cleanup pass, not ownership, since the
    same bytes may now be referenced from several places.

    Nothing here is ever mutated. An edited image is different bytes and
    therefore a different row.
    """

    __tablename__ = "assets"

    #: sha256 of `data`, hex. Not a UUID: the hash *is* the identity.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )

    #: Always one of the three we re-encode to; never the browser's claim.
    mime: Mapped[str] = mapped_column(String(40), nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    #: Pixel dimensions after sanitising, so a placed image can be given its
    #: true aspect ratio without decoding the bytes again.
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)

    #: What the user called it. Display only -- never used to build a path.
    filename: Mapped[str] = mapped_column(String(255), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

class ChatMessage(Base):
    """One turn of the sidebar conversation.

    Stored on the server rather than in the browser because it is not only
    something to redraw. Every turn sends the last few exchanges to the model,
    and a chat that lived in memory meant a page reload silently emptied that
    history -- the assistant would ask again for dates the person had already
    given it, mid-task, with no sign anything had been lost.

    Deliberately narrow: role, text, and how the turn ended. The tool chips the
    sidebar draws while a turn runs are reconstructed by the client's event
    reducer, and a second reduction here would be a copy of that logic drifting
    out of step with it. What a tool call *did* is in the document and its op
    log, which outlive any transcript.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    #: How the turn this message belongs to ended: ok, partial, failed,
    #: cancelled. Null for the user's own message, which does not end.
    status: Mapped[str | None] = mapped_column(String(16), default=None)
    turn_id: Mapped[str | None] = mapped_column(String(36), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
