"""A field added to the schema cannot hide from the assistant.

The bug this guards against was never one bug. Five places each enumerated the
document by hand -- the outline, the search inventory, the full read, each
tool's ``Literal``, and the tier table -- so a field landed in whichever of
them somebody remembered, and ``role`` landed in none: unsearchable, missing
from both views, and refused by the only tool that could have changed it. Every
gap found since has been the same shape one field over.

The views are derived now, and this is the check that they stay derived. It
enumerates nothing itself: the models come from ``StudioDoc``'s annotations and
the fields from ``prose_fields``, so adding either to the schema adds it here.
A field the assistant cannot see fails by name -- and a whole model nobody
wired up fails by name too, which is the case a test listing its own fields
could never have caught.
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

from studio.agent.context import find, full_section, walk
from studio.doc.apply import OpContext, apply_ops
from studio.agent.tools import REGISTRY
from studio.doc import schema as S
from studio.doc.apply import ENTRY_MODELS, ENTRY_SOFT_FIELDS, IDENTITY_FIELDS
from studio.doc.schema import prose_fields

#: Geometry, not writing: a page is addressed by `set_geometry` and `arrange`,
#: and `walk` skips it for the same reason.
NOT_CONTENT = {"pages"}

#: Every name `read_document` answers to, so "read it in full" is checked over
#: the whole résumé rather than the parts somebody thought of.
SECTIONS = (
    "personal", "summary", "experience", "education", "projects",
    "skills", "certifications", "blocks", "sections",
)


def models_in(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    if get_origin(annotation) in (list, Union, UnionType):
        return [found for arg in get_args(annotation) for found in models_in(arg)]
    return []


def reachable(model: type[BaseModel], seen: set[type] | None = None) -> set[type]:
    """Every model the document can hold, from the annotations alone."""
    seen = set() if seen is None else seen
    if model in seen:
        return seen
    seen.add(model)
    for name, info in model.model_fields.items():
        if model is S.StudioDoc and name in NOT_CONTENT:
            continue
        for part in models_in(info.annotation):
            reachable(part, seen)
    return seen


def every_node(node: Any, found: list[Any] | None = None) -> list[Any]:
    found = [] if found is None else found
    found.append(node)
    for name in type(node).model_fields:
        if isinstance(node, S.StudioDoc) and name in NOT_CONTENT:
            continue
        value = getattr(node, name)
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, BaseModel):
                every_node(child, found)
    return found


def a_resume() -> S.StudioDoc:
    """One of every node the document can hold.

    Deliberately structural: the *shape* is written out here, the *fields* are
    not. Every piece of writing is stamped in by ``stamped`` from
    ``prose_fields``, so a field added to a model is covered without this file
    changing.
    """
    return S.StudioDoc(
        personal=S.PersonalInfo(),
        summary=S.TextNode(nid="sum_aaaaa"),
        experience=[
            S.ExperienceNode(nid="exp_aaaaa", bullets=[S.TextNode(nid="blt_aaaaa")])
        ],
        education=[
            S.EducationNode(nid="edu_aaaaa", detail=S.TextNode(nid="blt_bbbbb"))
        ],
        projects=[
            S.ProjectNode(nid="prj_aaaaa", bullets=[S.TextNode(nid="blt_ccccc")])
        ],
        skills=[S.SkillGroup(nid="sgp_aaaaa", items=[S.SkillItem(nid="skl_aaaaa")])],
        custom=[
            S.CustomSectionNode(
                nid="cst_aaaaa",
                key="certifications",
                text=S.TextNode(nid="sum_bbbbb"),
                items=[
                    S.CustomItemNode(
                        nid="cit_aaaaa", bullets=[S.TextNode(nid="blt_ddddd")]
                    )
                ],
                strings=[S.SkillItem(nid="skl_bbbbb")],
            )
        ],
        sections=[S.SectionMeta(key="certifications", order=0)],
        blocks=[S.TextBlockNode(nid="txb_aaaaa", lines=[S.TextNode(nid="blt_eeeee")])],
    )


def stamped() -> tuple[S.StudioDoc, dict[str, str]]:
    """The résumé with a unique word in every field, and where each one went."""
    doc = a_resume()
    marks: dict[str, str] = {}
    for node in every_node(doc):
        for field in prose_fields(type(node)):
            word = f"Zq{len(marks):03d}zQ"
            marks[word] = f"{type(node).__name__}.{field}"
            setattr(node, field, word)
    return doc, marks


class TestTheFixtureIsNotStale:
    def test_it_holds_one_of_every_model_the_document_can_hold(self) -> None:
        # The assertion a test listing its own fields could never make. Add
        # `awards: list[AwardNode]` to StudioDoc and this names AwardNode --
        # before anybody discovers the section is invisible by using it.
        present = {type(node).__name__ for node in every_node(a_resume())}
        expected = {
            model.__name__
            for model in reachable(S.StudioDoc)
            if model is not S.StudioDoc and prose_fields(model)
        }

        assert expected - present == set(), (
            "the document can hold models this fixture does not: "
            f"{sorted(expected - present)}"
        )


class TestEverythingWrittenIsVisible:
    def test_every_field_is_in_the_inventory(self) -> None:
        doc, marks = stamped()
        written = " ".join(item.text for item in walk(doc))

        missing = sorted(name for word, name in marks.items() if word not in written)
        assert missing == [], f"not in the inventory: {missing}"

    def test_every_field_can_be_searched_for(self) -> None:
        doc, marks = stamped()

        missing = sorted(name for word, name in marks.items() if not find(doc, word))
        assert missing == [], f"on the page and unsearchable: {missing}"

    def test_every_field_can_be_read_back_in_full(self) -> None:
        doc, marks = stamped()
        everything = " ".join(full_section(doc, name) for name in SECTIONS)

        missing = sorted(name for word, name in marks.items() if word not in everything)
        assert missing == [], f"cannot be read back in full: {missing}"

    def test_a_search_returns_something_the_assistant_can_act_on(self) -> None:
        # A heading and the header have no node id, so a hit on one has to come
        # back as the path the tool takes -- `section.<key>`, `personal` --
        # rather than as a name that addresses nothing.
        doc, _ = stamped()
        doc.sections[0].label = "Accreditations"
        doc.personal.name = "Alex Morgan"

        assert find(doc, "Accreditations")[0]["nid"] == "section.certifications"
        assert find(doc, "Alex Morgan")[0]["nid"] == "personal"


#: How each tool that writes text is called, for the probe below. The point is
#: not which tool wins -- it is that *some* tool does, for every field.
RENAMED = "Xyzzy"
PLANS = (
    ("rewrite_text", lambda nid, field: {"nid": nid, "value": RENAMED}),
    ("set_entry_field", lambda nid, field: {"nid": nid, "field": field, "value": RENAMED}),
    ("set_entry_identity", lambda nid, field: {"nid": nid, "field": field, "value": RENAMED}),
    ("set_personal_info", lambda nid, field: {"field": field, "value": RENAMED}),
    ("set_section", lambda nid, field: {"key": nid.split(".", 1)[-1], "label": RENAMED}),
)


def changes_it(nid: str, field: str, tool: str, args: dict[str, Any]) -> bool:
    """Whether that call actually leaves the new value on that field.

    Verified by reading the value back, not by the absence of a rejection. An
    argument a tool does not declare is dropped rather than refused, so a call
    that changed nothing at all looked like success -- and briefly convinced me
    that renaming a heading worked, when no tool had ever reached it.
    """
    doc, _ = stamped()
    try:
        spec = REGISTRY.get(tool)
        ops = spec.compile(spec.Args(**args), doc)
    except Exception:
        return False
    after, _, rejected = apply_ops(doc, ops, OpContext(granted_tiers={"A", "B", "C"}))
    if rejected:
        return False
    return any(
        item.nid == nid and item.fields.get(field) == RENAMED for item in walk(after)
    )


def targets() -> list[tuple[str, str]]:
    """Every field with something in it -- the stamped résumé, so that is all
    of them rather than only the ones carrying a schema default."""
    doc, _ = stamped()
    return [(item.nid, field) for item in walk(doc) for field in item.fields]


class TestEverythingWrittenIsChangeable:
    @pytest.mark.parametrize(
        "nid,field", targets(), ids=lambda value: str(value)
    )
    def test_some_tool_changes_it(self, nid: str, field: str) -> None:
        # The question this file exists to keep answered: is everything on the
        # résumé something the assistant can edit? It was 19 of 29 when it was
        # first asked, and the ten included every heading on the page -- the
        # engine has taken `section.<key>` since it was written, and no tool
        # ever called it.
        assert any(
            changes_it(nid, field, tool, build(nid, field)) for tool, build in PLANS
        ), f"{nid}.{field} is on the page and no tool can change it"


    def test_the_tool_offers_exactly_what_the_engine_allows(self) -> None:
        # The Founder bug in one line: the tool's Literal said three fields and
        # the engine's soft set said six, so the assistant was refused a change
        # the engine would have taken, and sent to a tool that does not accept
        # it either. They are one definition now.
        schema = REGISTRY.get("set_entry_field").json_schema()
        offered = set(schema["function"]["parameters"]["properties"]["field"]["enum"])

        assert offered == set(ENTRY_SOFT_FIELDS)

    @pytest.mark.parametrize("model", ENTRY_MODELS, ids=lambda model: model.__name__)
    def test_every_entry_field_is_soft_or_an_identity_claim(
        self, model: type[BaseModel]
    ) -> None:
        # No third category, so nothing can fall between the two the way a
        # project's links did: on the page, findable, and reachable by nothing.
        unclassified = set(prose_fields(model)) - ENTRY_SOFT_FIELDS - IDENTITY_FIELDS

        assert unclassified == set()


class TestTheOutlineSaysWhatThePageSays:
    def test_a_renamed_heading_is_the_one_the_outline_uses(self) -> None:
        # Not a missing field but a wrong statement: the outline printed
        # "EXPERIENCE:" whatever the heading said, so `set_section` could
        # rename a heading the assistant then described under the old name.
        from studio.agent.context import outline

        doc = a_resume()
        doc.sections = [S.SectionMeta(key="experience", label="Selected Work", order=0)]

        text = outline(doc)

        assert "SELECTED WORK" in text
        assert "EXPERIENCE:" not in text
        # And still addressable: the key is what `set_section` takes.
        assert "section key: experience" in text


class TestASectionThatIsEmptyStillExists:
    def test_the_outline_names_it(self) -> None:
        # A section with nothing in it printed no line, so it did not exist as
        # far as the assistant could see. Asked to add Projects to a résumé
        # whose Projects section was empty rather than absent, the only tool it
        # could reach for was `add_section` -- refused, correctly, with
        # "'Projects' is already a section of this resume", and the wasted call
        # was on camera in a demo.
        from studio.agent.context import outline
        from studio.doc.schema import DEFAULT_SECTIONS

        doc = S.StudioDoc(
            personal=S.PersonalInfo(name="Alex Morgan"),
            summary=S.TextNode(nid="sum_aaaaa", text="Backend engineer."),
            sections=list(DEFAULT_SECTIONS),
        )

        text = outline(doc)

        assert "EMPTY SECTIONS" in text
        assert "projects" in text.split("EMPTY SECTIONS")[1]

    def test_a_section_with_something_in_it_is_not_listed_as_empty(self) -> None:
        from studio.agent.context import outline

        doc, _ = stamped()

        empty = set()
        for line in outline(doc).splitlines():
            if line.startswith("EMPTY SECTIONS"):
                empty = {part.strip() for part in line.split(":", 1)[1].split(",")}

        assert empty == set(), f"listed as empty while holding writing: {empty}"
