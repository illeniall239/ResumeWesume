"""PDF text extraction, laid out as lines.

Everything above the ``pdfminer`` import boundary is pure geometry over
``Line`` values: no I/O, no library, no document. That split is not tidiness.
The column and heading heuristics are where this feature is most likely to be
wrong, and pure functions over hand-built lines are the only way to pin their
behaviour without committing a PDF for every case worth testing.

We read pages with ``extract_pages`` rather than calling ``extract_text``,
which would be one line. The reason is font metrics: character size and a
``Bold`` in the font name are by a wide margin the strongest heading signal a
resume offers, and ``extract_text`` throws both away. Column ordering is a
second benefit, not the motivation.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, replace
from typing import Iterable

# Bullet glyphs, plus the Private Use Area. Word exports a Symbol/Wingdings
# bullet as U+F0B7 and friends, which is not a "bullet character" in any
# Unicode sense but is what lands in a very large share of real resumes.
_BULLET_CHARS = "•▪●◦‣▸∙·⁃■○-–—*»›"
_PUA_START, _PUA_END = 0xF000, 0xF0FF

_CID = re.compile(r"\(cid:\d+\)")
_WS = re.compile(r"[ \t ]+")

# A line ending in one of these is a finished thought; the next line is not a
# continuation of it.
_TERMINAL = ".!?:;"


@dataclass(frozen=True)
class Line:
    """One visual line of text, with the geometry needed to read the page.

    ``top`` is normalised so that larger means higher on the page, matching
    pdfminer's upward y-axis, so descending ``top`` is reading order.
    """

    text: str
    page: int = 1
    x0: float = 0.0
    x1: float = 0.0
    top: float = 0.0
    size: float = 10.0
    bold: bool = False
    bullet: bool = False

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)


class ExtractionError(Exception):
    """The file cannot be read, in a way the user needs told about."""

    def __init__(self, message: str, *, code: str = "unreadable") -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# Pure text normalisation
# --------------------------------------------------------------------------


def normalise_text(raw: str) -> str:
    """Fold the encoding artefacts that break plain word matching.

    Each of these is a real thing that comes out of a real resume, and each one
    silently breaks a later comparison rather than raising: an NFKC ligature
    means "workflow" does not match "workflow", a soft hyphen means a word
    contains an invisible character, and a non-breaking space means a split on
    " " returns the wrong number of words.
    """
    text = raw.replace("­", "").replace(" ", " ")
    text = unicodedata.normalize("NFKC", text)
    return _WS.sub(" ", text).strip()


def strip_bullet(text: str) -> tuple[str, bool]:
    """Remove a leading bullet glyph, reporting whether there was one.

    The glyph is a layout fact, not content: it belongs in ``Line.bullet`` so
    the segmenter can use it, and it must not survive into the text we hand a
    model, which would otherwise dutifully copy it into the bullet value.
    """
    stripped = text.lstrip()
    if not stripped:
        return "", False

    first = stripped[0]
    is_bullet = first in _BULLET_CHARS or _PUA_START <= ord(first) <= _PUA_END
    if not is_bullet:
        return stripped, False

    rest = stripped[1:].lstrip()
    # A lone dash with nothing after it is a separator, not a bullet, and a
    # hyphen inside a word was never a bullet to begin with.
    if not rest:
        return stripped, False
    return rest, True


# --------------------------------------------------------------------------
# Pure geometry
# --------------------------------------------------------------------------

# A candidate gutter is scanned across the middle of the page only; a split
# further out than this is a margin artefact, not a column boundary.
_SCAN_LO, _SCAN_HI = 0.30, 0.70
_SCAN_STEP = 0.01

# All three must hold before we believe a page is two-column. See the module
# note in ``detect_gutter`` for why condition three is the load-bearing one.
_MIN_BAND = 0.04  # of page width
_MIN_CLEAR = 0.60  # of the page's occupied vertical extent
_MIN_RIGHT_LINES = 8
_MIN_RIGHT_CHARS = 0.25
_MIN_PAGE_LINES = 12


def _largest_clear_span(
    blocked: list[float], lo: float, hi: float
) -> tuple[float, float, float]:
    """Widest vertical run between blocking lines, as ``(span, lo, hi)``."""
    marks = [hi] + sorted((t for t in blocked if lo <= t <= hi), reverse=True) + [lo]
    best = (0.0, lo, lo)
    for upper, lower in zip(marks, marks[1:]):
        span = upper - lower
        if span > best[0]:
            best = (span, lower, upper)
    return best


def detect_gutter(lines: Iterable[Line], *, x0: float, width: float) -> float | None:
    """Find the x of a real two-column split, or ``None`` for one column.

    This is the most dangerous heuristic in the importer, because the naive
    version misfires on the single most common resume layout there is::

        Northwind Systems                          Mar 2021 - Present

    A right-aligned date column presents exactly like a second column, and
    believing it detaches every date from the job it belongs to -- silently,
    and in a way the user is unlikely to notice until they have sent the
    resume. So we ask for three independent things at once, and the third is
    what actually separates the two cases: dates are short and few, so the
    right-hand side of a false gutter can never carry a quarter of the page's
    characters across eight or more lines.

    Being wrong in the other direction is survivable. A missed two-column page
    reads in the wrong order, the segmenter still finds most headings, and the
    user sees it on the review screen.
    """
    page = [line for line in lines if line.text]
    if len(page) < _MIN_PAGE_LINES or width <= 0:
        return None

    tops = [line.top for line in page]
    extent_lo, extent_hi = min(tops), max(tops)
    extent = extent_hi - extent_lo
    if extent <= 0:
        return None

    eps = width * 0.005
    qualifying: list[float] = []
    steps = int(round((_SCAN_HI - _SCAN_LO) / _SCAN_STEP))

    for index in range(steps + 1):
        split = x0 + width * (_SCAN_LO + index * _SCAN_STEP)

        straddlers = [
            line for line in page if line.x0 < split - eps and line.x1 > split + eps
        ]
        span, lo, hi = _largest_clear_span(
            [line.top for line in straddlers], extent_lo, extent_hi
        )
        if span < _MIN_CLEAR * extent:
            continue

        inside = [line for line in page if lo <= line.top <= hi]
        right = [line for line in inside if line.x0 >= split - eps]
        if len(right) < _MIN_RIGHT_LINES:
            continue

        chars_inside = sum(len(line.text) for line in inside)
        chars_right = sum(len(line.text) for line in right)
        if chars_inside == 0 or chars_right / chars_inside < _MIN_RIGHT_CHARS:
            continue

        qualifying.append(split)

    if not qualifying:
        return None

    # A single qualifying x is a coincidence; a real gutter is a band of
    # whitespace, so require contiguous qualifying splits spanning 4% of the
    # page and take the middle of the widest such run.
    best: tuple[float, float] | None = None
    run_start = previous = qualifying[0]
    tolerance = width * _SCAN_STEP * 1.5

    for split in qualifying[1:] + [float("inf")]:
        if split - previous > tolerance:
            if previous - run_start >= _MIN_BAND * width:
                if best is None or (previous - run_start) > (best[1] - best[0]):
                    best = (run_start, previous)
            run_start = split
        previous = split

    return None if best is None else (best[0] + best[1]) / 2


def order_lines(lines: Iterable[Line], *, x0: float, width: float) -> list[Line]:
    """Sort one page's lines into reading order."""
    page = list(lines)
    gutter = detect_gutter(page, x0=x0, width=width)
    if gutter is None:
        return sorted(page, key=lambda line: (-line.top, line.x0))

    # A line straddling the gutter is a full-width heading or rule; it reads
    # before both columns it sits above, so it keeps its place by height while
    # the columns below it are read left then right.
    def key(line: Line) -> tuple[int, float, float]:
        if line.x1 <= gutter:
            return (0, -line.top, line.x0)
        if line.x0 >= gutter:
            return (1, -line.top, line.x0)
        return (0, -line.top, line.x0)

    return sorted(page, key=key)


# A wrapped line under a hanging indent sits slightly right of the line it
# wraps from. Slightly: a jump of hundreds of points is a right-aligned date
# column, not a continuation.
_HANGING_INDENT = 20.0


def merge_wrapped(lines: Iterable[Line]) -> list[Line]:
    """Rejoin lines that a PDF broke purely for width.

    Without this every wrapped bullet arrives as two lines, and a model asked
    to list the bullets faithfully returns one accomplishment as two -- the
    second of them a sentence fragment.

    Two signals, because neither is enough on its own. A continuation starting
    lowercase is unambiguous, but it misses the ordinary case of a wrap landing
    on an acronym or a proper noun ("...brought the catalogue to" / "WCAG 2.1
    AA"). The second signal is the hanging indent such a wrap sits on, which
    measurement says is a few points and which a right-aligned date column --
    the thing most likely to be mistaken for a continuation -- misses by two
    orders of magnitude.

    What is deliberately *not* used is "the previous line reached the right
    margin". A right-aligned date is flush to the margin by definition, so that
    test swallows the employer on the line beneath it: ``Mar 2021 - Present``
    followed by ``Northwind Systems`` reads as one wrapped line under it.

    A continuation flush with its predecessor (no hanging indent) is left
    alone unless it starts lowercase. That misses some real wraps, and it is
    the right way round to be wrong: an unmerged line is visible on the review
    screen, whereas a wrong merge silently fuses an employer into a job title.
    """
    merged: list[Line] = []
    for line in lines:
        if not merged or line.bullet or not line.text:
            merged.append(line)
            continue

        previous = merged[-1]
        head = line.text[0]
        indent = line.x0 - previous.x0
        unfinished = bool(previous.text) and previous.text[-1] not in _TERMINAL
        hanging = (
            0 < indent <= _HANGING_INDENT
            and not line.bold
            and line.size <= previous.size * 1.05
        )
        continues = unfinished and (head.islower() or head in ",;)" or hanging)
        if not continues:
            merged.append(line)
            continue

        merged[-1] = replace(
            previous,
            text=f"{previous.text} {line.text}",
            x1=max(previous.x1, line.x1),
        )
    return merged


def body_size(lines: Iterable[Line]) -> float:
    """The page's ordinary text size, against which a heading stands out."""
    sizes = [line.size for line in lines if line.text]
    return statistics.median(sizes) if sizes else 10.0


# --------------------------------------------------------------------------
# pdfminer boundary
# --------------------------------------------------------------------------

# A resume longer than this is either not a resume or not one we can help
# with, and every extra page is model context we would have to pay for.
MAX_PAGES = 10
MAX_CHARS = 60_000

# Below this there is no text layer worth parsing: the file is a scan.
_MIN_USEFUL_CHARS = 200
# Above this share of undecodable glyphs the font has no ToUnicode map and
# every word we extract is noise.
_MAX_CID_RATIO = 0.10

_BOLD_MARKERS = ("bold", "black", "heavy", "semibold", "demibold")


@dataclass(frozen=True)
class Page:
    """One page's lines, with the geometry needed to order them."""

    number: int
    x0: float
    width: float
    lines: list[Line]


@dataclass(frozen=True)
class Extraction:
    text: str
    lines: list[Line]
    pages: int
    columns: int
    warnings: list[str]


def _line_from(text_line: object, page_number: int) -> Line | None:
    """Build a ``Line`` from an ``LTTextLine``, keeping its font metrics."""
    from pdfminer.layout import LTChar

    raw = normalise_text(text_line.get_text())  # type: ignore[attr-defined]
    if not raw:
        return None

    text, bullet = strip_bullet(raw)
    if not text:
        return None

    chars = [item for item in text_line if isinstance(item, LTChar)]  # type: ignore[attr-defined]
    if chars:
        size = statistics.median(char.size for char in chars)
        bold_count = sum(
            1
            for char in chars
            if any(marker in (char.fontname or "").lower() for marker in _BOLD_MARKERS)
        )
        bold = bold_count > len(chars) / 2
    else:
        size, bold = 10.0, False

    return Line(
        text=text,
        page=page_number,
        x0=float(text_line.x0),  # type: ignore[attr-defined]
        x1=float(text_line.x1),  # type: ignore[attr-defined]
        top=float(text_line.y1),  # type: ignore[attr-defined]
        size=float(size),
        bold=bold,
        bullet=bullet,
    )


def read_pages(data: bytes, *, max_pages: int = MAX_PAGES) -> list[Page]:
    """Read a PDF into per-page lines, or raise ``ExtractionError``.

    Every failure here is one the user has to be told about in words they can
    act on. A scanned resume, an encrypted file and a font with no ToUnicode
    map all produce "nothing useful" in three different ways, and reporting
    them as one generic parse failure sends the user off to debug the wrong
    thing.
    """
    import io

    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer, LTTextLine
    from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
    from pdfminer.pdfparser import PDFSyntaxError

    try:
        pages: list[Page] = []
        for index, layout in enumerate(
            extract_pages(io.BytesIO(data), maxpages=max_pages)
        ):
            lines: list[Line] = []
            for element in layout:
                if not isinstance(element, LTTextContainer):
                    continue
                for text_line in element:
                    if not isinstance(text_line, LTTextLine):
                        continue
                    built = _line_from(text_line, index + 1)
                    if built is not None:
                        lines.append(built)
            pages.append(
                Page(
                    number=index + 1,
                    x0=float(layout.x0),
                    width=float(layout.width),
                    lines=lines,
                )
            )
    except (PDFPasswordIncorrect, PDFEncryptionError) as error:
        raise ExtractionError(
            "This PDF is password-protected. Remove the password and try again.",
            code="encrypted",
        ) from error
    except PDFSyntaxError as error:
        raise ExtractionError(
            "This file is not a readable PDF.", code="malformed"
        ) from error

    if not pages:
        raise ExtractionError("This PDF has no pages.", code="empty")
    return pages


def extract(
    data: bytes, *, max_pages: int = MAX_PAGES, max_chars: int = MAX_CHARS
) -> Extraction:
    """Read a PDF into ordered, de-wrapped lines.

    Blocking and CPU-bound; callers on the event loop must go through
    ``asyncio.to_thread``.
    """
    pages = read_pages(data, max_pages=max_pages)

    lines: list[Line] = []
    columns = 1
    for page in pages:
        gutter = detect_gutter(page.lines, x0=page.x0, width=page.width)
        ordered = order_lines(page.lines, x0=page.x0, width=page.width)

        if gutter is not None:
            columns = 2
        lines.extend(merge_wrapped(ordered))

    text = "\n".join(line.text for line in lines)

    cid_chars = sum(len(match) for match in _CID.findall(text))
    if text and cid_chars / len(text) > _MAX_CID_RATIO:
        raise ExtractionError(
            "The fonts in this PDF cannot be decoded to text. Try exporting it "
            "again from the original document, or saving it as a new PDF.",
            code="undecodable",
        )

    if len(text.strip()) < _MIN_USEFUL_CHARS:
        raise ExtractionError(
            "This PDF has no text layer -- it looks like a scan or an image. "
            "Scanned resumes are not supported.",
            code="no_text_layer",
        )

    warnings: list[str] = []
    if len(text) > max_chars:
        # Truncate on a line boundary, so the last line a model sees is not
        # half a sentence it will dutifully try to finish.
        keep: list[Line] = []
        budget = max_chars
        for line in lines:
            if budget - len(line.text) < 0:
                break
            keep.append(line)
            budget -= len(line.text) + 1
        lines = keep
        text = "\n".join(line.text for line in lines)
        warnings.append("This resume was long, so only the first part was read.")

    if len(pages) >= max_pages:
        warnings.append(f"Only the first {max_pages} pages were read.")

    return Extraction(
        text=text, lines=lines, pages=len(pages), columns=columns, warnings=warnings
    )
