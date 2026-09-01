"""Document storage with compare-and-set writes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from studio.doc.apply import OpContext, apply_ops, invert
from studio.doc.hashing import content_hash, etag
from studio.doc.migrate import load_doc
from studio.doc.history import VersionGroup, version_to_redo, version_to_undo
from studio.doc.ops import AppliedOp, DocOp, RejectedOp
from studio.doc.schema import StudioDoc
from studio.persistence.models import Asset, Base, Checkpoint, Document, DocumentOp


class VersionConflict(Exception):
    """Raised when a write's expected version no longer matches.

    Carries the ops applied since the client's base version so the caller can
    rebase rather than refetch and lose the user's in-flight typing.
    """

    def __init__(self, current_version: int, ops_since: list[dict[str, Any]]) -> None:
        super().__init__(f"Document moved to version {current_version}")
        self.current_version = current_version
        self.ops_since = ops_since


@dataclass
class DocumentState:
    id: str
    doc: StudioDoc
    version: int
    content_hash: str
    title: str
    settings: dict[str, Any] | None = None

    @property
    def etag(self) -> str:
        return etag(self.version, self.content_hash)


class DocumentRepo:
    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url, future=True)
        self._session = async_sessionmaker(self._engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self._engine.dispose()

    # --- assets -----------------------------------------------------------
    #
    # Content-addressed, so `store` is idempotent: the same bytes uploaded
    # twice return the same row rather than making a second copy. That falls
    # out of using the hash as the primary key and is worth having -- the same
    # headshot on three documents is one row.

    async def store_asset(
        self,
        *,
        sha256: str,
        document_id: str | None,
        mime: str,
        data: bytes,
        width: int,
        height: int,
        filename: str = "",
    ) -> Asset:
        async with self._session() as session:
            existing = await session.get(Asset, sha256)
            if existing is not None:
                return existing

            asset = Asset(
                id=sha256,
                document_id=document_id,
                mime=mime,
                data=data,
                width=width,
                height=height,
                byte_size=len(data),
                filename=filename[:255],
            )
            session.add(asset)
            await session.commit()
            await session.refresh(asset)
            return asset

    async def get_asset(self, asset_id: str) -> Asset | None:
        async with self._session() as session:
            return await session.get(Asset, asset_id)

    async def create(
        self,
        doc: StudioDoc,
        *,
        title: str = "Untitled resume",
        source_markdown: str | None = None,
    ) -> DocumentState:
        document_id = str(uuid.uuid4())
        digest = content_hash(doc)
        async with self._session() as session:
            session.add(
                Document(
                    id=document_id,
                    title=title,
                    version=1,
                    content_hash=digest,
                    doc=doc.model_dump(mode="json"),
                    source_markdown=source_markdown,
                )
            )
            await session.commit()
        return DocumentState(
            id=document_id, doc=doc, version=1, content_hash=digest, title=title
        )

    async def get(self, document_id: str) -> DocumentState | None:
        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                return None
            return DocumentState(
                id=row.id,
                doc=load_doc(row.doc),
                version=row.version,
                content_hash=row.content_hash,
                title=row.title,
                settings=row.settings,
            )

    async def list(self) -> list[DocumentState]:
        async with self._session() as session:
            rows = (
                await session.execute(select(Document).order_by(Document.updated_at.desc()))
            ).scalars()
            return [
                DocumentState(
                    id=row.id,
                    doc=load_doc(row.doc),
                    version=row.version,
                    content_hash=row.content_hash,
                    title=row.title,
                    settings=row.settings,
                )
                for row in rows
            ]

    async def delete(self, document_id: str) -> bool:
        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def apply(
        self,
        document_id: str,
        ops: list[DocOp],
        *,
        expected_version: int | None = None,
        ctx: OpContext | None = None,
        turn_id: str | None = None,
    ) -> tuple[DocumentState, list[AppliedOp], list[RejectedOp]]:
        """Apply ops under compare-and-set.

        ``expected_version=None`` means "I have not read this document, apply
        regardless" — used by the agent loop, which reads immediately before
        writing inside one turn. A client editing directly always passes one.
        """
        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                raise KeyError(document_id)

            if expected_version is not None and row.version != expected_version:
                raise VersionConflict(
                    row.version,
                    await self._ops_since(session, document_id, expected_version),
                )

            current = load_doc(row.doc)
            updated, applied, rejected = apply_ops(current, ops, ctx)

            if not applied:
                # Nothing changed: do not burn a version, or every rejected
                # agent batch would spuriously invalidate the client's ETag.
                return (
                    DocumentState(
                        id=row.id,
                        doc=current,
                        version=row.version,
                        content_hash=row.content_hash,
                        title=row.title,
                        settings=row.settings,
                    ),
                    applied,
                    rejected,
                )

            new_version = row.version + 1
            digest = content_hash(updated)

            # Compare-and-set: a concurrent writer that slipped in between the
            # read above and here loses, and the row count tells us so.
            result = await session.execute(
                update(Document)
                .where(Document.id == document_id, Document.version == row.version)
                .values(
                    doc=updated.model_dump(mode="json"),
                    version=new_version,
                    content_hash=digest,
                )
            )
            if result.rowcount == 0:
                await session.rollback()
                fresh = await session.get(Document, document_id)
                raise VersionConflict(
                    fresh.version if fresh else row.version,
                    await self._ops_since(session, document_id, row.version),
                )

            for seq, entry in enumerate(applied):
                inverse = None
                try:
                    op_model = _rebuild_op(entry.op)
                    inverted = invert(op_model) if op_model else None
                    inverse = inverted.model_dump(mode="json") if inverted else None
                except Exception:  # pragma: no cover - inverse is best-effort
                    inverse = None
                session.add(
                    DocumentOp(
                        document_id=document_id,
                        version=new_version,
                        seq=seq,
                        op=entry.op,
                        inverse=inverse,
                        actor=(ctx.actor if ctx else "user"),
                        turn_id=turn_id,
                    )
                )

            await session.commit()

        return (
            DocumentState(
                id=document_id,
                doc=updated,
                version=new_version,
                content_hash=digest,
                title=row.title,
                settings=row.settings,
            ),
            applied,
            rejected,
        )

    async def reverse(
        self, document_id: str, *, direction: Literal["undo", "redo"]
    ) -> tuple[DocumentState, int] | None:
        """Undo or redo one committed batch.

        Returns ``None`` when there is nothing to reverse, which the router
        turns into a 409 rather than an error — "nothing to undo" is a normal
        state, not a failure.

        Reversal goes through ``apply_ops`` like every other write. It is not a
        snapshot restore: the inverses are ops, so they pass the same gates, log
        their own inverses, and become redoable in turn. That is what lets redo
        be nothing more than "undo the undo".
        """
        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                raise KeyError(document_id)

            groups = await self._version_groups(session, document_id)
            chooser = version_to_undo if direction == "undo" else version_to_redo
            target = chooser(groups)
            if target is None:
                return None

            rows = (
                await session.execute(
                    select(DocumentOp)
                    .where(
                        DocumentOp.document_id == document_id,
                        DocumentOp.version == target,
                    )
                    .order_by(DocumentOp.seq.desc())
                )
            ).scalars().all()

            # Reverse seq order: the last op applied is the first undone, or a
            # remove-then-insert pair would restore into a list that has not
            # been put back yet.
            ops: list[DocOp] = []
            for entry in rows:
                if entry.inverse is None:
                    return None  # not fully invertible; refuse rather than half-undo
                rebuilt = _rebuild_op(entry.inverse)
                if rebuilt is None:
                    return None
                ops.append(rebuilt)

        if not ops:
            return None

        state, applied, rejected = await self.apply(
            document_id,
            ops,
            ctx=OpContext(granted_tiers={"A", "B", "C"}, actor=direction),
        )
        if rejected or not applied:
            return None
        return state, target

    async def _version_groups(
        self, session: AsyncSession, document_id: str
    ) -> list[VersionGroup]:
        rows = (
            await session.execute(
                select(DocumentOp.version, DocumentOp.actor)
                .where(DocumentOp.document_id == document_id)
                .distinct()
            )
        ).all()
        return [VersionGroup(version, actor) for version, actor in rows]

    async def replace(
        self, document_id: str, doc: StudioDoc, *, reason: str = "replace"
    ) -> DocumentState:
        """Overwrite the document wholesale, bumping the version.

        Used only by the drift guards, which correct a document *after* ops
        have already been applied and so cannot express their correction as
        ops against the version they started from. Deliberately not exposed
        through the API: every other write goes through ``apply`` so that it
        passes the gates and lands in the ops log.
        """
        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                raise KeyError(document_id)

            digest = content_hash(doc)
            row.doc = doc.model_dump(mode="json")
            row.version += 1
            row.content_hash = digest
            session.add(
                DocumentOp(
                    document_id=document_id,
                    version=row.version,
                    seq=0,
                    op={"op": "replace", "reason": reason},
                    inverse=None,
                    actor="system",
                )
            )
            await session.commit()

            return DocumentState(
                id=document_id,
                doc=doc,
                version=row.version,
                content_hash=digest,
                title=row.title,
                settings=row.settings,
            )

    async def checkpoint(
        self, document_id: str, *, label: str = "", turn_id: str | None = None
    ) -> str:
        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                raise KeyError(document_id)
            checkpoint_id = str(uuid.uuid4())
            session.add(
                Checkpoint(
                    id=checkpoint_id,
                    document_id=document_id,
                    version=row.version,
                    doc=row.doc,
                    label=label,
                    turn_id=turn_id,
                )
            )
            await session.commit()
            return checkpoint_id

    async def revert(self, document_id: str, checkpoint_id: str) -> DocumentState:
        """Restore a snapshot as a *new* version.

        History stays append-only: reverting never rewinds ``version``, so a
        client holding an old ETag still gets a conflict rather than silently
        appearing to be up to date.
        """
        async with self._session() as session:
            snapshot = await session.get(Checkpoint, checkpoint_id)
            row = await session.get(Document, document_id)
            if snapshot is None or row is None or snapshot.document_id != document_id:
                raise KeyError(checkpoint_id)

            restored = load_doc(snapshot.doc)
            digest = content_hash(restored)
            row.doc = snapshot.doc
            row.version += 1
            row.content_hash = digest
            await session.commit()

            return DocumentState(
                id=document_id,
                doc=restored,
                version=row.version,
                content_hash=digest,
                title=row.title,
                settings=row.settings,
            )

    async def _ops_since(
        self, session: AsyncSession, document_id: str, version: int
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(DocumentOp)
                .where(
                    DocumentOp.document_id == document_id,
                    DocumentOp.version > version,
                )
                .order_by(DocumentOp.version, DocumentOp.seq)
            )
        ).scalars()
        return [row.op for row in rows]


def _rebuild_op(raw: dict[str, Any]) -> DocOp | None:
    """Re-parse a serialized op back into its model, for inversion."""
    from pydantic import TypeAdapter

    try:
        return TypeAdapter(DocOp).validate_python(raw)
    except Exception:
        return None
