"""Invariants the import path must hold for *any* model output.

The unit tests cover the malformed shapes we have actually seen. These cover
the ones we have not: a small model handed a resume section will emit
structures nobody would think to write down, and the import path has to turn
every one of them into a document or into an empty section -- never into an
exception, and never into a corrupt document.

Two properties, both of which mean a real user-visible failure if violated:

*Nothing raises.* An exception here is a whole import lost -- minutes of local
model time -- because one job had a null where a string belonged.

*Bullets and their styles never desync.* The parallel arrays are the one place
in the legacy shape where two lists have to agree, and when they disagree the
document renders one bullet's text with another bullet's style: plausible, and
therefore not noticed.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.doc.legacy import from_resume_data
from studio.ingest.contact import Contact
from studio.ingest.merge import to_resume_data
from studio.ingest.schemas import (
    EducationOut,
    ExperienceOut,
    ProjectsOut,
    SummaryOut,
)

SETTINGS = settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)

# What a model actually puts in a field it was asked for a string: the string,
# nothing, a number, a list it did not flatten, or an object it wrapped.
scalars = st.one_of(
    st.text(max_size=60),
    st.none(),
    st.integers(min_value=-2000, max_value=3000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.booleans(),
)

junk = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=8), children, max_size=4),
    ),
    max_leaves=6,
)

experience_entries = st.dictionaries(
    st.sampled_from(["title", "company", "location", "years", "bullets", "extra"]),
    junk,
    max_size=6,
)

education_entries = st.dictionaries(
    st.sampled_from(["institution", "degree", "years", "description", "extra"]),
    junk,
    max_size=5,
)

project_entries = st.dictionaries(
    st.sampled_from(["name", "role", "years", "github", "website", "bullets"]),
    junk,
    max_size=6,
)

section_keys = st.lists(
    st.sampled_from(
        ["contact", "summary", "experience", "education", "projects", "skills", "other"]
    ),
    max_size=8,
)

skill_lists = st.dictionaries(
    st.sampled_from(["skills", "languages", "certifications", "awards"]),
    st.lists(st.text(max_size=24), max_size=6),
    max_size=4,
)


def _parse(model, payload):
    """Validate, mirroring the pipeline's containment: failure is an empty
    section, never an exception."""
    try:
        return model.model_validate(model.house(payload))
    except Exception:  # noqa: BLE001 - the pipeline catches exactly this broadly
        return None


class TestNothingRaises:
    @given(entries=st.lists(experience_entries, max_size=4))
    @SETTINGS
    def test_arbitrary_experience_output_never_breaks_the_import(self, entries) -> None:
        parsed = _parse(ExperienceOut, {"entries": entries})
        data = to_resume_data(
            contact=Contact(), parts={"experience": parsed}, skills={}, order=[]
        )
        from_resume_data(data)

    @given(entries=st.lists(education_entries, max_size=4))
    @SETTINGS
    def test_arbitrary_education_output_never_breaks_the_import(self, entries) -> None:
        parsed = _parse(EducationOut, {"entries": entries})
        data = to_resume_data(
            contact=Contact(), parts={"education": parsed}, skills={}, order=[]
        )
        from_resume_data(data)

    @given(entries=st.lists(project_entries, max_size=4))
    @SETTINGS
    def test_arbitrary_project_output_never_breaks_the_import(self, entries) -> None:
        parsed = _parse(ProjectsOut, {"entries": entries})
        data = to_resume_data(
            contact=Contact(), parts={"projects": parsed}, skills={}, order=[]
        )
        from_resume_data(data)

    @given(payload=junk)
    @SETTINGS
    def test_a_bare_payload_of_any_shape_is_survivable(self, payload) -> None:
        """Including the bare arrays and bare scalars ``repair_json`` cannot
        return a dict for."""
        parts = {
            "experience": _parse(ExperienceOut, payload),
            "education": _parse(EducationOut, payload),
            "projects": _parse(ProjectsOut, payload),
            "summary": _parse(SummaryOut, payload),
        }
        from_resume_data(
            to_resume_data(contact=Contact(), parts=parts, skills={}, order=[])
        )

    @given(order=section_keys, skills=skill_lists)
    @SETTINGS
    def test_any_section_order_produces_a_valid_document(self, order, skills) -> None:
        from_resume_data(
            to_resume_data(contact=Contact(), parts={}, skills=skills, order=order)
        )


class TestBulletsNeverDesync:
    @given(entries=st.lists(experience_entries, max_size=4))
    @SETTINGS
    def test_experience_arrays_stay_aligned(self, entries) -> None:
        data = to_resume_data(
            contact=Contact(),
            parts={"experience": _parse(ExperienceOut, {"entries": entries})},
            skills={},
            order=[],
        )
        for entry in data["workExperience"]:
            assert len(entry["description"]) == len(entry["descriptionStyles"])
            assert all(style == "bullet" for style in entry["descriptionStyles"])

    @given(entries=st.lists(project_entries, max_size=4))
    @SETTINGS
    def test_project_arrays_stay_aligned(self, entries) -> None:
        data = to_resume_data(
            contact=Contact(),
            parts={"projects": _parse(ProjectsOut, {"entries": entries})},
            skills={},
            order=[],
        )
        for entry in data["personalProjects"]:
            assert len(entry["description"]) == len(entry["descriptionStyles"])

    @given(entries=st.lists(experience_entries, max_size=4))
    @SETTINGS
    def test_bullets_survive_into_the_document_intact(self, entries) -> None:
        """The arrays are rebuilt once more inside ``from_resume_data``; a
        desync introduced there would be just as invisible."""
        data = to_resume_data(
            contact=Contact(),
            parts={"experience": _parse(ExperienceOut, {"entries": entries})},
            skills={},
            order=[],
        )
        doc = from_resume_data(data)
        for source, node in zip(data["workExperience"], doc.experience):
            kept = [text for text in source["description"] if text.strip()]
            assert [bullet.text for bullet in node.bullets] == kept


class TestSectionMeta:
    @given(order=section_keys)
    @SETTINGS
    def test_every_renderable_section_is_always_present_exactly_once(
        self, order
    ) -> None:
        """A section missing from the metadata cannot be turned back on by the
        user, so a duplicate or an omission is a permanently lost section."""
        meta = to_resume_data(
            contact=Contact(), parts={}, skills={}, order=order
        )["sectionMeta"]
        keys = [item["key"] for item in meta]
        assert sorted(keys) == sorted(
            {"summary", "experience", "education", "projects", "skills"}
        )
        assert [item["order"] for item in meta] == list(range(len(keys)))
