"""Document storage with compare-and-set writes."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
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
    Canvas,
    ChatMessage,
    Checkpoint,
    Document,
    DocumentOp,
)


#: Long enough for "Rao Muhammad Hamza — Senior AI Engineer, Platform", short
#: enough that the column it is stored in cannot be filled with a paste.
log = logging.getLogger(__name__)

MAX_TITLE = 300

#: How much of a job posting is kept.
#:
#: Generous, because a real posting runs long and the interesting requirements
#: are as often at the bottom as the top. Capped at all because this text goes
#: into the prompt on every turn, and an unbounded field there is an unbounded
#: bill -- and because `outline()` exists precisely to stop a large prompt from
#: pushing the system prompt off the front of a local model's context.
MAX_JOB_DESCRIPTION = 20_000


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
class CanvasState:
    """A canvas and the boards on it."""

    id: str
    title: str
    boards: list["DocumentState"] = field(default_factory=list)
    updated_at: datetime | None = None


@dataclass
class DocumentState:
    id: str
    doc: StudioDoc
    version: int
    content_hash: str
    title: str
    settings: dict[str, Any] | None = None
    #: The posting this résumé is aimed at, or None. Read on every turn.
    job_description: str | None = None
    #: The canvas this board sits on.
    canvas_id: str | None = None
    #: When this document last changed. Optional because a state built from a
    #: fresh write has not been read back yet, and nothing depends on it there.
    updated_at: datetime | None = None

    @property
    def etag(self) -> str:
        return etag(self.version, self.content_hash)


#: Ops that move something without changing a word.
#:
#: ``tier_of`` already states the principle for ``set_geometry``: "moving a box
#: is not a claim about the person." The scaffolding rule is the same claim, so
#: it has to follow the same line.
#:
#: It matters because the reflow pass posts a batch of these every time a
#: document is *opened*. Counted as the author writing, that ended the
#: scaffolding of every template before its owner had typed a character --
#: which silently took ``SCAFFOLD_NOTE``, ``TurnBudget.for_scaffold`` and the
#: unverified marks with it, none of which announce their absence. Opening a
#: résumé is not writing one.
_LAYOUT_ONLY = frozenset({"set_geometry"})


def _add_missing_columns(connection: Any) -> None:
    """Add columns a model has grown that an existing table does not have yet.

    ``create_all`` creates missing *tables* and is silent about missing
    *columns*, so a field added to a model reaches a fresh database and never
    reaches anybody's existing one -- where the next query then fails with
    "no such column" on a database that was working a minute earlier. There is
    no migration tool in this project, and this is the smallest thing that is
    not one.

    Deliberately additive only, and only for columns that are nullable with no
    server default: those are the ones SQLite can add instantly and safely,
    and they are the only kind whose meaning for existing rows is obvious --
    the row simply does not have one. Anything else (a rename, a type change,
    a NOT NULL) is a real migration and has to be written as one; this will
    not attempt it and will not pretend to have done it.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    present = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in present:
            continue  # create_all has just made it, with every column
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            if not column.nullable or column.server_default is not None:
                log.warning(
                    "column %s.%s is missing and cannot be added automatically; "
                    "it needs a real migration",
                    table.name,
                    column.name,
                )
                continue
            kind = column.type.compile(connection.dialect)
            connection.execute(
                text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}')
            )
            log.info("added column %s.%s", table.name, column.name)


def _state_of(row: Document) -> DocumentState:
    """One stored row as the state everything else reads.

    Written once because three readers build it and they had drifted apart
    before: a field added to the row reached whichever of them the change
    happened to touch.
    """
    return DocumentState(
        id=row.id,
        doc=load_doc(row.doc),
        version=row.version,
        content_hash=row.content_hash,
        title=row.title,
        settings=row.settings,
        job_description=row.job_description,
        canvas_id=row.canvas_id,
        updated_at=row.updated_at,
    )


def _adopt_orphan_documents(connection: Any) -> None:
    """Give every document without a canvas one of its own.

    Boards live on canvases, and every résumé written before canvases existed
    has none -- so without this the register, which lists canvases, would open
    empty on a database full of work. Each becomes a canvas of one board,
    keeping its own name, which is the shape it already had.

    Idempotent by construction: it acts only on rows where ``canvas_id`` is
    null, and leaves none behind. A document created and then orphaned by a
    hand-deleted canvas would be adopted again on the next boot, which is the
    right answer rather than a special case.
    """
    from sqlalchemy import select, update

    orphans = connection.execute(
        select(Document.id, Document.title).where(Document.canvas_id.is_(None))
    ).all()
    if not orphans:
        return

    for document_id, title in orphans:
        canvas_id = str(uuid.uuid4())
        connection.execute(
            Canvas.__table__.insert().values(
                id=canvas_id, title=(title or "Untitled")[:MAX_TITLE]
            )
        )
        connection.execute(
            update(Document).where(Document.id == document_id).values(canvas_id=canvas_id)
        )

    log.info("adopted %d document(s) onto canvases of their own", len(orphans))


def _link_messages_to_canvases(connection: Any) -> None:
    """File every stored message under the canvas its board sits on.

    The transcript moved from the board to the canvas, and a conversation held
    before that move has no canvas on it -- so a résumé full of history would
    open on an empty sidebar, and the history handed to the model would be
    empty too. Runs after the adoption above, which is what gives every board a
    canvas to be filed under.
    """
    from sqlalchemy import text as sql

    changed = connection.execute(
        sql(
            "UPDATE chat_messages SET canvas_id = ("
            "  SELECT documents.canvas_id FROM documents"
            "  WHERE documents.id = chat_messages.document_id"
            ") WHERE canvas_id IS NULL"
        )
    ).rowcount
    if changed:
        log.info("filed %d message(s) under their canvas", changed)


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

    Layout is neither. A batch that only moves frames leaves the document
    exactly as unwritten as it found it, whoever sent it.
    """
    if not doc.scaffold:
        return doc

    wrote = [op for op in applied if op.op.get("op") not in _LAYOUT_ONLY]
    if not wrote:
        return doc

    touched = [nid for op in wrote for nid in op.touched]
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
            await connection.run_sync(_add_missing_columns)
            await connection.run_sync(_adopt_orphan_documents)
            await connection.run_sync(_link_messages_to_canvases)

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
            # The canvas the board sits on, so the conversation is filed with
            # the résumé rather than with one version of it.
            board = await session.get(Document, document_id)
            for row in rows:
                row.canvas_id = board.canvas_id if board else None
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

    async def turn_checkpoints(self, document_id: str) -> dict[str, tuple[str, int]]:
        """Where each stored turn began: ``turn_id`` -> (checkpoint id, version).

        A conversation read back from the database has no memory of the
        snapshot the loop streamed while it was running, so without this
        "undo this turn" is an offer that evaporates on refresh -- and a
        refresh is exactly what somebody does when they are unsure whether an
        edit landed.

        Joined on ``turn_id`` rather than carried on the message: both tables
        already record it, and a message column would be a second copy of a
        fact that can only ever be derived from the checkpoint anyway.
        """
        async with self._session() as session:
            result = await session.execute(
                select(Checkpoint.turn_id, Checkpoint.id, Checkpoint.version)
                .where(Checkpoint.document_id == document_id)
                .where(Checkpoint.turn_id.is_not(None))
            )
            return {
                turn_id: (checkpoint_id, version)
                for turn_id, checkpoint_id, version in result.all()
                if turn_id
            }

    async def canvas_conversation(
        self, canvas_id: str, *, limit: int = 200
    ) -> list[ChatMessage]:
        """Everything said about this résumé, oldest first, across its versions.

        Canvas-wide because a conversation is: you ask for a version aimed at
        one job, read it back, then ask for another. Keyed to the board it
        would split into as many transcripts as there are versions, and the
        history handed to the model would lose everything said about the
        résumé as a whole.

        Capped for the same reason `conversation` is -- this is read on every
        page load -- and the cap keeps the most recent, so what falls off is
        the distant past rather than the exchange somebody is in the middle of.
        """
        async with self._session() as session:
            result = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.canvas_id == canvas_id)
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            )
            return list(reversed(result.scalars().all()))

    async def clear_canvas_conversation(self, canvas_id: str) -> None:
        async with self._session() as session:
            await session.execute(
                delete(ChatMessage).where(ChatMessage.canvas_id == canvas_id)
            )
            await session.commit()

    async def turn_checkpoints_for_canvas(
        self, canvas_id: str
    ) -> dict[str, list[tuple[str, int, str]]]:
        """Every snapshot each turn took, for every board on a canvas.

        ``turn_id`` -> [(checkpoint id, version, document id), ...].

        A *list* per turn, because a turn can move between versions: "add Rust
        to the Stripe one" takes a snapshot of the board it started on and
        another of the board it moved to, and undoing that turn has to put back
        both. Keyed by turn alone this silently kept whichever row the database
        returned last.

        The document id comes back with each because whether a snapshot is
        worth offering depends on how far *that* board has moved since.
        """
        async with self._session() as session:
            result = await session.execute(
                select(Checkpoint.turn_id, Checkpoint.id, Checkpoint.version,
                       Checkpoint.document_id)
                .join(Document, Document.id == Checkpoint.document_id)
                .where(Document.canvas_id == canvas_id)
                .where(Checkpoint.turn_id.is_not(None))
                .order_by(Checkpoint.created_at)
            )
            found: dict[str, list[tuple[str, int, str]]] = {}
            for turn_id, checkpoint_id, version, document_id in result.all():
                if not turn_id:
                    continue
                found.setdefault(turn_id, []).append(
                    (checkpoint_id, version, document_id)
                )
            return found

    async def clear_conversation(self, document_id: str) -> None:
        async with self._session() as session:
            await session.execute(
                delete(ChatMessage).where(ChatMessage.document_id == document_id)
            )
            await session.commit()


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
        canvas_id: str | None = None,
    ) -> DocumentState:
        """Create a board.

        ``canvas_id`` puts it on an existing canvas -- a second version of a
        résumé, aimed at another job. Without one it gets a canvas of its own,
        so no caller has to know canvases exist to make a résumé, and no
        document can be created orphaned.
        """
        document_id = str(uuid.uuid4())
        digest = content_hash(doc)
        async with self._session() as session:
            if canvas_id is None:
                canvas_id = str(uuid.uuid4())
                session.add(Canvas(id=canvas_id, title=title[:MAX_TITLE] or "Untitled"))
            session.add(
                Document(
                    id=document_id,
                    title=title,
                    version=1,
                    content_hash=digest,
                    doc=doc.model_dump(mode="json"),
                    source_markdown=source_markdown,
                    canvas_id=canvas_id,
                )
            )
            await session.commit()
        return DocumentState(
            id=document_id,
            doc=doc,
            version=1,
            content_hash=digest,
            title=title,
            canvas_id=canvas_id,
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

    async def set_job_description(
        self, document_id: str, text: str | None
    ) -> DocumentState | None:
        """Aim a résumé at a posting, or stop aiming it at one.

        Deliberately not an op, for the same reason a title is not: the posting
        is *about* the document rather than in it. It renders nothing, changes
        no word on the page, and moves neither the version nor the content hash
        -- so pasting one does not hand a 409 to every open editor, and does not
        sit in the undo stack between two real edits.

        Empty or whitespace clears it. There is no separate "remove" call
        because "aimed at nothing" is not a different kind of state.
        """
        async with self._session() as session:
            row = await session.get(Document, document_id)
            if row is None:
                return None
            clean = (text or "").strip()
            row.job_description = clean[:MAX_JOB_DESCRIPTION] or None
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
                job_description=row.job_description,
                canvas_id=row.canvas_id,
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
                    job_description=row.job_description,
                    canvas_id=row.canvas_id,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    # --- canvases ---------------------------------------------------------
    #
    # A canvas is what the register lists and what a URL names. Its boards are
    # ordinary documents, which is what keeps ops, undo, export and the agent
    # loop working on a board exactly as they worked on a document.

    async def create_canvas(self, title: str = "Untitled") -> CanvasState:
        canvas_id = str(uuid.uuid4())
        async with self._session() as session:
            session.add(
                Canvas(id=canvas_id, title=(title.strip() or "Untitled")[:MAX_TITLE])
            )
            await session.commit()
        return CanvasState(id=canvas_id, title=title.strip() or "Untitled", boards=[])

    async def list_canvases(self) -> list[CanvasState]:
        """Every canvas, newest activity first, each with its boards.

        Boards come back in full because the register renders them for real --
        the same ``DocumentFlow`` the studio and the PDF use -- so a card
        cannot go stale against the thing it opens.
        """
        async with self._session() as session:
            canvases = (
                await session.execute(select(Canvas).order_by(Canvas.updated_at.desc()))
            ).scalars().all()
            documents = (
                await session.execute(
                    select(Document).order_by(Document.updated_at.desc())
                )
            ).scalars().all()

        boards: dict[str, list[DocumentState]] = {}
        for row in documents:
            if row.canvas_id is None:
                continue
            boards.setdefault(row.canvas_id, []).append(_state_of(row))

        return [
            CanvasState(
                id=canvas.id,
                title=canvas.title,
                updated_at=canvas.updated_at,
                boards=boards.get(canvas.id, []),
            )
            for canvas in canvases
        ]

    async def get_canvas(self, canvas_id: str) -> CanvasState | None:
        async with self._session() as session:
            canvas = await session.get(Canvas, canvas_id)
            if canvas is None:
                return None
            rows = (
                await session.execute(
                    select(Document)
                    .where(Document.canvas_id == canvas_id)
                    .order_by(Document.created_at.asc())
                )
            ).scalars().all()
        return CanvasState(
            id=canvas.id,
            title=canvas.title,
            updated_at=canvas.updated_at,
            boards=[_state_of(row) for row in rows],
        )

    async def rename_canvas(self, canvas_id: str, title: str) -> CanvasState | None:
        clean = title.strip()
        if not clean:
            return None
        async with self._session() as session:
            canvas = await session.get(Canvas, canvas_id)
            if canvas is None:
                return None
            canvas.title = clean[:MAX_TITLE]
            await session.commit()
        return await self.get_canvas(canvas_id)

    async def delete_canvas(self, canvas_id: str) -> bool:
        """Delete a canvas and every board on it.

        The boards go because a board is a version of the résumé this canvas
        holds; keeping them would leave a set of unnamed sheets with nothing
        in common and no way back to each other.

        Deleted row by row rather than left to the foreign key, because SQLite
        enforces ``ON DELETE CASCADE`` only when foreign keys are switched on
        per connection -- so relying on it here would work in one deployment
        and silently orphan every board in another.
        """
        async with self._session() as session:
            canvas = await session.get(Canvas, canvas_id)
            if canvas is None:
                return False
            await session.execute(
                delete(Document).where(Document.canvas_id == canvas_id)
            )
            await session.delete(canvas)
            await session.commit()
        return True

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
                        job_description=row.job_description,
                        canvas_id=row.canvas_id,
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
                job_description=row.job_description,
                canvas_id=row.canvas_id,
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
                job_description=row.job_description,
                canvas_id=row.canvas_id,
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
                job_description=row.job_description,
                canvas_id=row.canvas_id,
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
                job_description=row.job_description,
                canvas_id=row.canvas_id,
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
