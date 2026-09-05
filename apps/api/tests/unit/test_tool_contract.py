"""Every tool, exercised through the whole contract it declares.

A tool declares four things: a tier, an argument model, how to compile, and
what grants to mint. The suite covered ``compile`` thoroughly and never called
``grants`` or ``label`` at all -- so ``add_text_box`` shipped naming a
``GrantScope`` member that does not exist, and every call crashed with

    GrantScope has no attribute BULLET_ADD

only once a live model reached for it. The model reported it accurately and
could not work around it.

These run over the registry rather than over a list, so a tool added later is
covered without anyone remembering this file exists.
"""

from __future__ import annotations

import pytest

from studio.agent.tools import REGISTRY, ToolError, ToolSpec
from studio.doc.schema import (
    ExperienceNode,
    PageNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

EXP = "exp_11111"
BULLET = "blt_aaaaa"
SKILL_GROUP = "sgp_ggggg"


def doc() -> StudioDoc:
    """A document complete enough for any tool to compile against."""
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", title="Engineer"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Engineer",
                company="Northwind",
                years="2021 - Present",
                bullets=[TextNode(nid=BULLET, text="Rebuilt the ledger.")],
            )
        ],
        skills=[
            SkillGroup(
                nid=SKILL_GROUP,
                key="technical",
                items=[SkillItem(nid="skl_ppppp", text="Python")],
            )
        ],
        pages=[PageNode(nid="pag_aaaaa")],
    )


#: Arguments that satisfy each tool. Not generated: a plausible call is the
#: point, and a fuzzer would mostly prove that validation rejects nonsense.
CALLS: dict[str, dict] = {
    "read_document": {},
    "find_text": {"query": "ledger"},
    # Compiles to nothing on purpose: it acts on the repository rather than on
    # the document, so the loop executes it directly the way it does a read.
    "fork_board": {"name": "Stripe - Payments"},
    # Both compile to nothing for the same reason as fork_board: they act on
    # the repository, so the loop executes them directly.
    "switch_board": {"name": "Stripe - Payments"},
    "rename_board": {"name": "Stripe - Backend"},
    "rewrite_text": {"nid": BULLET, "value": "Cut latency 96%."},
    "add_bullet": {"parent": EXP, "value": "Shipped the thing."},
    "remove_bullet": {"nid": BULLET},
    "reorder_bullets": {"parent": EXP, "order": [BULLET]},
    "set_bullet_style": {"nid": BULLET, "style": "plain"},
    "move_entry": {"nid": EXP, "index": 0, "section": "experience"},
    "set_section": {"key": "experience", "label": "Work"},
    "add_page": {},
    "arrange": {"preset": "align_left", "nids": ["frm_a", "frm_b"]},
    "add_skill": {"skill": "Rust", "group": "technical", "evidence": "user_request"},
    "remove_skill": {"skill": "Python"},
    "set_entry_field": {"nid": EXP, "field": "years", "value": "2020 - 2024"},
    "remove_element": {"nid": "frm_a"},
    "set_personal_info": {"field": "name", "value": "Rao Muhammad Hamza"},
    "set_entry_identity": {"nid": EXP, "field": "company", "value": "Geo TV"},
    "add_experience": {"title": "Analyst", "company": "Geo TV", "years": "2025"},
    "remove_entry": {"nid": EXP, "reason": "asked"},
    "remove_page": {"page": 1},
    "add_education": {"institution": "NUST", "degree": "BS Computer Science"},
    "add_project": {"name": "Ledger rebuild"},
    "add_skill_group": {"label": "Languages", "skills": ["Urdu"]},
    "add_section": {"label": "Certifications", "items": ["AWS SA"]},
    "add_shape": {"shape": "line", "where": "under_the_name"},
    "add_image": {"asset": "a" * 64, "where": "top_right"},
    "set_photo": {"asset": "a" * 64},
    "add_text_box": {"value": "Made with ResumeWesume"},
    "set_element_style": {"nid": "frm_a", "align": "right"},
}


def every_tool() -> list[ToolSpec]:
    return REGISTRY.for_tiers({"R", "A", "B", "C"})


def test_the_sample_calls_cover_the_registry() -> None:
    """A tool added without a sample call here would go untested silently."""
    assert {tool.name for tool in every_tool()} == set(CALLS)


@pytest.mark.parametrize("tool", every_tool(), ids=lambda tool: tool.name)
class TestTheWholeContract:
    def _args(self, tool: ToolSpec):
        return tool.Args(**CALLS[tool.name])

    def test_grants_can_be_minted(self, tool: ToolSpec) -> None:
        """The half that was never called.

        `grants` names a `GrantScope` member, and a wrong name is an
        `AttributeError` at call time rather than at import -- so it survives
        every test that only compiles.
        """
        grants = tool.grants(self._args(tool), doc())

        assert isinstance(grants, list)
        for grant in grants:
            # A real member, not a string that looks like one.
            assert grant.scope.name
            assert isinstance(grant.ref, str)

    def test_it_has_a_label_for_the_ui(self, tool: ToolSpec) -> None:
        label = tool.label(self._args(tool))

        assert isinstance(label, str) and label.strip()

    def test_it_compiles_or_says_why_not(self, tool: ToolSpec) -> None:
        try:
            ops = tool.compile(self._args(tool), doc())
        except ToolError:
            # A refusal in the model's own terms is a valid outcome; a crash
            # is not, and anything other than ToolError propagates here.
            return

        assert isinstance(ops, list)

    def test_its_schema_is_well_formed(self, tool: ToolSpec) -> None:
        schema = tool.json_schema()

        assert schema["function"]["name"] == tool.name
        assert schema["function"]["description"].strip()
        assert schema["function"]["parameters"]["type"] == "object"
