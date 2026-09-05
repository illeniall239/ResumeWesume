"""Driving one import, start to finish, onto a channel.

Shaped like ``TurnRunner.run(request, channel)`` on purpose: same signature,
same "the channel always gets closed" discipline, same rule that the runner
owns no transport of its own. A reader who understands one understands the
other.

The order of work is chosen for what the user sees, not for what is convenient.
Every section is announced before any of them is parsed, so the checklist
appears whole and immediately; then the sections that need no model resolve
within a second; only then does the slow part start, one section at a time,
against a checklist the user has already read.

Sequential, not concurrent. Ollama serialises on a single model slot anyway, so
``gather`` would buy no wall clock -- it would just replace a progressing
checklist with four spinners that all finish at once, and multiply the context
held in VRAM while doing it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from studio.doc.legacy import from_resume_data
from studio.ingest import pdf
from studio.ingest.contact import Contact, parse_contact
from studio.ingest.custom import column_right, parse_custom
from studio.ingest.extract import parse_section
from studio.ingest.merge import title_for, to_resume_data
from studio.ingest.schemas import SECTION_SCHEMAS, _Section
from studio.ingest.segment import Segment, segment
from studio.ingest.skills import parse_credentials, parse_skills
from studio.llm.backend import ChatBackend
from studio.streaming.channel import Cancelled, TurnChannel
from studio.streaming.events import (
    ErrorEvent,
    ImportReady,
    ImportStarted,
    SectionFailed,
    SectionFound,
    SectionParsed,
    SectionSkipped,
    SectionStarted,
)

logger = logging.getLogger(__name__)

# Sections read with a regex or a split rather than a model. See
# ``contact.py`` for why: these have exact shapes, and exact beats likely.
_DETERMINISTIC = ("contact", "skills", "languages", "certifications", "awards")

# Short tokens several to a line, versus long entries one to a line. Reading a
# credential as a skill splits it at its own commas and drops it for length.
_TOKEN_SECTIONS = ("skills", "languages")
_CREDENTIAL_SECTIONS = ("certifications", "awards")

_FAILURE_MESSAGES = {
    "no_json": "The model did not return readable data for this section.",
    "invalid_shape": "The model returned data in an unexpected shape.",
    "timeout": "This section took too long to read and was skipped.",
    "provider_error": "The model was unreachable while reading this section.",
    "empty": "This section had no text in it.",
}


@dataclass(frozen=True)
class ImportRequest:
    data: bytes
    filename: str


class ImportRunner:
    """Reads one uploaded resume onto a channel."""

    def __init__(self, *, backend: ChatBackend) -> None:
        self._backend = backend

    async def run(self, request: ImportRequest, channel: TurnChannel) -> None:
        try:
            await self._run(request, channel)
        except Cancelled:
            logger.info("import %s cancelled", channel.turn_id)
        except pdf.ExtractionError as error:
            channel.emit(
                ErrorEvent(code=error.code, message=str(error), fatal=True)
            )
        except Exception as error:  # noqa: BLE001 - surfaced, never swallowed
            logger.exception("import %s failed: %s", channel.turn_id, error)
            channel.emit(
                ErrorEvent(
                    code="internal_error",
                    message="Something went wrong reading this file.",
                    fatal=True,
                )
            )
        finally:
            channel.close()

    async def _run(self, request: ImportRequest, channel: TurnChannel) -> None:
        # pdfminer is CPU-bound and blocking. Run inline and it stalls the
        # event loop for the whole process: every other request, and every
        # heartbeat holding this very connection open.
        extraction = await asyncio.to_thread(pdf.extract, request.data)

        channel.emit(
            ImportStarted(
                filename=request.filename,
                pages=extraction.pages,
                chars=len(extraction.text),
                columns=extraction.columns,
                warnings=extraction.warnings,
            )
        )

        segments = segment(extraction.lines)
        for item in segments:
            channel.emit(
                SectionFound(
                    key=item.key,
                    heading=item.heading,
                    chars=len(item.text),
                    order=item.order,
                    needs_model=item.key in SECTION_SCHEMAS,
                )
            )

        found = {item.key: item for item in segments if item.key != "other"}
        contact = Contact()
        skills: dict[str, list[str]] = {}
        parts: dict[str, _Section | None] = {}
        parsed_count = 0
        failed_count = 0

        # The cheap sections first, so the checklist starts filling in at once.
        for key in _DETERMINISTIC:
            item = found.get(key)
            if item is None:
                continue
            if key == "contact":
                contact = parse_contact(item.lines)
                data = contact.as_personal_info()
            else:
                reader = (
                    parse_credentials
                    if key in _CREDENTIAL_SECTIONS
                    else parse_skills
                )
                skills[key] = reader(item.lines)
                data = {"items": skills[key]}
            parsed_count += 1
            channel.emit(
                SectionParsed(key=key, data=data, source_text=item.text)
            )

        # Then the slow ones, announced individually as they start.
        for key, schema in SECTION_SCHEMAS.items():
            item = found.get(key)
            if item is None:
                continue

            channel.raise_if_cancelled()
            channel.emit(SectionStarted(key=key))
            started = time.monotonic()

            parsed, code = await parse_section(
                self._backend, item.text, kind=key, model=schema
            )
            elapsed = int((time.monotonic() - started) * 1000)

            if parsed is None:
                failed_count += 1
                parts[key] = None
                channel.emit(
                    SectionFailed(
                        key=key,
                        code=code or "no_json",
                        message=_FAILURE_MESSAGES.get(code or "no_json", ""),
                        source_text=item.text,
                    )
                )
                continue

            parsed_count += 1
            parts[key] = parsed
            channel.emit(
                SectionParsed(
                    key=key,
                    data=parsed.model_dump(mode="json"),
                    source_text=item.text,
                    ms=elapsed,
                )
            )

        # Sections we have no schema for. They are imported under their own
        # heading rather than skipped: the geometry found the boundary and the
        # person wrote the label, so there is nothing left to guess. This is
        # what carries Publications, Volunteering, Leadership and every heading
        # in a language the alias table does not speak.
        edge = column_right(extraction.lines)
        custom: list[tuple[str, dict[str, Any]]] = []
        order: list[str] = []
        for item in segments:
            if item.key != "other":
                order.append(item.key)
                continue
            parsed_custom = parse_custom(item.lines, right=edge)
            heading = item.heading.strip()
            if parsed_custom is None or not heading:
                # No heading to file it under, or nothing under the heading.
                # Reported rather than invented -- the review screen keeps the
                # source text either way.
                channel.emit(
                    SectionSkipped(
                        key=item.key, heading=item.heading, source_text=item.text
                    )
                )
                continue
            custom.append((heading, parsed_custom))
            order.append(heading)
            parsed_count += 1
            channel.emit(
                SectionParsed(
                    key=heading, data=parsed_custom, source_text=item.text
                )
            )

        data = to_resume_data(
            contact=contact,
            parts=parts,
            skills=skills,
            order=order,
            custom=custom,
        )
        # Ids are minted here, once, and thrown away: the preview renders from
        # this document, but confirming posts ``resume_data`` back and mints
        # them again server-side. The client never supplies a node id.
        preview = from_resume_data(data)

        channel.emit(
            ImportReady(
                title=title_for(contact, request.filename),
                resume_data=data,
                doc=preview.model_dump(mode="json"),
                source_text=extraction.text,
                parsed=parsed_count,
                failed=failed_count,
            )
        )
