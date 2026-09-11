"""Reading the old ResumeData shape into a document."""

from __future__ import annotations

from studio.doc.legacy import from_resume_data
from studio.doc.nodes import kind_of

OLD = {
    "personalInfo": {
        "name": "Alex Morgan",
        "email": "alex@example.com",
        "phone": "+1-555-0142",
        "location": "Austin, TX",
    },
    "summary": "Backend engineer with 8 years building payment platforms.",
    "workExperience": [
        {
            "id": 1,
            "title": "Senior Backend Engineer",
            "company": "Northwind Systems",
            "location": "Austin, TX",
            "years": "Mar 2021 - Present",
            "description": ["Rebuilt the payments ledger.", "Led the async migration."],
            "descriptionStyles": ["bullet", "plain"],
        }
    ],
    "education": [
        {
            "id": 1,
            "institution": "UT Austin",
            "degree": "B.S. Computer Science",
            "years": "2013 - 2017",
            "description": "Graduated with honors.",
        }
    ],
    "personalProjects": [],
    "additional": {
        "technicalSkills": ["Python", "Go", "Kafka"],
        "languages": [],
        "certificationsTraining": ["AWS Solutions Architect"],
        "awards": [],
    },
}


class TestImport:
    def test_content_survives(self) -> None:
        doc = from_resume_data(OLD)
        assert doc.personal.name == "Alex Morgan"
        assert doc.summary is not None
        assert doc.summary.text.startswith("Backend engineer")
        assert doc.experience[0].company == "Northwind Systems"
        assert [b.text for b in doc.experience[0].bullets] == [
            "Rebuilt the payments ledger.",
            "Led the async migration.",
        ]

    def test_bullet_styles_move_onto_the_nodes(self) -> None:
        doc = from_resume_data(OLD)
        assert [b.style for b in doc.experience[0].bullets] == ["bullet", "plain"]

    def test_every_node_gets_a_well_formed_id(self) -> None:
        doc = from_resume_data(OLD)
        for nid in [
            doc.summary.nid,
            doc.experience[0].nid,
            doc.experience[0].bullets[0].nid,
            doc.education[0].nid,
            doc.skills[0].nid,
            doc.skills[0].items[0].nid,
        ]:
            assert kind_of(nid) is not None, nid

    def test_ids_are_unique_across_the_document(self) -> None:
        doc = from_resume_data(OLD)
        ids = [doc.summary.nid, doc.experience[0].nid, doc.education[0].nid]
        ids += [b.nid for b in doc.experience[0].bullets]
        ids += [g.nid for g in doc.skills]
        ids += [i.nid for g in doc.skills for i in g.items]
        assert len(ids) == len(set(ids))

    def test_empty_skill_lists_produce_no_group(self) -> None:
        # The old shape always carries all four keys, mostly empty. Empty groups
        # would render as bare headings with nothing under them.
        doc = from_resume_data(OLD)
        assert {g.key for g in doc.skills} == {"technical", "certifications"}

    def test_desynced_styles_array_does_not_break_import(self) -> None:
        """The exact bug the new schema makes unrepresentable, arriving from
        legacy data: fewer styles than descriptions. Import must tolerate it."""
        broken = {
            **OLD,
            "workExperience": [
                {
                    **OLD["workExperience"][0],
                    "description": ["one", "two", "three"],
                    "descriptionStyles": ["plain"],
                }
            ],
        }
        doc = from_resume_data(broken)
        bullets = doc.experience[0].bullets
        assert [b.text for b in bullets] == ["one", "two", "three"]
        assert [b.style for b in bullets] == ["plain", "bullet", "bullet"]

    def test_garbage_input_does_not_raise(self) -> None:
        for junk in ({}, {"workExperience": "not a list"}, {"personalInfo": 42}):
            assert from_resume_data(junk) is not None
