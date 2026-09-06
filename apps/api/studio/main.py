"""Application entry point and composition root.

Dependencies are constructed once at startup and hung off ``app.state``, then
read by routers from the request. No module-level singletons: a global is
invisible to tests, impossible to swap per request, and outlives the event loop
it was built for.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from studio.config import settings
from studio.llm.factory import BackendFactory, health
from studio.persistence.providers import ProviderStore
from studio.persistence.repo import DocumentRepo
from studio.routers import (
    assets,
    canvases,
    documents,
    export,
    ingest,
    providers,
    turns,
)
from studio.streaming.channel import TurnRegistry

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
)
logger = logging.getLogger(__name__)


class RequestIdFilter(logging.Filter):
    """Ensures every record has a request_id, so the format string is safe.

    Without a default, any log line emitted outside a request (startup, a
    background turn) raises a formatting error and is silently dropped, which
    loses exactly the lines you need when debugging startup.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


for handler in logging.getLogger().handlers:
    handler.addFilter(RequestIdFilter())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    repo = DocumentRepo(settings.resolved_database_url())
    await repo.create_schema()

    app.state.repo = repo
    # Shares the repository's engine: same file, one connection pool.
    app.state.providers = ProviderStore(repo.session_factory)
    app.state.turns = TurnRegistry()
    # A registry of its own, so an import and a turn cannot collide on an id
    # and so shutting one down never touches the other.
    app.state.imports = TurnRegistry()
    app.state.backends = BackendFactory()

    logger.info(
        "Studio API ready (provider=%s model=%s)",
        settings.llm_provider,
        settings.llm_model,
    )
    try:
        yield
    finally:
        # Stop in-flight turns before the database goes away, or a turn's final
        # write lands on a disposed engine.
        await app.state.turns.shutdown()
        await app.state.imports.shutdown()
        await repo.dispose()


app = FastAPI(title="Resume Studio API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    # The browser cannot read these on a cross-origin response unless they are
    # exposed. ETag drives optimistic concurrency; X-Turn-Id and X-Import-Id
    # let a client reattach to a stream it lost.
    expose_headers=["ETag", "X-Turn-Id", "X-Import-Id"],
)


@app.middleware("http")
async def observability(request: Request, call_next):
    """Correlation id and timing on every request.

    One id ties an HTTP request to the turn it started and to every log line
    either produced, which is the difference between debugging a distributed
    flow and guessing at it.
    """
    request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    started = time.monotonic()

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unhandled error on %s %s",
            request.method,
            request.url.path,
            extra={"request_id": request_id},
        )
        # Detail server-side, generic to the client: an internal message can
        # carry a path, a query or a key fragment.
        return JSONResponse(
            status_code=500,
            content={"detail": "Something went wrong. Please try again."},
            headers={"X-Request-Id": request_id},
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    response.headers["X-Request-Id"] = request_id

    # Streaming responses have no meaningful duration here; the interesting
    # timing is in the turn's own usage event.
    if elapsed_ms > 1000 and "ndjson" not in response.headers.get("content-type", ""):
        logger.info(
            "%s %s took %sms",
            request.method,
            request.url.path,
            elapsed_ms,
            extra={"request_id": request_id},
        )
    return response


app.include_router(canvases.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")
app.include_router(export.backup_router, prefix="/api/v1")
app.include_router(turns.router, prefix="/api/v1")
app.include_router(ingest.router, prefix="/api/v1")
app.include_router(assets.router, prefix="/api/v1")
app.include_router(providers.router, prefix="/api/v1")


@app.get("/api/v1/health")
async def liveness() -> dict[str, str]:
    """Liveness only: no model call, no database round trip.

    Deliberately cheap so an orchestrator's probe cannot be made to fail by a
    slow dependency, which would restart a process that is actually fine.
    """
    return {"status": "healthy"}


@app.get("/api/v1/health/model")
async def model_health(request: Request) -> dict[str, object]:
    """Readiness for the model path. Makes a real call, so it is slow."""
    # The selected backend, not the configured one: a readiness probe that
    # tests a model the app is not using answers the wrong question.
    backend = await request.app.state.backends.resolve(request.app.state.providers)
    return await health(backend)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "studio.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )
