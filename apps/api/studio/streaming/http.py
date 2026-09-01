"""Turning a channel into an HTTP response.

Shared by the turn and import routers, which stream the same protocol over the
same transport and differ only in what fills the channel. Lifted here rather
than copied so that a fix to the buffering headers -- the kind of thing found
once, painfully, against a real proxy -- lands in both.
"""

from __future__ import annotations

from typing import AsyncIterator

from studio.streaming.channel import TurnChannel

# Chunked NDJSON. Buffering proxies are the enemy of a streaming UI, and
# X-Accel-Buffering is the one nginx respects.
STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

MEDIA_TYPE = "application/x-ndjson"


def ndjson(channel: TurnChannel, *, from_seq: int = 0) -> AsyncIterator[bytes]:
    """Stream a channel's events as newline-delimited JSON."""

    async def generate() -> AsyncIterator[bytes]:
        async for event in channel.drain(from_seq=from_seq):
            yield event.line().encode("utf-8")

    return generate()
