"""Heading detection, sectioning, and the contact block.

The asymmetry these tests encode: a missed heading is recoverable because its
content lands in the previous section and the user sees it on the review
screen, while a false heading shreds a job into pieces nobody goes looking for.
So the cases about what must *not* be a heading are the load-bearing ones here.
"""

from __future__ import annotations

from studio.ingest.contact import Contact, parse_contact
from studio.ingest.pdf import Line
from studio.ingest.segment import (
    classify_heading,
    heading_style,
    matches_style,
    normalise_heading,
    segment,
    unspace,
)

BODY = 10.0


def text(value: str, *, top: float, size: float = BODY, bold: bool = False,
         x0: float = 50.0, bullet: bool = False) -> Line:
    return Line(
        text=value, x0=x0, x1=x0 + len(value) * 5.0, top=top, size=size,
        bold=bold, bullet=bullet,
    )


def resume(*rows: Line) -> list[Line]:
    """Rows top to bottom, so tests read in document order."""
    return [
        Line(
            text=row.text, x0=row.x0, x1=row.x1, top=700.0 - index * 16.0,
            size=row.size, bold=row.bold, bullet=row.bullet,
        )
        for index, row in enumerate(rows)
    ]


def keys(lines: list[Line]) -> list[str]:
    return [item.key for item in segment(lines)]


class TestAliases:
    def test_the_common_variants_all_resolve(self) -> None:
        assert classify_heading("PROFESSIONAL EXPERIENCE") == "experience"
        assert classify_heading("Employment History") == "experience"
        assert classify_heading("Core Competencies") == "skills"
        assert classify_heading("Technical Proficiencies") == "skills"
        assert classify_heading("Objective") == "summary"
        assert classify_heading("About Me") == "summary"
        assert classify_heading("Selected Projects") == "projects"

    def test_a_trailing_colon_does_not_defeat_the_table(self) -> None:
        assert classify_heading("SKILLS:") == "skills"

    def test_a_compound_heading_resolves_on_its_recognised_part(self) -> None:
        assert classify_heading("Skills & Tools") == "skills"
        assert classify_heading("Experience and Achievements") == "experience"

    def test_an_unknown_heading_is_not_forced_into_a_section(self) -> None:
        assert classify_heading("Volunteering") is None
        assert classify_heading("Northwind Systems") is None

    def test_normalisation_is_case_and_punctuation_insensitive(self) -> None:
        assert normalise_heading("  WORK  EXPERIENCE:  ") == "work experience"


class TestHeadingsThatMustNotBeFound:
    def test_a_bold_employer_name_is_not_a_heading(self) -> None:
        """The expensive false positive.

        It is bold, it is short, and it sits under a gap. The comma in the
        location is what has to overpower all three, because promoting this
        line splits one job into two half-jobs.
        """
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("alex@example.com", top=0),
            text("EXPERIENCE", top=0, size=13.0, bold=True),
            text("Senior Software Engineer", top=0, bold=True),
            text("Northwind Systems, Austin TX", top=0, bold=True),
            text("Rebuilt the payments ledger", top=0, bullet=True),
            text("Led the migration of 40 services off a shared database", top=0, bullet=True),
            text("Introduced contract tests between two services", top=0, bullet=True),
        )
        assert keys(lines) == ["contact", "experience"]
        experience = segment(lines)[1]
        assert any("Northwind" in line.text for line in experience.lines)

    def test_a_bold_job_title_is_not_a_heading(self) -> None:
        """No comma and no digit to save us -- only the score being short of
        the threshold on boldness and brevity alone."""
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("alex@example.com", top=0),
            text("EXPERIENCE", top=0, size=13.0, bold=True),
            text("Senior Software Engineer", top=0, bold=True),
            text("Rebuilt the payments ledger", top=0, bullet=True),
            text("Led the migration of 40 services", top=0, bullet=True),
        )
        assert keys(lines) == ["contact", "experience"]

    def test_a_dated_line_is_not_a_heading(self) -> None:
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("alex@example.com", top=0),
            text("EXPERIENCE", top=0, size=13.0, bold=True),
            text("Northwind Systems 2021 - Present", top=0, bold=True, size=12.0),
            text("Rebuilt the payments ledger", top=0, bullet=True),
            text("Led the migration of 40 services", top=0, bullet=True),
        )
        assert keys(lines) == ["contact", "experience"]

    def test_the_name_at_the_top_is_never_a_heading(self) -> None:
        """A 20pt bold name outscores most real headings; position saves it."""
        lines = resume(
            text("ALEX MORGAN", top=0, size=20.0, bold=True),
            text("alex@example.com", top=0),
            text("SUMMARY", top=0, size=13.0, bold=True),
            text("Backend engineer.", top=0),
        )
        found = segment(lines)
        assert found[0].key == "contact"
        assert found[0].lines[0].text == "ALEX MORGAN"


class TestHeadingsThatMustBeFound:
    def test_all_caps_heading(self) -> None:
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("EDUCATION", top=0, bold=True),
            text("University of Texas", top=0),
        )
        assert "education" in keys(lines)

    def test_title_case_heading_in_the_body_face(self) -> None:
        """Not bold, not caps, not larger -- carried entirely by the alias."""
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("Work Experience", top=0),
            text("Senior Engineer", top=0),
        )
        assert "experience" in keys(lines)

    def test_a_mostly_bold_document_does_not_lose_its_headings(self) -> None:
        """When everything is bold, boldness stops being evidence and the
        remaining signals have to carry the heading on their own."""
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("EXPERIENCE", top=0, bold=True),
            text("Senior Engineer at Northwind", top=0, bold=True),
            text("Rebuilt the payments ledger", top=0, bold=True, bullet=True),
            text("EDUCATION", top=0, bold=True),
            text("University of Texas", top=0, bold=True),
        )
        assert keys(lines) == ["contact", "experience", "education"]


class TestRealResumeFalsePositives:
    """Drawn from a real resume that the first version of this file shredded.

    Its job titles and degree names were bold, short and preceded by a gap --
    three weak signals, which was exactly the base threshold. Every one became
    a section, which left the experience and education sections holding no
    lines at all, and an empty section is dropped: the entire work history
    disappeared and nothing reported it.
    """

    def resume(self) -> list[Line]:
        heading = dict(size=13.0, bold=True)
        job = dict(size=11.0, bold=True)
        rows = [
            ("Hamza Khan", dict(size=20.0, bold=True)),
            ("hamza@example.com | London, UK", {}),
            ("SUMMARY", heading),
            ("Data scientist building ML systems end to end.", {}),
            ("EXPERIENCE", heading),
            ("AI Engineer", job),
            ("EDI.ai", {}),
            ("Built retrieval pipelines over clinical documents.", {}),
            ("Tech Lead", job),
            ("Acme Analytics", {}),
            ("Led a team of four across two products.", {}),
            ("Founding Engineer", job),
            ("Northwind", {}),
            ("EDUCATION", heading),
            ("Bachelor of Data Science", job),
            ("University of London", {}),
            ("A-Levels", job),
            ("O-Levels", job),
            ("SKILLS", heading),
            ("Python, SQL, PyTorch, Docker, AWS", {}),
        ]
        return resume(*[text(value, top=0, **kwargs) for value, kwargs in rows])

    def test_job_titles_do_not_become_sections(self) -> None:
        found = {item.key for item in segment(self.resume())}
        assert found == {"contact", "summary", "experience", "education", "skills"}

    def test_the_work_history_survives_intact(self) -> None:
        """The failure was not that a job title looked odd in the list -- it
        was that every job vanished."""
        experience = next(
            item for item in segment(self.resume()) if item.key == "experience"
        )
        for expected in ("AI Engineer", "EDI.ai", "Tech Lead", "Founding Engineer"):
            assert expected in experience.text

    def test_degrees_stay_inside_education(self) -> None:
        education = next(
            item for item in segment(self.resume()) if item.key == "education"
        )
        for expected in ("Bachelor of Data Science", "A-Levels", "O-Levels"):
            assert expected in education.text

    def test_a_company_name_with_a_dot_is_not_a_section(self) -> None:
        """"EDI.ai" has no comma and no digit, so the content-mark penalty
        never fires on it. Only the style test excludes it."""
        assert all(item.heading != "EDI.ai" for item in segment(self.resume()))


class TestHeadingStyle:
    def test_the_style_is_learned_from_recognised_headings(self) -> None:
        lines = resume(
            text("Hamza Khan", top=0, size=20.0, bold=True),
            text("EXPERIENCE", top=0, size=13.0, bold=True),
            text("Senior Engineer", top=0, size=11.0, bold=True),
            text("EDUCATION", top=0, size=13.0, bold=True),
            text("University of London", top=0),
        )
        style = heading_style(lines)
        assert style is not None
        # The name is not a heading, so its 20pt must not drag the size up.
        assert style.size == 13.0
        assert style.bold is True
        assert style.upper is True

    def test_a_quieter_line_does_not_match(self) -> None:
        lines = resume(
            text("EXPERIENCE", top=0, size=13.0, bold=True),
            text("EDUCATION", top=0, size=13.0, bold=True),
        )
        style = heading_style(lines)
        assert style is not None
        assert matches_style(text("AI Engineer", top=0, size=11.0, bold=True), style) is False
        assert matches_style(text("VOLUNTEERING", top=0, size=13.0, bold=True), style) is True

    def test_title_case_fails_a_document_whose_headings_are_uppercase(self) -> None:
        """Same size, same weight, different capitalisation. On this resume
        that is the only thing separating a heading from a job title."""
        lines = resume(
            text("EXPERIENCE", top=0, size=12.0, bold=True),
            text("SKILLS", top=0, size=12.0, bold=True),
        )
        style = heading_style(lines)
        assert matches_style(text("Tech Lead", top=0, size=12.0, bold=True), style) is False

    def test_no_recognisable_heading_means_no_style(self) -> None:
        lines = resume(
            text("Hamza Khan", top=0, size=18.0, bold=True),
            text("Some prose about a career.", top=0),
        )
        assert heading_style(lines) is None

    def test_without_a_style_a_job_title_is_still_rejected(self) -> None:
        """Nothing to compare against, so the line itself must be
        overwhelming. Title case is what "AI Engineer" cannot fix."""
        lines = resume(
            text("Hamza Khan", top=0, size=18.0, bold=True),
            text("hamza@example.com", top=0),
            text("AI Engineer", top=0, size=11.0, bold=True),
            text("Built retrieval pipelines over clinical documents.", top=0),
            text("Tech Lead", top=0, size=11.0, bold=True),
            text("Led a team of four.", top=0),
        )
        assert [item.key for item in segment(lines)] == ["contact"]

    def test_an_unknown_section_is_still_found_when_it_matches(self) -> None:
        """The fix must not cost us real unlabelled sections."""
        lines = resume(
            text("Hamza Khan", top=0, size=18.0, bold=True),
            text("hamza@example.com", top=0),
            text("EXPERIENCE", top=0, size=13.0, bold=True),
            text("Senior Engineer", top=0, size=11.0, bold=True),
            text("Did the work.", top=0),
            text("VOLUNTEERING", top=0, size=13.0, bold=True),
            text("Taught evening classes at the library.", top=0),
        )
        found = segment(lines)
        assert [item.key for item in found] == [
            "contact",
            "experience",
            "other",
        ]
        assert found[-1].heading == "VOLUNTEERING"


class TestSectioning:
    def test_a_resume_with_no_headings_is_all_contact(self) -> None:
        """Nothing is dropped just because the layout was unreadable."""
        lines = resume(
            text("Alex Morgan", top=0),
            text("alex@example.com", top=0),
            text("Did some things at a company", top=0),
        )
        found = segment(lines)
        assert [item.key for item in found] == ["contact"]
        assert len(found[0].lines) == 3

    def test_the_contact_block_is_emitted_even_when_unlabelled(self) -> None:
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("SUMMARY", top=0, size=13.0, bold=True),
            text("Backend engineer.", top=0),
        )
        found = segment(lines)
        assert found[0].key == "contact"
        assert found[0].heading == ""

    def test_a_repeated_heading_is_folded_into_one_section(self) -> None:
        """A resume continuing EXPERIENCE after a page break must not produce
        two experience sections, one of which the merge would drop."""
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("EXPERIENCE", top=0, size=13.0, bold=True),
            text("Senior Engineer", top=0),
            text("EDUCATION", top=0, size=13.0, bold=True),
            text("University of Texas", top=0),
            text("EXPERIENCE", top=0, size=13.0, bold=True),
            text("Junior Engineer", top=0),
        )
        found = segment(lines)
        assert [item.key for item in found] == ["contact", "experience", "education"]
        experience = found[1]
        assert {"Senior Engineer", "Junior Engineer"} <= {
            line.text for line in experience.lines
        }

    def test_a_labelled_contact_block_merges_with_the_inferred_one(self) -> None:
        """Two-column resumes commonly label the sidebar CONTACT, so the name
        above it and the details inside it are one block, not two."""
        lines = resume(
            text("Priya Raman", top=0, size=20.0, bold=True),
            text("CONTACT", top=0, size=12.0, bold=True),
            text("priya@example.com", top=0),
        )
        found = segment(lines)
        assert [item.key for item in found] == ["contact"]
        assert len(found[0].lines) == 2

    def test_an_unknown_section_is_kept_and_marked_not_importable(self) -> None:
        """Shown to the user rather than silently discarded."""
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("alex@example.com", top=0),
            text("VOLUNTEERING", top=0, size=13.0, bold=True),
            text("Taught evening classes at the local library", top=0),
            text("Ran the weekend coding club for two years", top=0),
        )
        found = segment(lines)
        unknown = [item for item in found if item.key == "other"]
        assert len(unknown) == 1
        assert unknown[0].heading == "VOLUNTEERING"
        assert unknown[0].importable is False
        assert "Taught evening classes" in unknown[0].text

    def test_two_different_unknown_sections_stay_separate(self) -> None:
        """Failing to classify two headings does not make them one section."""
        lines = resume(
            text("Alex Morgan", top=0, size=18.0, bold=True),
            text("alex@example.com", top=0),
            text("VOLUNTEERING", top=0, size=13.0, bold=True),
            text("Taught evening classes at the local library", top=0),
            text("PUBLICATIONS", top=0, size=13.0, bold=True),
            text("A paper about append-only ledgers", top=0),
        )
        assert [item.key for item in segment(lines)].count("other") == 2


class TestContact:
    def line_block(self, *values: str) -> list[Line]:
        return resume(*[text(value, top=0) for value in values])

    def test_pulls_every_field_out_of_a_single_run_on_line(self) -> None:
        block = self.line_block(
            "Alex Morgan",
            "Austin, TX · alex.morgan@example.com · (512) 555-0148 · "
            "linkedin.com/in/alexmorgan · github.com/alexmorgan",
        )
        found = parse_contact(block)
        assert found.name == "Alex Morgan"
        assert found.email == "alex.morgan@example.com"
        assert found.phone == "(512) 555-0148"
        assert found.location == "Austin, TX"
        assert found.linkedin == "linkedin.com/in/alexmorgan"
        assert found.github == "github.com/alexmorgan"

    def test_a_profile_url_is_not_reported_as_a_personal_site(self) -> None:
        """Otherwise every resume gets a "website" that is really its LinkedIn."""
        block = self.line_block(
            "Alex Morgan", "alex@example.com linkedin.com/in/alexmorgan"
        )
        assert parse_contact(block).website is None

    def test_a_real_personal_site_is_found(self) -> None:
        block = self.line_block(
            "Alex Morgan", "alex@example.com · alexmorgan.dev · github.com/alexmorgan"
        )
        assert parse_contact(block).website == "alexmorgan.dev"

    def test_a_title_under_the_name_is_picked_up(self) -> None:
        block = self.line_block(
            "Alex Morgan", "Senior Backend Engineer", "alex@example.com"
        )
        found = parse_contact(block)
        assert found.name == "Alex Morgan"
        assert found.title == "Senior Backend Engineer"

    def test_a_location_under_the_name_is_not_mistaken_for_a_title(self) -> None:
        block = self.line_block("Alex Morgan", "Austin, TX", "alex@example.com")
        found = parse_contact(block)
        assert found.title == ""
        assert found.location == "Austin, TX"

    def test_a_date_range_is_not_read_as_a_phone_number(self) -> None:
        block = self.line_block("Alex Morgan", "2014 - 2018", "alex@example.com")
        assert parse_contact(block).phone == ""

    def test_a_run_together_international_number_is_found(self) -> None:
        """From a real resume. Most of the world writes a phone number with no
        separators at all, and a pattern that demanded one after the area code
        matched nothing."""
        block = self.line_block(
            "Rao Muhammad Hamza",
            "+441632960123 | rao@example.com | https://github.com/raoexample",
        )
        assert parse_contact(block).phone == "+441632960123"

    def test_a_less_common_tld_is_still_a_website(self) -> None:
        """An allow-list of nine suffixes drops anyone whose site is not a
        .com -- here a .tech, alongside the .dev and country codes people
        actually own."""
        block = self.line_block(
            "Rao Muhammad Hamza",
            "+441632960123 | rao@example.com | https://github.com/raoexample "
            "| raoexample.dev",
        )
        found = parse_contact(block)
        assert found.website == "raoexample.dev"
        assert found.github == "https://github.com/raoexample"
        assert found.email == "rao@example.com"

    def test_a_pipe_separated_header_yields_every_field(self) -> None:
        block = self.line_block(
            "Rao Muhammad Hamza",
            "+441632960123 | rao@example.com | https://github.com/raoexample "
            "| linkedin.com/in/raoexample | raoexample.dev",
        )
        found = parse_contact(block)
        assert found.name == "Rao Muhammad Hamza"
        assert found.phone == "+441632960123"
        assert found.email == "rao@example.com"
        assert found.github == "https://github.com/raoexample"
        assert found.linkedin == "linkedin.com/in/raoexample"
        assert found.website == "raoexample.dev"

    def test_parenthesised_area_codes_keep_their_bracket(self) -> None:
        block = self.line_block("Alex Morgan", "(512) 555-0148 · alex@example.com")
        assert parse_contact(block).phone == "(512) 555-0148"

    def test_a_dotted_word_with_no_real_suffix_is_not_a_website(self) -> None:
        """Why a bare domain still needs a recognised suffix rather than any
        two dotted tokens.

        Note the limit this does not claim: "socket.io" is a real domain, so
        nothing here can tell a library from a personal site by pattern alone.
        That ambiguity is tolerable because only the header block is searched,
        and a list of libraries does not live there.
        """
        block = self.line_block("Alex Morgan", "Node.js, Next.js", "alex@example.com")
        assert parse_contact(block).website is None

    def test_an_empty_block_yields_empty_fields_rather_than_raising(self) -> None:
        assert parse_contact([]) == Contact()


class TestLetterSpacedHeadings:
    """`letter-spacing` on a heading is real spaces in the PDF.

    It is ordinary résumé typography -- every template site uses it -- and no
    renderer preserves it as tracking. The text layer genuinely contains
    "E X P E R I E N C E", which matches no alias.

    Measured on `resume_wonky.pdf` before this: **zero sections**. Twenty-seven
    lines swept into the contact block, and a whole work history reported as
    unreadable.
    """

    def test_a_tracked_heading_is_recognised(self) -> None:
        assert classify_heading("E X P E R I E N C E") == "experience"
        assert classify_heading("S K I L L S") == "skills"
        assert classify_heading("E D U C A T I O N") == "education"

    def test_a_heading_that_swallowed_its_first_line(self) -> None:
        """The word break where the run of single letters ends is kept.

        "L A N G U A G E S English" must come back as two words, not one.
        """
        assert unspace("L A N G U A G E S English") == "LANGUAGES English"

    def test_ordinary_prose_is_untouched(self) -> None:
        line = "Rebuilt the payments ledger on an append-only model"
        assert unspace(line) == line

    def test_loose_initials_are_left_alone(self) -> None:
        """Below the four-token floor, so a degree keeps its shape."""
        assert unspace("B S Informatics") == "B S Informatics"
        assert unspace("R & D") == "R & D"

    def test_a_mixed_line_is_not_collapsed(self) -> None:
        """Under the threshold: this is content that happens to have initials."""
        assert unspace("A B testing and R analysis for the team") == (
            "A B testing and R analysis for the team"
        )
