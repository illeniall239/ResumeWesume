"""Section schemas, skills splitting, and the merge into legacy ResumeData.

The merge is the last place an import can be silently wrong before it becomes
a document, and the thing it must never get wrong is the parallel bullet
arrays: ``description`` and ``descriptionStyles`` desyncing puts a bullet's
text against another bullet's style, which renders as plausible nonsense.
"""

from __future__ import annotations

from studio.doc.legacy import from_resume_data
from studio.ingest.contact import Contact
from studio.ingest.merge import title_for, to_resume_data
from studio.ingest.pdf import Line
from studio.ingest.schemas import (
    EducationOut,
    ExperienceOut,
    ProjectsOut,
    SummaryOut,
)
from studio.ingest.skills import parse_credentials, parse_skills


def lines(*values: str) -> list[Line]:
    return [Line(text=value, top=700.0 - index * 14) for index, value in enumerate(values)]


def merged(**kwargs) -> dict:
    base = {
        "contact": Contact(name="Alex Morgan", email="alex@example.com"),
        "parts": {},
        "skills": {},
        "order": [],
    }
    base.update(kwargs)
    return to_resume_data(**base)


class TestTolerantSchemas:
    def test_a_bare_array_is_rehoused_under_the_single_key(self) -> None:
        """The shape ``repair_json`` cannot return, and the reason each schema
        has exactly one top-level key."""
        payload = ExperienceOut.house([{"title": "Engineer", "company": "Northwind"}])
        parsed = ExperienceOut.model_validate(payload)
        assert len(parsed.entries) == 1
        assert parsed.entries[0].company == "Northwind"

    def test_a_dict_passes_through_unhoused(self) -> None:
        payload = ExperienceOut.house({"entries": [{"title": "Engineer"}]})
        assert ExperienceOut.model_validate(payload).entries[0].title == "Engineer"

    def test_a_scalar_where_a_list_belongs_becomes_a_one_item_list(self) -> None:
        parsed = ExperienceOut.model_validate(
            {"entries": [{"title": "Engineer", "bullets": "Rebuilt the ledger"}]}
        )
        assert parsed.entries[0].bullets == ["Rebuilt the ledger"]

    def test_objects_where_strings_belong_are_flattened(self) -> None:
        parsed = ExperienceOut.model_validate(
            {"entries": [{"bullets": [{"text": "Rebuilt the ledger"}, {"value": "Led it"}]}]}
        )
        assert parsed.entries[0].bullets == ["Rebuilt the ledger", "Led it"]

    def test_nulls_inside_a_list_are_dropped(self) -> None:
        parsed = ExperienceOut.model_validate(
            {"entries": [{"bullets": ["Rebuilt the ledger", None, ""]}]}
        )
        assert parsed.entries[0].bullets == ["Rebuilt the ledger"]

    def test_a_null_entry_does_not_fail_the_whole_section(self) -> None:
        """One bad job must cost one job, not the entire work history."""
        parsed = ExperienceOut.model_validate(
            {"entries": [{"title": "Engineer"}, None, "garbage", {"title": "Analyst"}]}
        )
        assert [entry.title for entry in parsed.entries] == ["Engineer", "Analyst"]

    def test_a_number_where_text_belongs_is_stringified(self) -> None:
        parsed = EducationOut.model_validate({"entries": [{"years": 2018}]})
        assert parsed.entries[0].years == "2018"

    def test_every_field_is_optional(self) -> None:
        assert ExperienceOut.model_validate({}).entries == []
        assert SummaryOut.model_validate({}).summary == ""

    def test_unknown_keys_are_ignored_rather_than_rejected(self) -> None:
        """A model that invents a field has not failed at the fields we asked
        for."""
        parsed = ExperienceOut.model_validate(
            {"entries": [{"title": "Engineer", "confidence": 0.9}], "notes": "hi"}
        )
        assert parsed.entries[0].title == "Engineer"


class TestSkills:
    def test_a_comma_separated_line_splits(self) -> None:
        assert parse_skills(lines("Python, Go, PostgreSQL")) == [
            "Python",
            "Go",
            "PostgreSQL",
        ]

    def test_one_skill_per_line_is_kept_whole(self) -> None:
        """The layout a two-column sidebar produces."""
        assert parse_skills(lines("TypeScript", "React", "Node.js")) == [
            "TypeScript",
            "React",
            "Node.js",
        ]

    def test_a_repeated_label_is_stripped(self) -> None:
        """Otherwise the word "Languages" is imported as a language."""
        assert parse_skills(lines("Languages: Python, Go")) == ["Python", "Go"]

    def test_slashes_inside_a_skill_are_not_split_on(self) -> None:
        assert parse_skills(lines("CI/CD, ETL/ELT")) == ["CI/CD", "ETL/ELT"]

    def test_duplicates_are_dropped_keeping_the_first_spelling(self) -> None:
        assert parse_skills(lines("Python, Go", "python, Rust")) == [
            "Python",
            "Go",
            "Rust",
        ]

    def test_a_prose_paragraph_does_not_become_forty_skills(self) -> None:
        prose = (
            "Experienced backend engineer who has spent the last eight years "
            "building payment systems and ledgers at scale"
        )
        assert parse_skills(lines(prose)) == []


class TestCredentials:
    """Certifications and awards, taken verbatim from a real resume.

    Read as a skills list, this section lost half of itself: two entries were
    over the character cap sized for the word "Python", one was split at its
    own comma, and one had "Storytelling and Influencing:" removed by a rule
    meant for "Languages:".
    """

    REAL = [
        "IBM Data Analysis with Python - coursera.org/verify/BP29BZCVM45H",
        "IBM - Python for Data Science, AI & Development - coursera.org/verify/4FYQXKBE9P8H",
        "Databases and SQL for Data Science with Python - coursera.org/verify/CKEH6SGZ2HJY",
        "Storytelling and Influencing: Communicate with Impact - coursera.org/verify/9V2HSXGDY34F",
    ]

    def test_every_credential_survives_verbatim(self) -> None:
        assert parse_credentials(lines(*self.REAL)) == self.REAL

    def test_a_comma_inside_a_name_is_not_a_separator(self) -> None:
        """The line break is the separator here; the comma belongs to the
        credential."""
        one = "IBM - Python for Data Science, AI & Development - coursera.org/verify/4FY"
        assert parse_credentials(lines(one, "Another Course - example.com/x")) == [
            one,
            "Another Course - example.com/x",
        ]

    def test_a_colon_inside_a_title_is_not_a_label(self) -> None:
        """"Storytelling and Influencing:" reads exactly like "Languages:" and
        is not one. What follows a real label is a delimited list."""
        titled = "Storytelling and Influencing: Communicate with Impact - coursera.org/x"
        assert parse_credentials(lines(titled, "Second Course - example.com/y")) == [
            titled,
            "Second Course - example.com/y",
        ]

    def test_a_long_credential_is_not_dropped(self) -> None:
        """A verification URL puts almost every real credential over a cap
        sized for a skill token, and the longest are the most specific."""
        long_one = (
            "Databases and SQL for Data Science with Python - "
            "coursera.org/verify/CKEH6SGZ2HJY"
        )
        assert long_one in parse_credentials(lines(long_one, "Other - example.com/z"))

    def test_a_compact_one_line_list_still_splits(self) -> None:
        """The other real layout: several credentials on a single line."""
        assert parse_credentials(
            lines("AWS Solutions Architect, Google Data Engineer, Databricks ML")
        ) == ["AWS Solutions Architect", "Google Data Engineer", "Databricks ML"]

    def test_a_single_credential_on_one_line_is_not_split_at_its_comma(self) -> None:
        """A URL says this is one credential that contains a comma, not a list."""
        one = "IBM - Python for Data Science, AI & Development - coursera.org/verify/4FY"
        assert parse_credentials(lines(one)) == [one]

    def test_a_paragraph_is_still_rejected(self) -> None:
        assert parse_credentials(lines("x" * 400)) == []

    def test_skills_are_unaffected_by_the_credential_rules(self) -> None:
        assert parse_skills(lines("Languages: Python, Go")) == ["Python", "Go"]
        assert parse_skills(lines("Frontend: React, Vue, Svelte")) == [
            "React",
            "Vue",
            "Svelte",
        ]

    def test_a_skills_label_is_only_stripped_before_a_real_list(self) -> None:
        """A colon with a single value after it is not a category heading."""
        assert parse_skills(lines("Storytelling and Influencing: Communicate")) == [
            "Storytelling and Influencing: Communicate"
        ]


class TestMerge:
    def test_bullet_arrays_are_always_the_same_length(self) -> None:
        data = merged(
            parts={
                "experience": ExperienceOut.model_validate(
                    {
                        "entries": [
                            {"title": "Engineer", "bullets": ["a", "b", "c"]},
                            {"title": "Analyst", "bullets": []},
                        ]
                    }
                )
            }
        )
        for entry in data["workExperience"]:
            assert len(entry["description"]) == len(entry["descriptionStyles"])
        assert data["workExperience"][0]["descriptionStyles"] == ["bullet"] * 3

    def test_a_failed_section_lands_empty_and_the_rest_survives(self) -> None:
        """The containment promise, at the point it actually has to hold."""
        data = merged(
            parts={
                "experience": None,
                "summary": SummaryOut.model_validate({"summary": "Backend engineer."}),
            },
            skills={"skills": ["Python"]},
        )
        assert data["workExperience"] == []
        assert data["summary"] == "Backend engineer."
        assert data["additional"]["technicalSkills"] == ["Python"]

    def test_every_section_missing_still_produces_a_valid_document(self) -> None:
        doc = from_resume_data(merged())
        assert doc.personal.name == "Alex Morgan"
        assert doc.experience == []

    def test_section_order_follows_the_source_document(self) -> None:
        """An imported resume renders the way the person wrote it."""
        data = merged(order=["contact", "skills", "education", "experience"])
        keys = [item["key"] for item in data["sectionMeta"]]
        assert keys[:3] == ["skills", "education", "experience"]
        # Sections the source never mentioned still exist, at the end.
        assert set(keys) == {"summary", "experience", "education", "projects", "skills"}

    def test_only_populated_sections_are_visible(self) -> None:
        data = merged(skills={"skills": ["Python"]})
        visible = {item["key"] for item in data["sectionMeta"] if item["isVisible"]}
        assert visible == {"skills"}

    def test_orders_are_contiguous_from_zero(self) -> None:
        orders = [item["order"] for item in merged()["sectionMeta"]]
        assert orders == list(range(len(orders)))

    def test_skill_groups_map_to_their_legacy_fields(self) -> None:
        data = merged(
            skills={
                "skills": ["Python"],
                "languages": ["Tamil"],
                "certifications": ["AWS SA"],
                "awards": ["Employee of the Month"],
            }
        )
        assert data["additional"] == {
            "technicalSkills": ["Python"],
            "languages": ["Tamil"],
            "certificationsTraining": ["AWS SA"],
            "awards": ["Employee of the Month"],
        }

    def test_the_whole_payload_survives_from_resume_data(self) -> None:
        doc = from_resume_data(
            merged(
                parts={
                    "summary": SummaryOut.model_validate({"summary": "Backend engineer."}),
                    "experience": ExperienceOut.model_validate(
                        {
                            "entries": [
                                {
                                    "title": "Senior Engineer",
                                    "company": "Northwind",
                                    "years": "2021 – Present",
                                    "bullets": ["Rebuilt the ledger"],
                                }
                            ]
                        }
                    ),
                    "education": EducationOut.model_validate(
                        {"entries": [{"institution": "UT Austin", "degree": "B.S."}]}
                    ),
                    "projects": ProjectsOut.model_validate(
                        {"entries": [{"name": "Sightline", "bullets": ["A visualiser"]}]}
                    ),
                },
                skills={"skills": ["Python"]},
                order=["contact", "summary", "experience", "education", "projects", "skills"],
            )
        )
        assert doc.summary is not None and doc.summary.text == "Backend engineer."
        assert doc.experience[0].company == "Northwind"
        # Ids are minted server-side, once, by from_resume_data.
        assert doc.experience[0].nid.startswith("exp_")
        assert doc.experience[0].bullets[0].nid.startswith("blt_")
        assert doc.education[0].institution == "UT Austin"
        assert doc.projects[0].name == "Sightline"
        assert doc.skills[0].items[0].text == "Python"

    def test_non_ascii_survives_the_round_trip(self) -> None:
        """An en-dash in a date range and an accent in a name are content."""
        doc = from_resume_data(
            merged(
                contact=Contact(name="Chloé Dubois"),
                parts={
                    "experience": ExperienceOut.model_validate(
                        {"entries": [{"company": "Nörthwind", "years": "2021 – Present"}]}
                    )
                },
            )
        )
        assert doc.personal.name == "Chloé Dubois"
        assert doc.experience[0].years == "2021 – Present"


class TestTitle:
    def test_the_persons_name_is_used_when_we_found_one(self) -> None:
        assert title_for(Contact(name="Alex Morgan"), "cv.pdf") == "Alex Morgan"

    def test_the_filename_is_the_fallback_not_untitled(self) -> None:
        """"Untitled" tells the user nothing about which upload this was."""
        assert title_for(Contact(), "alex-morgan-cv.pdf") == "alex-morgan-cv"

    def test_an_empty_filename_still_yields_something_readable(self) -> None:
        assert title_for(Contact(), "") == "Imported resume"
