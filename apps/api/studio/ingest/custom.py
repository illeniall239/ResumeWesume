"""Sections the importer has no schema for.

A résumé is not made only of the six sections we happen to model. Publications,
Volunteering, Leadership, Patents, Speaking, Coursework, References -- and every
heading written in a language the alias table does not speak -- are ordinary
parts of ordinary résumés, and until now each one was recognised as a section,
reported as skipped, and left out of the document.

The fix is not a longer table. It is a destination: ``CustomSectionNode``
already exists, already renders, and already carries a label of its own, so an
unrecognised section becomes a section named by the résumé rather than by us.
That covers every heading nobody has thought of yet, which a table by
construction cannot.

No model call. There is nothing here to interpret -- the geometry found the
boundary and the person wrote the heading, so the only question left is whether
the lines inside are a list or a paragraph, and that is a question about shape.
Reading it from the same font metrics ``segment`` already uses keeps the answer
free, exact and reproducible.
"""

from __future__ import annotations

from typing import Any

from studio.ingest.pdf import Line

#: How close to the text column's right edge a line must end to count as having
#: been wrapped rather than deliberately ended. Generous, because justification
#: and a trailing space both pull the last glyph short of the true margin.
_WRAP_RATIO = 0.92


def column_right(lines: list[Line]) -> float:
    """Where the document's text column ends.

    Measured across the whole résumé rather than within one section, and that
    is the whole point of it. A section's own longest line is its own right
    edge by definition, so comparing lines against it says every section is
    fully justified: two publications, the longer 300pt and the shorter 280pt,
    read as one wrapped sentence. Against the column the person actually typed
    in, both are plainly short.
    """
    return max((line.x1 for line in lines if line.text.strip()), default=0.0)


def _is_prose(body: list[Line], right: float) -> bool:
    """Is this a wrapped paragraph, or a list of separate lines?

    A wrapped line ends where the column ends, because nothing chose to end it
    there. A list entry ends where its content does. Measuring that is what
    distinguishes two publications from one sentence that happened to run over
    two lines -- the same evidence, and the same reason, as reading a heading
    from its size rather than from its words.
    """
    if len(body) < 2:
        return True
    if any(line.bullet for line in body):
        return False
    if right <= 0:
        # Nothing to measure against: a text layer with no geometry at all.
        # Keeping the lines apart preserves the break; joining them invents a
        # paragraph the résumé may never have had.
        return False
    # Every line but the last: a paragraph's final line is short by definition,
    # and judging it would make every paragraph look like a list.
    return all(line.x1 >= right * _WRAP_RATIO for line in body[:-1])


def parse_custom(lines: list[Line], *, right: float = 0.0) -> dict[str, Any] | None:
    """Read one unrecognised section into a legacy ``customSections`` entry.

    ``right`` is the document's column edge, from :func:`column_right`.

    ``None`` when there is nothing in it, so an empty section does not become
    an empty heading on somebody's résumé.
    """
    body = [line for line in lines if line.text.strip()]
    if not body:
        return None

    if _is_prose(body, right):
        return {
            "sectionType": "text",
            "text": " ".join(line.text.strip() for line in body),
        }

    # A line that carries a bullet glyph belongs to the entry above it; a line
    # that does not starts a new one. The same shape as an experience entry,
    # because that is the shape a résumé writes these in.
    items: list[dict[str, Any]] = []
    for line in body:
        text = line.text.strip()
        if line.bullet and items:
            items[-1]["description"].append(text)
            items[-1]["descriptionStyles"].append("bullet")
            continue
        items.append(
            {
                "title": "" if line.bullet else text,
                "years": "",
                "description": [text] if line.bullet else [],
                "descriptionStyles": ["bullet"] if line.bullet else [],
            }
        )

    return {"sectionType": "itemList", "items": items}
