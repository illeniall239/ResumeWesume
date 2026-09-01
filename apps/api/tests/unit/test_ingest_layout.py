"""Page geometry: columns, reading order, wrapped lines, bullet glyphs.

These run on hand-built ``Line`` values rather than PDFs, which is the point:
the cases worth testing here are layout shapes, and committing a PDF for each
one would make them unreadable and slow without making them more real.

The single most important test in this file is
``test_right_aligned_dates_are_not_a_second_column``. Getting that wrong
detaches every date from its job, silently, in a document someone is about to
send to an employer.
"""

from __future__ import annotations

from studio.ingest.pdf import (
    Line,
    body_size,
    detect_gutter,
    merge_wrapped,
    normalise_text,
    order_lines,
    strip_bullet,
)

PAGE_X0 = 0.0
PAGE_WIDTH = 612.0


def line(text: str, *, x0: float, x1: float, top: float, **kwargs: object) -> Line:
    return Line(text=text, x0=x0, x1=x1, top=top, **kwargs)  # type: ignore[arg-type]


def column(
    count: int, *, x0: float, x1: float, top: float, chars: int = 60, step: float = 14.0
) -> list[Line]:
    """A block of body text, ``chars`` wide, running down the page."""
    return [
        line("x" * chars, x0=x0, x1=x1, top=top - index * step) for index in range(count)
    ]


class TestGutterDetection:
    def test_right_aligned_dates_are_not_a_second_column(self) -> None:
        """The layout this heuristic exists to survive.

        Short left-hand content leaves the middle of the page clear, so the
        whitespace test alone would happily split here and carry every date
        away from its employer. Only the "the right side must hold real
        content" condition separates this from a genuine two-column page.
        """
        body = column(20, x0=50, x1=290, top=700)
        dates = [
            line("Mar 2021 - Present", x0=450, x1=560, top=700 - index * 70)
            for index in range(5)
        ]
        assert detect_gutter(body + dates, x0=PAGE_X0, width=PAGE_WIDTH) is None

    def test_many_short_right_aligned_dates_are_still_not_a_column(self) -> None:
        """Eight of them clears the line count, so the character share decides."""
        body = column(20, x0=50, x1=290, top=700, chars=60)
        dates = [
            line("2021 - 2023", x0=470, x1=560, top=700 - index * 30)
            for index in range(9)
        ]
        assert detect_gutter(body + dates, x0=PAGE_X0, width=PAGE_WIDTH) is None

    def test_genuine_two_column_page_splits(self) -> None:
        left = column(15, x0=50, x1=200, top=700, chars=25)
        right = column(15, x0=330, x1=560, top=700, chars=35)
        gutter = detect_gutter(left + right, x0=PAGE_X0, width=PAGE_WIDTH)
        assert gutter is not None
        assert 200 < gutter < 330

    def test_full_width_heading_does_not_hide_the_gutter(self) -> None:
        """A name banner across the top straddles every candidate split.

        It blocks the band only at its own height, so the columns beneath it
        must still be found; requiring a completely unbroken band would fail on
        essentially every real two-column resume.
        """
        heading = line("ALEX MORGAN", x0=50, x1=560, top=750, size=18.0, bold=True)
        left = column(15, x0=50, x1=200, top=700, chars=25)
        right = column(15, x0=330, x1=560, top=700, chars=35)
        gutter = detect_gutter([heading, *left, *right], x0=PAGE_X0, width=PAGE_WIDTH)
        assert gutter is not None

    def test_narrow_skills_sidebar_is_not_a_column(self) -> None:
        """Ten one-word lines clear the count but carry almost no text."""
        body = column(20, x0=50, x1=400, top=700, chars=80)
        sidebar = [
            line("Python", x0=470, x1=530, top=700 - index * 20) for index in range(10)
        ]
        assert detect_gutter(body + sidebar, x0=PAGE_X0, width=PAGE_WIDTH) is None

    def test_sparse_page_is_never_split(self) -> None:
        """Too little on the page to tell a layout from an accident."""
        left = column(4, x0=50, x1=200, top=700)
        right = column(4, x0=330, x1=560, top=700)
        assert detect_gutter(left + right, x0=PAGE_X0, width=PAGE_WIDTH) is None

    def test_single_column_body_text_is_not_split(self) -> None:
        body = column(25, x0=50, x1=560, top=700, chars=90)
        assert detect_gutter(body, x0=PAGE_X0, width=PAGE_WIDTH) is None


class TestReadingOrder:
    def test_single_column_reads_top_to_bottom(self) -> None:
        lines = [
            line("third", x0=50, x1=300, top=100),
            line("first", x0=50, x1=300, top=300),
            line("second", x0=50, x1=300, top=200),
        ]
        ordered = order_lines(lines, x0=PAGE_X0, width=PAGE_WIDTH)
        assert [item.text for item in ordered] == ["first", "second", "third"]

    def test_two_columns_read_left_side_first(self) -> None:
        """Not interleaved by height, which is what a plain sort would do."""
        left = column(15, x0=50, x1=200, top=700, chars=25)
        right = column(15, x0=330, x1=560, top=700, chars=35)
        marked_left = [
            Line(text=f"L{index}", x0=item.x0, x1=item.x1, top=item.top)
            for index, item in enumerate(left)
        ]
        marked_right = [
            Line(text=f"R{index}", x0=item.x0, x1=item.x1, top=item.top)
            for index, item in enumerate(right)
        ]
        # Interleaved on input, so a stable sort cannot accidentally pass.
        scrambled: list[Line] = []
        for pair in zip(marked_left, marked_right):
            scrambled.extend(pair)

        ordered = [item.text for item in order_lines(scrambled, x0=PAGE_X0, width=PAGE_WIDTH)]
        assert ordered[:15] == [f"L{index}" for index in range(15)]
        assert ordered[15:] == [f"R{index}" for index in range(15)]

    def test_full_width_heading_reads_before_both_columns(self) -> None:
        heading = line("ALEX MORGAN", x0=50, x1=560, top=750, size=18.0, bold=True)
        left = column(15, x0=50, x1=200, top=700, chars=25)
        right = column(15, x0=330, x1=560, top=700, chars=35)
        ordered = order_lines(
            [*right, *left, heading], x0=PAGE_X0, width=PAGE_WIDTH
        )
        assert ordered[0].text == "ALEX MORGAN"


class TestWrappedLines:
    def test_continuation_is_joined_to_its_bullet(self) -> None:
        lines = [
            line("Rebuilt the payments ledger and", x0=60, x1=400, top=300, bullet=True),
            line("cut settlement time by half", x0=60, x1=340, top=286),
        ]
        merged = merge_wrapped(lines)
        assert len(merged) == 1
        assert merged[0].text == (
            "Rebuilt the payments ledger and cut settlement time by half"
        )
        # The joined line is still the bullet it started as.
        assert merged[0].bullet is True

    def test_a_new_bullet_is_never_absorbed(self) -> None:
        lines = [
            line("Rebuilt the payments ledger", x0=60, x1=400, top=300, bullet=True),
            line("owned the migration plan", x0=60, x1=380, top=286, bullet=True),
        ]
        assert len(merge_wrapped(lines)) == 2

    def test_a_finished_sentence_does_not_absorb_the_next_line(self) -> None:
        lines = [
            line("Rebuilt the payments ledger.", x0=60, x1=400, top=300, bullet=True),
            line("owned the migration plan", x0=60, x1=380, top=286),
        ]
        assert len(merge_wrapped(lines)) == 2

    def test_hanging_indent_rejoins_a_wrap_starting_with_an_acronym(self) -> None:
        """The case the lowercase rule alone gets wrong.

        A wrap that lands on an acronym or a proper noun starts with a capital
        and is indistinguishable from a new line by text alone. The hanging
        indent it sits on is what gives it away.
        """
        lines = [
            line(
                "Introduced accessibility auditing to CI and brought the catalogue to",
                x0=243.8,
                x1=545.2,
                top=300,
                bullet=True,
            ),
            line("WCAG 2.1 AA.", x0=251.8, x1=317.9, top=288),
        ]
        merged = merge_wrapped(lines)
        assert len(merged) == 1
        assert merged[0].text.endswith("catalogue to WCAG 2.1 AA.")

    def test_a_right_aligned_date_does_not_absorb_the_employer_below_it(self) -> None:
        """The reason the wrap test is an indent and not a right margin.

        A right-aligned date sits flush to the margin by definition, so "the
        previous line reached the right edge" is always true beneath one, and
        using it fuses the employer into the date. These are real coordinates
        from the single-column fixture.
        """
        lines = [
            line("Senior Software Engineer", x0=39.7, x1=178.9, top=300, bold=True),
            line("Mar 2021 - Present", x0=467.9, x1=573.0, top=300, bold=True),
            line("Northwind Systems", x0=39.7, x1=128.6, top=288),
            line("Austin, TX", x0=525.5, x1=573.0, top=288),
        ]
        assert [item.text for item in merge_wrapped(lines)] == [
            "Senior Software Engineer",
            "Mar 2021 - Present",
            "Northwind Systems",
            "Austin, TX",
        ]

    def test_a_flush_continuation_starting_capitalised_is_left_alone(self) -> None:
        """Deliberately conservative: no indent and no lowercase means no merge.

        An unmerged line is visible on the review screen. A wrong merge fuses
        two facts together and is not.
        """
        lines = [
            line("Senior Software Engineer", x0=39.7, x1=178.9, top=300),
            line("Northwind Systems", x0=39.7, x1=128.6, top=288),
        ]
        assert len(merge_wrapped(lines)) == 2

    def test_a_heading_is_not_absorbed_into_the_line_above(self) -> None:
        """Headings start with a capital, which is what keeps them separate."""
        lines = [
            line("cut settlement time by half", x0=60, x1=340, top=300),
            line("EDUCATION", x0=50, x1=140, top=280, bold=True, size=13.0),
        ]
        assert [item.text for item in merge_wrapped(lines)] == [
            "cut settlement time by half",
            "EDUCATION",
        ]


class TestBulletGlyphs:
    def test_common_glyph_is_stripped(self) -> None:
        assert strip_bullet("• Rebuilt the ledger") == ("Rebuilt the ledger", True)

    def test_private_use_area_glyph_is_stripped(self) -> None:
        """U+F0B7 is Word's Symbol-font bullet and is everywhere in real files.

        It is not a bullet in any Unicode sense, so a glyph allow-list that
        looks complete will still miss it.
        """
        assert strip_bullet(" Rebuilt the ledger") == ("Rebuilt the ledger", True)

    def test_ordinary_text_is_untouched(self) -> None:
        assert strip_bullet("Rebuilt the ledger") == ("Rebuilt the ledger", False)

    def test_a_lone_glyph_is_not_a_bullet(self) -> None:
        assert strip_bullet("—") == ("—", False)


class TestNormalisation:
    def test_ligatures_are_folded(self) -> None:
        """Otherwise "workflow" does not match "workflow"."""
        assert normalise_text("workﬂow") == "workflow"

    def test_soft_hyphen_is_removed(self) -> None:
        assert normalise_text("re­built") == "rebuilt"

    def test_non_breaking_space_becomes_a_space(self) -> None:
        assert normalise_text("Mar 2021") == "Mar 2021"

    def test_runs_of_whitespace_collapse(self) -> None:
        assert normalise_text("Alex     Morgan  ") == "Alex Morgan"


class TestBodySize:
    def test_median_ignores_a_large_heading(self) -> None:
        lines = [
            line("HEADING", x0=50, x1=140, top=300, size=18.0),
            *[
                line("body", x0=50, x1=300, top=280 - index * 14, size=10.0)
                for index in range(9)
            ],
        ]
        assert body_size(lines) == 10.0
