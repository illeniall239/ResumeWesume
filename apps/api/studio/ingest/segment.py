"""Splitting extracted lines into the sections a resume is made of.

Heading detection is scored rather than matched, because no single signal
survives contact with real resumes: plenty of headings are not bold, plenty are
not capitalised, and some are simply "Experience" in the body face with a rule
under it. Six weak signals summed beat any one of them.

The scoring is tuned for **precision, not recall**, and the asymmetry is the
whole design. A missed heading dumps its content into the previous section,
where the review screen shows it to the user next to the source. A false
heading is far worse than it first appears: promoting a job title to a section
leaves the section it was standing in with *no lines*, and an empty section is
dropped, so a single false positive can delete an entire work history and
report nothing. That is not hypothetical -- it is what the first version of
this file did to a real resume whose job titles were bold and short.

Two mechanisms come out of that, and the second is the one that matters.

**Content marks are penalised heavily.** A digit, a comma or an "@" outweighs
every positive signal at once, so ``Northwind Systems, Austin TX`` stays body
text however it is set.

**Unknown headings must look like known ones.** Scoring a line in isolation
cannot separate ``AI Engineer`` from ``VOLUNTEERING``: both are short, bold and
sit under a gap. What separates them is the rest of the document. A resume
dresses its section headings consistently, so the lines the alias table
recognises are used as labelled examples of this document's heading style, and
an unrecognised line has to match that style *and* clear a higher threshold
before it can start a section. A job title set two points smaller than
``EXPERIENCE``, or in title case where the real headings are uppercase, is
thereby excluded by the document's own typography rather than by a guess.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Iterable, Literal

from studio.ingest.pdf import Line, body_size

SectionKey = Literal[
    "contact",
    "summary",
    "experience",
    "education",
    "projects",
    "skills",
    "certifications",
    "awards",
    "languages",
    "other",
]

# Sections we know how to import. Anything else is kept and shown, never
# silently dropped, but does not become part of the document in this version.
IMPORTABLE: frozenset[str] = frozenset(
    {
        "contact",
        "summary",
        "experience",
        "education",
        "projects",
        "skills",
        "certifications",
        "awards",
        "languages",
    }
)

_ALIASES: dict[str, SectionKey] = {
    # experience
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "relevant experience": "experience",
    "industry experience": "experience",
    "employment": "experience",
    "employment history": "experience",
    "work history": "experience",
    "career history": "experience",
    "professional background": "experience",
    # education
    "education": "education",
    "academic background": "education",
    "academics": "education",
    "education and training": "education",
    "qualifications": "education",
    # skills
    "skills": "skills",
    "technical skills": "skills",
    "core skills": "skills",
    "key skills": "skills",
    "core competencies": "skills",
    "competencies": "skills",
    "technologies": "skills",
    "technical proficiencies": "skills",
    "proficiencies": "skills",
    "tools": "skills",
    "tools and technologies": "skills",
    "skills and tools": "skills",
    # projects
    "projects": "projects",
    "personal projects": "projects",
    "selected projects": "projects",
    "side projects": "projects",
    "notable projects": "projects",
    "portfolio": "projects",
    # summary
    "summary": "summary",
    "professional summary": "summary",
    "executive summary": "summary",
    "profile": "summary",
    "professional profile": "summary",
    "objective": "summary",
    "career objective": "summary",
    "about": "summary",
    "about me": "summary",
    # the rest
    "certifications": "certifications",
    "certification": "certifications",
    "certifications and training": "certifications",
    "training": "certifications",
    "licenses and certifications": "certifications",
    "awards": "awards",
    "honors": "awards",
    "honours": "awards",
    "awards and honors": "awards",
    "achievements": "awards",
    "languages": "languages",
    "spoken languages": "languages",
    # contact, for the resumes that do label it
    "contact": "contact",
    "contact information": "contact",
    "details": "contact",
}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
# A digit, a comma or an "@" is the mark of content, not of a section label.
_CONTENT_MARKS = re.compile(r"[0-9,@]")

HEADING_THRESHOLD = 3.0

# A line the alias table does not recognise has to clear a much higher bar, and
# look like the headings we did recognise. Without both, an ordinary resume
# shreds itself: a job title is bold, short, and sits under a gap, which is
# three weak signals and exactly the base threshold. "AI Engineer" then becomes
# a section, the experience section it was sitting in is left with no lines at
# all, and the whole work history vanishes with nothing reported.
UNKNOWN_HEADING_THRESHOLD = 4.0

# When a document names none of its sections in a way we recognise, there is no
# style to compare against and the line itself is the only evidence. Uppercase
# is then required outright rather than merely scored: it is the one signal a
# job title reliably lacks, since resumes set titles in title case and section
# headings in capitals. A resume that shouts its job titles too is genuinely
# ambiguous, and merging two sections there is the recoverable way to be wrong.
UNSTYLED_HEADING_NEEDS_CAPS = True

_MAX_HEADING_WORDS = 5
_MAX_HEADING_CHARS = 44
_SIZE_RATIO = 1.08
_GAP_RATIO = 1.5
# Above this share of bold lines, boldness says nothing about a line.
_MOSTLY_BOLD = 0.6
# How far under the established heading size a line may still be one. Generous
# enough for a hinted font's rounding, tight enough to separate an 11pt job
# title from a 13pt section heading.
_SIZE_SLACK = 0.4


@dataclass(frozen=True)
class Segment:
    key: SectionKey
    heading: str
    lines: list[Line]
    order: int

    @property
    def text(self) -> str:
        """The section's lines, joined.

        This drops everything a ``Line`` knows except the string -- its size,
        whether it was bold, whether it carried a bullet glyph -- and that looks
        like a loss worth recovering. It was measured rather than assumed.

        Two alternatives were built and compared against this one: a `markdown`
        property rendering those metrics as ``**bold**`` and ``- bullets``, and
        Firecrawl's anydoc converting the whole PDF to GitHub-flavoured
        markdown. Twenty-four runs -- two fixtures, three formats, Claude and a
        12B local model -- scored 8/8 every time. No format won, so the plainest
        stays; it is also 3% cheaper in tokens than the marked-up version.

        The reason it does not matter is upstream. By the time text reaches the
        model it is *one section of one known kind*, with its own schema and its
        own prompt. The geometry already did its work in ``segment``, deciding
        where the boundaries are; restating it inside a section tells the model
        what the section itself already said.
        """
        return "\n".join(line.text for line in self.lines)

    @property
    def importable(self) -> bool:
        return self.key in IMPORTABLE


def normalise_heading(text: str) -> str:
    """Fold a heading to its comparable form: lowercase, letters and digits.

    Punctuation becomes a space rather than nothing, so "Skills/Tools" reads as
    two words; the runs that leaves behind are then collapsed, or every alias
    lookup for a heading with punctuation in it would miss by a space.
    """
    return " ".join(_PUNCT.sub(" ", text.lower()).split())


def classify_heading(text: str) -> SectionKey | None:
    """Map a line to the section it names, if it names one."""
    folded = normalise_heading(text)
    if not folded:
        return None
    if folded in _ALIASES:
        return _ALIASES[folded]
    # "Experience & Achievements", "Skills / Tools": take the first recognised
    # word group rather than giving up on a compound heading.
    words = folded.split()
    if 1 < len(words) <= 4:
        for size in (3, 2, 1):
            for start in range(len(words) - size + 1):
                candidate = " ".join(words[start : start + size])
                if candidate in _ALIASES:
                    return _ALIASES[candidate]
    return None


def heading_score(
    line: Line,
    *,
    body: float,
    gap_above: float,
    median_gap: float,
    bold_is_meaningful: bool,
) -> float:
    """How much this line looks like a section heading.

    No single term is decisive except the alias table, which is worth the whole
    threshold on its own: if a line says "Professional Experience" then it is a
    heading whatever it looks like.
    """
    text = line.text.strip()
    if not text:
        return 0.0

    score = 0.0
    if classify_heading(text) is not None:
        score += 3.0
    if line.size > body * _SIZE_RATIO:
        score += 1.0
    if line.bold and bold_is_meaningful:
        score += 1.0
    if len(text.split()) <= _MAX_HEADING_WORDS and len(text) <= _MAX_HEADING_CHARS:
        score += 1.0
    if text.isupper():
        score += 1.0
    if median_gap > 0 and gap_above > median_gap * _GAP_RATIO:
        score += 1.0

    # Content, not a label. Large enough to overpower every positive at once,
    # because a false heading is the expensive mistake here.
    if _CONTENT_MARKS.search(text) or text.endswith("."):
        score -= 3.0
    return score


@dataclass(frozen=True)
class HeadingStyle:
    """How this particular document dresses its section headings."""

    size: float
    bold: bool
    upper: bool


def heading_style(lines: list[Line]) -> HeadingStyle | None:
    """Learn the document's heading style from the headings we can name.

    A resume styles its section headings consistently -- one size, one weight,
    one capitalisation -- and that consistency is far more reliable evidence
    than any property of a line considered alone. So the lines the alias table
    recognises are used as labelled examples, and everything else has to look
    like them.

    This is what separates a real unlabelled section from a job title. Both are
    short, bold and preceded by a gap. Only one of them is set in the same
    13pt uppercase as the ``EXPERIENCE`` heading further up the page.

    Returns ``None`` when nothing was recognised, which is treated as "trust no
    unknown heading at all" -- the conservative direction, since an unfound
    heading merges two sections the user can see, while an invented one silently
    empties the section it was standing in.
    """
    known = [
        line
        for line in lines
        if line.text
        and classify_heading(line.text) is not None
        and len(line.text) <= _MAX_HEADING_CHARS
    ]
    if not known:
        return None

    return HeadingStyle(
        size=statistics.median(line.size for line in known),
        # "Most of them" rather than "all": one heading in a different weight
        # should not disarm the test for the rest.
        bold=sum(1 for line in known if line.bold) > len(known) / 2,
        upper=sum(1 for line in known if line.text.isupper()) > len(known) / 2,
    )


def matches_style(line: Line, style: HeadingStyle) -> bool:
    """Whether ``line`` is dressed at least as boldly as a known heading.

    Deliberately one-directional. A candidate larger or louder than the
    established headings is still plausibly one; a candidate quieter than them
    is body text, whatever else it has going for it.
    """
    if line.size < style.size - _SIZE_SLACK:
        return False
    if style.bold and not line.bold:
        return False
    if style.upper and not line.text.isupper():
        return False
    return True


def _gaps(lines: list[Line]) -> list[float]:
    gaps: list[float] = []
    for previous, current in zip(lines, lines[1:]):
        if previous.page != current.page:
            continue
        gap = previous.top - current.top
        if gap > 0:
            gaps.append(gap)
    return gaps


def segment(lines: Iterable[Line]) -> list[Segment]:
    """Split ordered lines into sections.

    Everything before the first heading is the contact block, always emitted
    even when a resume has no headings at all -- resumes essentially never
    label their own header, so this is the one section that has to be inferred
    from position rather than found.

    The very first line is never treated as a heading for the same reason. A
    name in 20pt bold is a heading by every signal we have except the one that
    matters, and demoting it costs nothing: no resume opens with a section.
    """
    body = [line for line in lines if line.text]
    if not body:
        return []

    reference = body_size(body)
    gaps = _gaps(body)
    median_gap = statistics.median(gaps) if gaps else 0.0
    bold_share = sum(1 for line in body if line.bold) / len(body)
    bold_is_meaningful = bold_share <= _MOSTLY_BOLD
    style = heading_style(body)

    segments: list[Segment] = []
    current_key: SectionKey = "contact"
    current_heading = ""
    bucket: list[Line] = []
    order = 0

    def flush() -> None:
        nonlocal bucket
        if bucket or not segments:
            segments.append(
                Segment(
                    key=current_key,
                    heading=current_heading,
                    lines=bucket,
                    order=order,
                )
            )
        bucket = []

    for index, line in enumerate(body):
        gap_above = 0.0
        if index and body[index - 1].page == line.page:
            gap_above = max(0.0, body[index - 1].top - line.top)

        score = heading_score(
            line,
            body=reference,
            gap_above=gap_above,
            median_gap=median_gap,
            bold_is_meaningful=bold_is_meaningful,
        )

        if index == 0:
            # No resume opens with a section, and a 20pt name outscores every
            # real heading on the page.
            is_heading = False
        elif classify_heading(line.text) is not None:
            is_heading = score >= HEADING_THRESHOLD
        elif style is not None:
            # Unrecognised, in a document that has shown us what its headings
            # look like. Clear a higher bar, and be dressed like them.
            is_heading = (
                matches_style(line, style) and score >= UNKNOWN_HEADING_THRESHOLD
            )
        else:
            # Unrecognised, with nothing to compare against.
            is_heading = (
                line.text.isupper() or not UNSTYLED_HEADING_NEEDS_CAPS
            ) and score >= UNKNOWN_HEADING_THRESHOLD

        if not is_heading:
            bucket.append(line)
            continue

        flush()
        order += 1
        current_key = classify_heading(line.text) or "other"
        current_heading = line.text

    flush()
    return _coalesce(segments)


def _coalesce(segments: list[Segment]) -> list[Segment]:
    """Fold repeats of the same section together.

    Two things produce them, both common: a resume that labels its header
    ``CONTACT`` (so the inferred block and the labelled one are both contact),
    and a resume that continues ``EXPERIENCE`` after a page break with the
    heading repeated. Unknown sections are left alone -- two different headings
    we could not classify are not the same section just because we failed at
    both.
    """
    merged: dict[SectionKey, Segment] = {}
    result: list[Segment] = []

    for item in segments:
        if item.key == "other":
            if item.lines:
                result.append(item)
            continue
        existing = merged.get(item.key)
        if existing is None:
            merged[item.key] = item
            result.append(item)
            continue
        combined = Segment(
            key=existing.key,
            heading=existing.heading or item.heading,
            lines=existing.lines + item.lines,
            order=existing.order,
        )
        merged[item.key] = combined
        result[result.index(existing)] = combined

    return [item for item in result if item.lines or item.key == "contact"]
