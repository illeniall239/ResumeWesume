"""Failure policy for outbound model calls.

A local Ollama and a hosted API fail in different ways and on different
timescales, so the policy is explicit rather than implied by scattered
try/excepts.

**Circuit breaker.** When a provider is down, every request pays the full
connect timeout before failing. Under a burst that converts one outage into a
queue of stalled requests. The breaker fails fast after a threshold and probes
with a single request after a cooldown, so recovery is automatic and cheap.

**Classified retries.** Retrying an authentication failure is pointless and
retrying a content-policy refusal is worse. Only transient classes are retried,
with jittered exponential backoff so a fleet does not synchronise into a
thundering herd on recovery.

**Timeouts scale with the work.** A 14B local model legitimately takes minutes
where a hosted flash model takes seconds; one wall-clock number cannot serve
both. Streaming additionally needs an *idle* timeout rather than a total one, or
a long healthy generation is killed for taking a long time.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class FailureClass(StrEnum):
    """How a failure should be treated. The taxonomy drives every decision."""

    TRANSIENT = "transient"  # network blip, 5xx, timeout — retry
    RATE_LIMIT = "rate_limit"  # back off harder, then retry
    AUTH = "auth"  # bad key — never retry, tell the user
    BAD_REQUEST = "bad_request"  # our bug — never retry
    CONTENT = "content"  # refusal — never retry
    UNAVAILABLE = "unavailable"  # provider down — breaker territory


_RETRYABLE = {FailureClass.TRANSIENT, FailureClass.RATE_LIMIT, FailureClass.UNAVAILABLE}


def classify(error: Exception) -> FailureClass:
    """Map a provider exception onto the taxonomy.

    String matching is unfortunate but unavoidable: litellm normalises some
    provider errors and passes others through, so the exception type alone is
    not a reliable signal.
    """
    text = str(error).lower()
    name = type(error).__name__.lower()

    if "authentication" in name or "unauthorized" in text or "api key" in text:
        return FailureClass.AUTH
    if "ratelimit" in name or "rate limit" in text or "429" in text:
        return FailureClass.RATE_LIMIT
    if "contentpolicy" in name or "content_policy" in text:
        return FailureClass.CONTENT
    if "badrequest" in name or "invalid_request" in text or "400" in text:
        return FailureClass.BAD_REQUEST
    if (
        "connection" in text
        or "refused" in text
        or "unavailable" in text
        or "503" in text
    ):
        return FailureClass.UNAVAILABLE
    if "timeout" in name or "timed out" in text:
        return FailureClass.TRANSIENT
    return FailureClass.TRANSIENT


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    # Full jitter. Without it, N clients that failed together retry together and
    # re-create the load that caused the failure.
    jitter: bool = True

    def delay_for(self, attempt: int, failure: FailureClass) -> float:
        base = self.base_delay * (2**attempt)
        if failure is FailureClass.RATE_LIMIT:
            base *= 2
        capped = min(base, self.max_delay)
        return random.uniform(0, capped) if self.jitter else capped


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """One breaker per provider.

    Keyed per provider deliberately: a dead local Ollama must not stop a
    configured cloud fallback from being tried.
    """

    failure_threshold: int = 5
    cooldown_seconds: float = 30.0

    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)

    @property
    def state(self) -> BreakerState:
        if self._state is BreakerState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                # Let exactly one request through to test the water.
                self._state = BreakerState.HALF_OPEN
        return self._state

    def allows(self) -> bool:
        return self.state is not BreakerState.OPEN

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._state = BreakerState.CLOSED

    def record_failure(self, failure: FailureClass) -> None:
        # A bad key or a malformed request says nothing about provider health;
        # counting them would trip the breaker on our own bug.
        if failure in {FailureClass.AUTH, FailureClass.BAD_REQUEST, FailureClass.CONTENT}:
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = BreakerState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit opened after %s consecutive failures", self._failures
            )

    def reset(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._state = BreakerState.CLOSED


class CircuitOpen(Exception):
    """Fail fast: the provider is known-bad and the cooldown has not elapsed."""


async def call_with_policy(
    operation: Callable[[], Awaitable[T]],
    *,
    breaker: CircuitBreaker,
    retry: RetryPolicy | None = None,
    on_retry: Callable[[int, FailureClass, float], None] | None = None,
) -> T:
    """Run ``operation`` under the breaker and retry policy."""
    retry = retry or RetryPolicy()

    if not breaker.allows():
        raise CircuitOpen(
            "The model provider is unavailable. Retrying automatically shortly."
        )

    last: Exception | None = None
    for attempt in range(retry.max_attempts):
        try:
            result = await operation()
            breaker.record_success()
            return result
        except Exception as error:  # noqa: BLE001 - classified immediately below
            failure = classify(error)
            breaker.record_failure(failure)
            last = error

            if failure not in _RETRYABLE or attempt == retry.max_attempts - 1:
                raise

            delay = retry.delay_for(attempt, failure)
            if on_retry:
                on_retry(attempt, failure, delay)
            logger.info(
                "Retrying after %s in %.2fs (attempt %s/%s)",
                failure,
                delay,
                attempt + 1,
                retry.max_attempts,
            )
            await asyncio.sleep(delay)

    assert last is not None
    raise last


def timeout_for(
    *, operation: str, provider: str, max_tokens: int = 2048
) -> float:
    """Seconds to allow, scaled by work and provider.

    Local inference is roughly an order of magnitude slower than a hosted flash
    model on the same prompt; a single constant either kills healthy local runs
    or lets hosted failures hang.
    """
    base = {"health": 15.0, "chat": 120.0, "tools": 180.0}.get(operation, 120.0)
    token_factor = max(1.0, max_tokens / 2048)
    provider_factor = {
        "ollama": 4.0,
        "openai_compatible": 3.0,
        "openrouter": 1.5,
        "anthropic": 1.2,
        "gemini": 1.0,
        "openai": 1.0,
        "groq": 0.8,
    }.get(provider, 1.5)
    return base * token_factor * provider_factor


# Streaming is bounded by silence, not by duration: a long generation that keeps
# producing tokens is healthy, while one that stops mid-flight is not.
STREAM_IDLE_TIMEOUT = 90.0
