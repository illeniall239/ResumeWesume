"""Everything on the page is something the assistant can see, find and change.

"Make Founder into Creator in EDI.ai" failed three times over, and each failure
was silent in the same way -- the assistant reported the word was not in the
résumé, which from where it stood was true:

  * the outline printed a project as ``name (years)``, so the role was not in
    the view it reads every turn;
  * ``find`` matched a project on ``name`` alone, so searching for the word
    returned nothing;
  * and no tool could have changed it anyway. ``set_entry_field`` refused the
    word "role" and redirected to ``set_entry_identity``, which does not accept
    it either -- while the engine had classified ``role`` as soft metadata,
    beside ``years`` and ``location``, all along.

The same drift had hidden more than the role: a whole Certifications or Awards
section keeps its lines in ``strings``, and nothing walked ``strings``. The
section was a heading with no contents as far as the assistant could tell.

The cause is that three separate walks over the document each enumerated it by
hand, so each could omit a different field. ``texts`` is now the one inventory
and ``find`` derives from it; these tests are the check that it stays complete.
"""

from __future__ import annotations

import pytest

from studio.agent.context import find, full_section, outline, texts
from studio.agent.tools import REGISTRY
from studio.doc.apply import OpContext, apply_ops
from studio.doc.autolayout import layout
from studio.doc.schema import (
    CustomItemNode,
    CustomSectionNode,
    EducationNode,
    ExperienceNode,
    PersonalInfo,
    ProjectNode,
    SectionMeta,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

AUTHORIZED = OpContext(granted_tiers={"A", "B", "C"})


def a_resume() -> StudioDoc:
    """One of every shape a piece of writing can take in this schema."""
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", title="Engineer"),
        summary=TextNode(nid="sum_aaaaa", text="Backend engineer."),
        experience=[
            ExperienceNode(
                nid="exp_aaaaa",
                title="Engineer",
                company="Northwind",
                location="Austin",
                bullets=[TextNode(nid="blt_aaaaa", text="Rebuilt the ledger.")],
            )
        ],
        education=[
            EducationNode(
                nid="edu_aaaaa",
                institution="Tessell",
                degree="BSc",
                detail=TextNode(nid="blt_bbbbb", text="Graduated with honours."),
            )
        ],
        projects=[
            ProjectNode(
                nid="prj_aaaaa",
                name="EDI.ai",
                role="Founder",
                github="github.com/amorgan/ledgerline",
                bullets=[TextNode(nid="blt_ccccc", text="Built EDI.")],
            )
        ],
        skills=[
            SkillGroup(nid="sgp_aaaaa", items=[SkillItem(nid="skl_aaaaa", text="Python")])
        ],
        custom=[
            CustomSectionNode(
                nid="cst_aaaaa",
                key="certifications",
                label="Certifications",
                kind="stringList",
                strings=[SkillItem(nid="skl_ccccc", text="IBM Data Analysis with Python")],
            ),
            CustomSectionNode(
                nid="cst_bbbbb",
                key="volunteering",
                label="Volunteering",
                kind="itemList",
                items=[
                    CustomItemNode(
                        nid="cit_aaaaa",
                        title="Night shelter",
                        subtitle="Coordinator",
                        bullets=[TextNode(nid="blt_ddddd", text="Ran the rota.")],
                    )
                ],
            ),
        ],
        sections=[
            SectionMeta(key="summary", label="Summary", order=0),
            SectionMeta(key="experience", label="Experience", order=1),
            SectionMeta(key="education", label="Education", order=2),
            SectionMeta(key="projects", label="Projects", order=3),
            SectionMeta(key="certifications", label="Certifications", order=4),
            SectionMeta(key="volunteering", label="Volunteering", order=5),
            SectionMeta(key="skills", label="Skills", order=6),
        ],
    )
    doc.pages = layout(doc)
    return doc


#: A word only in that field, and the nid a search for it must return.
ONLY_HERE = {
    "Founder": "prj_aaaaa",          # a project's role -- the reported bug
    "amorgan": "prj_aaaaa",          # and its link
    "Austin": "exp_aaaaa",           # an employer's location
    "honours": "blt_bbbbb",          # an education detail
    "IBM": "skl_ccccc",              # a stringList section's contents
    "shelter": "cit_aaaaa",          # an itemList section's contents
    "rota": "blt_ddddd",             # a bullet inside one
}


class TestEverythingIsFindable:
    @pytest.mark.parametrize("word,nid", sorted(ONLY_HERE.items()))
    def test_a_word_that_is_on_the_page_can_be_found(self, word: str, nid: str) -> None:
        hits = find(a_resume(), word)

        assert hits, f"{word!r} is on the page and the search cannot find it"
        assert nid in {hit["nid"] for hit in hits}, (
            f"{word!r} found, but not the node holding it"
        )

    def test_the_inventory_covers_every_nid_the_search_can_return(self) -> None:
        # `find` derives from `texts`, so this is really a statement that the
        # inventory is where completeness is decided -- one place to add a
        # field to, rather than three to remember.
        doc = a_resume()
        inventory = {nid for nid, _, _ in texts(doc)}

        for word, nid in ONLY_HERE.items():
            assert nid in inventory, f"{nid} ({word}) is not in the inventory"


class TestEverythingIsVisible:
    def test_the_outline_shows_a_project_role(self) -> None:
        assert "Founder" in outline(a_resume())

    def test_the_outline_shows_what_is_in_a_custom_section(self) -> None:
        text = outline(a_resume())

        assert "IBM Data Analysis with Python" in text
        assert "Night shelter" in text

    def test_reading_a_project_in_full_shows_its_role(self) -> None:
        assert "Founder" in full_section(a_resume(), "projects")

    def test_a_custom_section_can_be_read_in_full(self) -> None:
        # `read_document(section="certifications")` answered "(no certifications)"
        # about a section sitting on the page.
        by_key = full_section(a_resume(), "certifications")

        assert "IBM Data Analysis with Python" in by_key


class TestEverythingIsChangeable:
    def test_a_project_role_can_be_changed(self) -> None:
        # The end of the reported bug: found, and now actionable.
        doc = a_resume()
        spec = REGISTRY.get("set_entry_field")

        after, _, rejected = apply_ops(
            doc,
            spec.compile(spec.Args(nid="prj_aaaaa", field="role", value="Creator"), doc),
            AUTHORIZED,
        )

        assert rejected == []
        assert after.projects[0].role == "Creator"
        assert find(after, "Creator")[0]["nid"] == "prj_aaaaa"

    def test_a_job_title_still_goes_through_the_consent_gate(self) -> None:
        # Widening `set_entry_field` must not open a back door: an employer and
        # a job title are factual claims and stay Tier C.
        spec = REGISTRY.get("set_entry_field")

        with pytest.raises(Exception) as caught:
            spec.Args(nid="exp_aaaaa", field="title", value="Director")

        assert "set_entry_identity" in str(caught.value)
