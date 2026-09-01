"""Getting structured JSON out of a model that only streams text.

``ChatBackend`` streams text and has no JSON mode. We asked for one anyway and
measured the answer: ``response_format={"type": "json_object"}`` reaches
litellm's ``ollama_chat`` provider and does nothing at all -- the model keeps
its ordinary sampling and, because ``litellm.drop_params`` is on, no error says
so. So the parser below is not a fallback for JSON mode; it is the mechanism.

``repair_json`` from the agent's salvage ladder does most of the work, since it
was built from exactly this kind of output. Two shapes it does not cover, both
observed from qwen3 on the fixtures in this repo:

*A bare top-level array.* ``repair_json`` returns a dict or nothing, so
``[{...}, {...}]`` -- the obvious reply to "list the jobs" -- is not merely
missed. It finds the first balanced object *inside* the array and returns that,
which is one job, and which validates against the section schema as a section
with no jobs in it. A silent, plausible, total loss of a work history.

*A single entry with no wrapper.* Asked for ``{"entries": [...]}`` from a
section holding one school, the model returns the school. Same failure mode:
valid, empty, wrong.

Both are handled by candidate ranking rather than by a cleverer parser --
several readings are tried and one that carries content beats one that merely
parses. Reasoning markers are stripped in text space as well, for providers
that inline ``<think>`` in ``content`` rather than separating it; Ollama
separates it, but that is a property of one provider's template, not a promise.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from studio.agent.salvage import repair_json
from studio.ingest.prompts import max_tokens_for, messages_for
from studio.ingest.schemas import _Section
from studio.llm.backend import BackendError, ChatBackend, StreamEnd, TextDelta

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=_Section)

# One stalled generation must not hold the whole import open. Generous, because
# a long work history on a 14B model legitimately takes a while.
SECTION_TIMEOUT = 120.0

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.I)
_THINK_OPEN = re.compile(r"<\|?think(?:ing)?\|?>", re.I)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.M)


def strip_reasoning(text: str) -> str:
    """Remove a reasoning preamble, closed or not.

    The unclosed case is not hypothetical: a model that hits its token budget
    mid-thought, or one whose template opens the block without closing it,
    leaves a ``<think>`` with the answer somewhere after it. Dropping only
    closed blocks would leave the reasoning in, and reasoning about JSON is
    full of braces that parse before the real object does.
    """
    cleaned = _THINK_BLOCK.sub(" ", text)
    if _THINK_OPEN.search(cleaned):
        # Unclosed: keep only what follows the last opener, which is where an
        # answer would be if there is one.
        cleaned = _THINK_OPEN.split(cleaned)[-1]
    cleaned = cleaned.replace("</think>", " ")
    return cleaned.strip()


def _balanced_spans(text: str, opener: str, closer: str) -> list[str]:
    """Every top-level balanced span, string-aware, in order of appearance."""
    spans: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for position, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == opener:
            if depth == 0:
                start = position
            depth += 1
        elif character == closer and depth:
            depth -= 1
            if depth == 0:
                spans.append(text[start : position + 1])
    return spans


def _load(text: str) -> Any:
    """Parse JSON, falling back to a Python literal."""
    import ast
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None


def _payloads(cleaned: str) -> list[Any]:
    """Candidate payloads, best guess first.

    Two shapes make a single "parse it" call insufficient.

    A bare top-level array is what a model most often returns when asked to
    list jobs, and ``repair_json`` cannot return one -- it yields dicts only.
    Worse, it does not simply miss: it finds the first balanced object *inside*
    the array and returns that, which is one job, and which validates against
    the section schema as a section containing no jobs at all. So the array has
    to be tried before, not after.

    A reasoning preamble the model never closed leaves decoy objects in front
    of the real answer, so the last balanced object is tried as well as the
    first.
    """
    defenced = _FENCE.sub("", cleaned).strip()
    candidates: list[Any] = []

    arrays = _balanced_spans(defenced, "[", "]")
    objects = _balanced_spans(defenced, "{", "}")

    if defenced.startswith("[") and arrays:
        candidates.append(_load(arrays[0]))

    repaired = repair_json(defenced)
    if repaired is not None:
        candidates.append(repaired)

    # Reasoning first, answer last: try from the end as well.
    for span in reversed(objects):
        candidates.append(_load(span))
    for span in reversed(arrays):
        candidates.append(_load(span))

    return [item for item in candidates if item is not None]


def parse_json_into(raw: str, model: type[T]) -> T | None:
    """Turn a model's reply into a validated section, or ``None``.

    Never raises: every failure here is one section landing empty, which the
    pipeline reports and the review screen shows next to its source text.

    A candidate that validates but carries nothing is kept only as a fallback.
    Parsing is not the goal -- a payload that yields zero jobs from a section
    full of jobs has parsed successfully and is still the wrong object.
    """
    cleaned = strip_reasoning(raw)
    if not cleaned:
        return None

    fallback: T | None = None
    for payload in _payloads(cleaned):
        try:
            parsed = model.model_validate(model.house(payload))
        except ValidationError as error:
            logger.debug("section payload failed validation: %s", error)
            continue
        if not parsed.is_empty:
            return parsed
        if fallback is None:
            fallback = parsed
    return fallback


async def complete_json(
    backend: ChatBackend,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 1200,
    temperature: float = 0.0,
) -> str:
    """Run one completion and return its text.

    Temperature zero by default: this is a transcription task, and sampling
    variety in a transcription is just a chance to get a date wrong.
    """
    text: list[str] = []
    async for chunk in backend.stream(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        # Reasoning is worse than useless here. This is transcription, and on
        # qwen3 the model spends several hundred tokens deliberating before its
        # first character of output -- so a budget sized for the answer is
        # exhausted mid-thought and the section returns nothing. Measured on
        # one education section: 35.2s and a token-limit failure with reasoning
        # on, 4.0s and correct output with it off.
        think=False,
    ):
        if isinstance(chunk, TextDelta):
            text.append(chunk.text)
        elif isinstance(chunk, StreamEnd):
            break
    return "".join(text)


async def parse_section(
    backend: ChatBackend,
    source_text: str,
    *,
    kind: str,
    model: type[T],
    timeout: float = SECTION_TIMEOUT,
) -> tuple[T | None, str | None]:
    """Extract one section, returning ``(parsed, failure_code)``.

    Never raises. Containment is the whole point of parsing section by section:
    a provider error, a timeout, or output that is not JSON costs exactly the
    section it happened in, and the rest of the resume still imports.
    """
    if not source_text.strip():
        return None, "empty"

    try:
        raw = await asyncio.wait_for(
            complete_json(
                backend,
                messages_for(kind, source_text),
                max_tokens=max_tokens_for(source_text),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("section %s timed out after %ss", kind, timeout)
        return None, "timeout"
    except BackendError as error:
        logger.warning("section %s failed at the provider: %s", kind, error)
        return None, "provider_error"
    except Exception as error:  # noqa: BLE001 - one section must not end an import
        logger.exception("section %s raised unexpectedly: %s", kind, error)
        return None, "provider_error"

    parsed = parse_json_into(raw, model)
    if parsed is None:
        return None, "no_json"
    return parsed, None
