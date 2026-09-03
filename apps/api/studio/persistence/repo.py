"""Document storage with compare-and-set writes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from studio.doc.apply import OpContext, apply_ops, invert
from studio.doc.hashing import content_hash, etag
from studio.doc.migrate import load_doc
from studio.doc.history import VersionGroup, version_to_redo, version_to_undo
from studio.doc.ops import AppliedOp, DocOp, RejectedOp
from studio.doc.schema import StudioDoc
from studio.persistence.models import (
    Asset,
    Base,
    ChatMessage,
    Checkpoint,
    Document,
    DocumentOp,
)


#: Long enough for "Rao Muhammad Hamza — Senior AI Engineer, Platform", short
#: enough that the column it is stored in cannot be filled with a paste.
MAX_TITLE = 300


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
    #: When this document last changed. Optional because a state built from a
    #: fresh write has not been read back yet, and nothing depends on it there.
    updated_at: datetime | None = None

    @property
    def etag(self) -> str:
        return etag(self.version, self.content_hash)


def _settle_scaffold(
    doc: "StudioDoc", applied: list[AppliedOp], ctx: OpContext | None
) -> "StudioDoc":
    """Record who wrote what while a document was still scaffolding.

    Two things happen at the first real edit, and they happen here because this
    is the one place every write passes through -- the agent loop and a person
    typing both land on ``apply``.

    An **agent** write marks the nodes it touched as unverified: on scaffolding
    the assistant is allowed to invent, and invented text on a résumé may never
    be quiet about itself.

    A write by the **author** ends the scaffolding outright and clears the marks
    it covers. Typing into the document is the moment it stops being a template
    and starts being theirs, and from then on every guarantee applies unchanged.
    """
    if not doc.scaffold:
        return doc

    touched = [nid for op in applied for nid in op.touched]
    actor = ctx.actor if ctx else "agent"  # anything but the author is the agent

    if actor != "user":
        marked = list(doc.unverified)
        marked.extend(nid for nid in touched if nid not in marked)
        return doc.model_copy(update={"unverified": marked})

    # The author has written in it. It is a résumé now.
    remaining = [nid for nid in doc.unverified if nid not in set(touched)]
    return doc.model_copy(update={"scaffold": False, "unverified": remaining})


class DocumentRepo:
    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url, future=True)
        self._session = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def session_factory(self) -> async_sessionmaker:
        """Shared with ``ProviderStore``, so both live on one engine.

        Exposed rather than duplicated: a second ``create_async_engine`` against
        the same SQLite file is a second connection pool competing for the same
        write lock, which is how a settings write starts intermittently timing
        out behind a document write.
        """
        return self._session

    async def create_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self._engine.dispose()


    # --- conversation -----------------------------------------------------
    #
    # The sidebar transcript. Kept here rather than in the browser because the
    # next turn is built from it: a reload used to empty the history sent to the
    # model, so the assistant would ask again for facts it had just been given.

    async def add_messages(
        self,
        document_id: str,
        messages: list[tuple[str, str, str | None]],
        *,
        turn_id: str | None = None,
    ) -> None:
        """Append ``(role, text, status)`` triples to a document's conversation.

        Written once, after the turn settles, so a turn that was cancelled or
        crashed leaves the same trace the user saw rather than a half-message.
        """
        rows = [
            ChatMessage(
                document_id=document_id,
                role=role,
                text=text,
                status=status,
                turn_id=turn_id,
            )
            for role, text, status in messages
            if text.strip()
        ]
        if not rows:
            return

        async with self._session() as session:
            session.add_all(rows)
            await session.commit()

    async def conversation(
        self, document_id: str, *, limit: int = 200
    ) -> list[ChatMessage]:
        """The stored conversation, oldest first.

        Capped because a long-lived document accumulates turns without bound and
        this is read on every page load. The cap takes the *most recent*
        messages and then restores their order, so what falls off is the distant
        past rather than the exchange the person is in the middle of.
        """
        async with self._session() as session:
            result = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.document_id == document_id)
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            )
            return list(reversed(result.scalars().all()))

    async def clear_conversation(self, document_id: str) -> None:
        async with self._session() as session:
            await session.execute(
                delete(ChatMessage).where(ChatMessage.document_id == document_id)
            )
            await session.commit()


    # --- revisions --------------------------------------------------------
    #
    # The marks drawn on the sheet and the readings under them, rebuilt from the
    # op log so they survive a reload. They used to live only in the browser,
    # which put the record of what changed on weaker footing than the
    # conversation about it -- the dialogue came back and the artifact's history
    # did not.

    async def revisions(self, document_id: str) -> dict[str, Any]:
        """The document's revision count, and the marks of its latest issue.

        Counted per document rather than per session: a drawing's revision
        number does not restart because somebody closed it. What is *drawn* is
        still one issue -- the most recent turn -- because that is what the
        clouds mean, and clouding every change a document ever had would cover
        the whole sheet.

        The "before" reading comes from each op's stored inverse, which is the
        only place the outgoing text survives once the new text is in.
        """
        from pydantic import TypeAdapter

        from studio.doc.apply import _touched
        from studio.doc.ops import DocOp

        adapter = TypeAdapter(DocOp)

        async with self._session() as session:
            result = await session.execute(
                select(DocumentOp)
                .where(
                    DocumentOp.document_id == document_id,
                    DocumentOp.turn_id.is_not(None),
                )
                .order_by(DocumentOp.version, DocumentOp.seq)
            )
            rows = list(result.scalars().all())

        turns: list[str] = []
        for row in rows:
            if row.turn_id and (not turns or turns[-1] != row.turn_id):
                if row.turn_id not in turns:
                    turns.append(row.turn_id)

        if not turns:
            return {"revision": 0, "turn_id": None, "marks": []}

        latest = turns[-1]
        marks: list[dict[str, Any]] = []
        seen: dict[str, int] = {}

        for row in rows:
            if row.turn_id != latest:
                continue
            try:
                op = adapter.validate_python(row.op)
            except Exception:  # noqa: BLE001 -- a stored op we can no longer parse
                continue

            for nid in _touched(op):
                # One number per node, minted the first time it changes and kept
                # if a later op in the same issue touches it again -- a region
                # carries one mark however many times it was worked on.
                if nid not in seen:
                    seen[nid] = len(seen) + 1
                    marks.append({"nid": nid, "mark": seen[nid], "before": None})

                entry = next(item for item in marks if item["nid"] == nid)
                if entry["before"] is None:
                    entry["before"] = _outgoing_text(row.inverse)

        return {"revision": len(turns), "turn_id": latest, "marks": marks}

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

    async def list_assets(self, document_id: str) -> list[Asset]:
        """Images uploaded against this document, newest first.

        Needed because the assistant cannot upload anything -- only the person
        can -- so the only images it may place are ones already here. Without a
        listing it had no way to name one, and "put my photo at the top"
        reached nothing.

        Scoped by ``document_id``: rows are content-addressed and shared, but
        what this document introduced is what the person means by "my photo".
        """
        async with self._session() as session:
            result = await session.execute(
                select(Asset)
                .where(Asset.document_id == document_id)
                .order_by(Asset.created_at.desc())
            )
            return list(result.scalars().all())

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

    async def rename(self, document_id: str, title: str) -> DocumentState | None:
        """Give a document a different name.

        Deliberately not an op. A title is *about* the document rather than in
        it: it is not in `doc`, it does not change `content_hash`, and nothing
        renders it onto the page. Routing it through `apply` would put a
        rename in the undo stack between two edits to the résumé, and bump the
        version that every open client is holding as its compare-and-set base
        -- a 409 for everyone, over a word nobody typed into the sheet.
        """
        clean = title.strip()
        if not clean:
            return None

        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                return None
            row.title = clean[:MAX_TITLE]
            await session.commit()

        return await self.get(document_id)

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
                updated_at=row.updated_at,
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
                    updated_at=row.updated_at,
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

            if applied:
                updated = _settle_scaffold(updated, applied, ctx)

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
                updated_at=row.updated_at,
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

    async def confirm_unverified(
        self, document_id: str, nids: set[str] | None = None
    ) -> DocumentState:
        """Clear the invented-text marks the person has checked.

        Not routed through ``apply``, and the reason is a real distinction
        rather than a shortcut: ``scaffold`` and ``unverified`` are not content.
        No op addresses them, they do not appear in the document a person reads,
        and confirming changes not one word -- it records that somebody looked.
        Pushing it through the op path would put an entry in the undo stack for
        an action with nothing to undo.

        The content hash is left alone for the same reason: the résumé is
        byte-for-byte what it was, so a client's ETag stays valid and an export
        in flight does not become stale because a checkbox was ticked.
        """
        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                raise KeyError(document_id)

            doc = load_doc(row.doc)
            confirmed = nids if nids else set(doc.unverified)
            remaining = [nid for nid in doc.unverified if nid not in confirmed]
            # Reading the claims and accepting them is what makes the document
            # yours, so the scaffolding ends here too.
            updated = doc.model_copy(update={"unverified": remaining, "scaffold": False})

            row.doc = updated.model_dump(mode="json")
            await session.commit()

            return DocumentState(
                id=document_id,
                doc=updated,
                version=row.version,
                content_hash=row.content_hash,
                title=row.title,
                settings=row.settings,
                updated_at=row.updated_at,
            )

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
                updated_at=row.updated_at,
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
                updated_at=row.updated_at,
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

def _outgoing_text(inverse: dict[str, Any] | None) -> str | None:
    """What the node said before the op landed.

    Only a genuine replacement has one. An inserted node had no previous
    reading, and offering an empty one invites the user to open a cloud with
    nothing under it -- the same rule the browser applied when it captured this
    itself.
    """
    if not isinstance(inverse, dict):
        return None
    # `set_field` as well as `set_text`: a job title, an employer, a phone
    # number. Those are the changes most worth checking after the fact, and
    # reading only rewrites left every one of them with a mark you could click
    # and nothing under it.
    if inverse.get("op") not in {"set_text", "set_field"}:
        return None
    value = inverse.get("value")
    return value if isinstance(value, str) and value.strip() else None
