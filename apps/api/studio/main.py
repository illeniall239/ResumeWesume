"""Application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from studio.config import settings
from studio.persistence.repo import DocumentRepo
from studio.routers import documents, export

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    repo = DocumentRepo(settings.resolved_database_url())
    await repo.create_schema()
    app.state.repo = repo
    logger.info("Studio API ready")
    try:
        yield
    finally:
        await repo.dispose()


app = FastAPI(title="Resume Studio API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The browser cannot read ETag on a cross-origin response unless it is
    # exposed, and without it every direct edit would have to refetch first.
    expose_headers=["ETag"],
)

app.include_router(documents.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    return {"status": "healthy"}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "studio.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )
