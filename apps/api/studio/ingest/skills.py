"""Skills, languages, certifications and awards: the list sections.

No model call, for the same reason as the contact block. These are lists
someone has already delimited, and reading the delimiter is exact where a model
is merely likely. It also removes the failure where a model, asked to list
skills, helpfully adds two the person never claimed.

But they are **two different shapes**, and treating them as one loses data.

*Skills and languages* are short tokens, many to a line, separated by commas:
``Python, Go, PostgreSQL``. The comma is a separator and the entries are a few
characters each.

*Certifications and awards* are long, one to a line, and routinely contain the
very characters that separate a skills list::

    IBM - Python for Data Science, AI & Development - coursera.org/verify/4FY...
    Storytelling and Influencing: Communicate with Impact - coursera.org/ver...

Parsing those as a skills list is not a near miss. The first splits at its comma
into two half-certifications; the second loses "Storytelling and Influencing"
to a label rule meant for "Languages:"; and any line long enough to carry a
verification URL is discarded outright by a length cap sized for the word
"Python". A real four-line certifications section came out as three entries,
two of them wrong -- which is why the two shapes are now read separately.
"""

from __future__ import annotations

import re

from studio.ingest.pdf import Line

# Split on the delimiters resumes actually use. Not on "/" or "-": those live
# inside real skill names (CI/CD, ETL/ELT, test-driven development).
_SPLIT = re.compile(r"[,;|•·・]| {3,}|\t")

# "Languages:", "Frontend:", "Databases:" -- a category name repeated inside its
# own section. Bounded tightly, and only ever applied when what follows is
# itself a delimited list: without that guard it eats the first half of
# "Storytelling and Influencing: Communicate with Impact", which is a title, not
# a label.
_LABEL = re.compile(r"^([A-Za-z][A-Za-z /&+-]{2,28}):\s*(?=\S)")

# Long enough to be a sentence rather than a skill. A "skills" section that is
# really a paragraph should not become forty nonsense skills.
_MAX_SKILL_CHARS = 60
_MAX_SKILL_WORDS = 7

# A credential carries a course name, an issuer and often a verification URL, so
# the ceiling is only here to reject a paragraph that was never a list.
_MAX_CREDENTIAL_CHARS = 200

_URL = re.compile(r"https?://|\b[\w-]+\.(?:com|org|net|io|dev|ai|edu)/", re.I)


def _clean(value: str) -> str:
    return value.strip(" \t.,;:•·-–—")


def _strip_label(text: str) -> str:
    """Remove a leading category label, but only when it is really one.

    The test is what *follows* the colon: a category label introduces a list,
    so "Frontend: React, Vue, Svelte" is a label and "Storytelling and
    Influencing: Communicate with Impact" is a sentence that happens to contain
    a colon. Requiring a delimiter in the remainder separates them without
    needing to know every category name a resume might invent.
    """
    match = _LABEL.match(text)
    if not match:
        return text
    remainder = text[match.end() :]
    return remainder if _SPLIT.search(remainder) else text


def parse_skills(lines: list[Line]) -> list[str]:
    """Read a skills or languages section: short tokens, many to a line.

    Order is preserved and duplicates are dropped case-insensitively, so a
    resume repeating "Python" under both Skills and Tools imports it once,
    keeping the first spelling the person actually used.
    """
    found: list[str] = []
    seen: set[str] = set()

    for line in lines:
        text = _strip_label(line.text.strip())
        if not text:
            continue

        # A line with no delimiter is one skill per line, which is the other
        # common layout -- and the only one a two-column sidebar produces.
        for part in (_clean(part) for part in _SPLIT.split(text)):
            if not part:
                continue
            if len(part) > _MAX_SKILL_CHARS or len(part.split()) > _MAX_SKILL_WORDS:
                continue
            key = part.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(part)

    return found


def parse_credentials(lines: list[Line]) -> list[str]:
    """Read a certifications or awards section: one long entry per line.

    The line break is the separator, not the comma, because a credential name
    contains commas of its own. A section written compactly on a single line is
    the exception, and only there is a comma read as a separator -- one line
    holding several credentials has no other structure to go on.

    No word limit and a high character ceiling: an entry here is a course name
    plus an issuer plus a verification URL, and every cap sized for a skill
    token silently deletes the longest, most specific credentials a person has.
    """
    body = [line for line in lines if line.text.strip()]
    if not body:
        return []

    if len(body) == 1:
        # A compact one-line list. A URL means it is a single credential that
        # happens to contain a comma, not a list of several.
        text = _strip_label(body[0].text.strip())
        candidates = (
            [text] if _URL.search(text) else [part for part in _SPLIT.split(text)]
        )
    else:
        candidates = [line.text for line in body]

    found: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        entry = _clean(_strip_label(candidate.strip()))
        if not entry or len(entry) > _MAX_CREDENTIAL_CHARS:
            continue
        key = entry.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(entry)
    return found
