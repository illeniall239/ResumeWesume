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

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
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

    # Kept only to power date-precision recovery at import time; never consulted
    # during editing.
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
