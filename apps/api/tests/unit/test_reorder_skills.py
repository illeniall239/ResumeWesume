"""Reordering a skills group is one call, not thirty.

Asked to put the AI and GenAI skills first, the assistant had no way to say
"these, in this order": there was no reorder tool for skills, only add and
remove. So it demolished the list and rebuilt it -- fifteen `remove_skill`
calls followed by fifteen `add_skill` calls, for a permutation.

That is slow and ugly to watch, and it is not only cosmetic:

  * every skill's `source` is reset from `resume` to whatever the re-add
    claims, and `source` is the field the grounding notice reads to decide
    which lines nobody has vouched for;
  * the list passes through empty, so a turn that fails halfway leaves the
    section gone;
  * the add path is capped at fourteen per group, so a rebuild of a longer
    list stalls partway with the skills already removed.

The capability was always there -- `Reorder` accepts a skill group as its
parent, and `salvage.py` already repairs `reorder_skills` arguments by name.
Only the tool was missing.
"""

from __future__ import annotations

import pytest

from studio.agent.tools import ToolError, _default_specs
from studio.doc.apply import OpContext, apply_ops
from studio.doc.autolayout import layout
from studio.doc.schema import (
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

AUTHORIZED = OpContext(granted_tiers={"A", "B", "C"})

SKILLS = ["Python", "SQL", "RAG", "LangChain", "YOLO"]


def tool(name: str):
    return next(spec for spec in _default_specs() if spec.name == name)


def a_resume() -> StudioDoc:
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        summary=TextNode(nid="sum_aaaaa", text="Ships things."),
        skills=[
            SkillGroup(
                nid="sgp_aaaaa",
                key="technical",
                items=[
                    SkillItem(nid=f"skl_{index:05d}", text=text, source="resume")
                    for index, text in enumerate(SKILLS)
                ],
            )
        ],
    )
    doc.pages = layout(doc)
    return doc


def texts(doc: StudioDoc) -> list[str]:
    return [item.text for item in doc.skills[0].items]


def run(doc: StudioDoc, **args):
    spec = tool("reorder_skills")
    ops = spec.compile(spec.Args(**args), doc)
    return ops, apply_ops(doc, ops, AUTHORIZED)


class TestReorderingSkills:
    def test_the_tool_exists(self) -> None:
        # It was referenced by name in the salvage path long before it existed,
        # so the model was being repaired for a call it could never make.
        assert "reorder_skills" in {spec.name for spec in _default_specs()}

    def test_one_op_for_the_whole_permutation(self) -> None:
        doc = a_resume()

        ops, (after, _, rejected) = run(
            doc, order=["RAG", "LangChain", "YOLO", "Python", "SQL"]
        )

        assert rejected == []
        assert len(ops) == 1, "a permutation is one op, not fifteen removes and adds"
        assert texts(after) == ["RAG", "LangChain", "YOLO", "Python", "SQL"]

    def test_it_takes_ids_as_well_as_words(self) -> None:
        doc = a_resume()
        by_text = {item.text: item.nid for item in doc.skills[0].items}

        _, (after, _, rejected) = run(
            doc, order=[by_text["YOLO"], "RAG", by_text["SQL"], "Python", "LangChain"]
        )

        assert rejected == []
        assert texts(after) == ["YOLO", "RAG", "SQL", "Python", "LangChain"]

    def test_provenance_survives(self) -> None:
        # The reason this matters beyond tidiness. Rebuilding the list re-added
        # every skill with whatever `source` the add claimed, and `source` is
        # what tells a reader which lines nobody vouched for. Moving them keeps
        # the resume's own word for it.
        doc = a_resume()

        _, (after, _, _) = run(doc, order=["RAG", "LangChain", "YOLO", "Python", "SQL"])

        assert {item.source for item in after.skills[0].items} == {"resume"}

    def test_it_never_passes_through_empty(self) -> None:
        # A rebuild removes everything before it adds anything back, so a turn
        # that fails in the middle leaves the section gone.
        doc = a_resume()

        _, (after, _, _) = run(doc, order=["YOLO", "RAG", "Python", "SQL", "LangChain"])

        assert len(after.skills[0].items) == len(SKILLS)

    def test_a_partial_list_keeps_the_rest(self) -> None:
        # Naming only the ones that must lead is the natural way to ask, and
        # `Reorder` already appends whatever was omitted rather than losing it.
        doc = a_resume()

        _, (after, _, rejected) = run(doc, order=["RAG", "LangChain"])

        assert rejected == []
        assert texts(after)[:2] == ["RAG", "LangChain"]
        assert sorted(texts(after)) == sorted(SKILLS)

    def test_a_skill_that_is_not_there_is_named(self) -> None:
        # Salvaged silently, the model never learns that half its list went
        # nowhere -- and a reorder that quietly ignores an entry looks to it
        # like the tool not working.
        doc = a_resume()

        with pytest.raises(ToolError) as caught:
            run(doc, order=["RAG", "Rust"])

        assert "Rust" in str(caught.value)

    def test_an_unknown_group_says_what_there_is(self) -> None:
        doc = a_resume()

        with pytest.raises(ToolError) as caught:
            run(doc, order=["RAG"], parent="languages")

        assert "technical" in str(caught.value)

    def test_it_needs_no_consent(self) -> None:
        # A permutation adds nothing, removes nothing and claims nothing, so it
        # is Tier A -- the same tier as reordering bullets.
        assert tool("reorder_skills").tier == "A"

        doc = a_resume()
        spec = tool("reorder_skills")
        ops = spec.compile(spec.Args(order=["RAG", "LangChain"]), doc)

        _, _, rejected = apply_ops(doc, ops, OpContext(granted_tiers={"A"}))

        assert rejected == []
