"""The TypeScript contract still describes the Python models.

The schema is written in two languages and drift between them is data loss in a
document editor, so something has to check. That used to be
``scripts/gen_contracts.py``, which claimed to *derive* the TypeScript from the
Pydantic models and did not: ``build()`` returned a hand-typed copy of
``doc.ts``, wrote it over the real file, and CI failed on the diff. So the
schema was maintained twice, in two languages, with a script in between saying
otherwise -- and the copy inside the script always won.

It cost exactly what that costs. ``doc.ts`` carries ``GENERATED FILE -- DO NOT
EDIT``, which points a reader away from the only file anybody would think to
edit; a field added there by hand survived until the next ``make contracts``
and then vanished, with CI reporting a dirty tree rather than the reason.

What was worth keeping is this: the assertion that no model has grown a field
the contract does not mention. It is now a test over the real ``doc.ts``, which
is the file, hand-written like every other file. A field added to a model with
no TypeScript counterpart fails here, by name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from studio.doc.schema import (
    CustomItemNode,
    CustomSectionNode,
    EducationNode,
    ElementStyle,
    ExperienceNode,
    FrameElement,
    ImageElement,
    PageNode,
    PersonalInfo,
    ProjectNode,
    Rect,
    SectionMeta,
    ShapeElement,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextBlockNode,
    TextNode,
)
from studio.routers.canvases import CanvasResponse
from studio.routers.documents import DocumentResponse

CONTRACT = (
    Path(__file__).resolve().parents[4] / "apps" / "web" / "src" / "contracts" / "doc.ts"
)

#: Every model the browser receives. The old check covered ten of these and
#: missed the nested content models entirely -- which is how ``display`` on a
#: skills group, and ``pinned`` on a frame before it, reached the browser
#: without a type. If the server sends it, it belongs here.
MIRRORED: tuple[type[BaseModel], ...] = (
    StudioDoc,
    PersonalInfo,
    TextNode,
    ExperienceNode,
    EducationNode,
    ProjectNode,
    SkillItem,
    SkillGroup,
    CustomItemNode,
    CustomSectionNode,
    SectionMeta,
    Rect,
    ElementStyle,
    FrameElement,
    ImageElement,
    ShapeElement,
    PageNode,
    TextBlockNode,
    DocumentResponse,
    CanvasResponse,
)


def declared(source: str, name: str) -> set[str]:
    """Property names in one ``export interface`` of the contract."""
    block = re.search(rf"export interface {name}\b[^{{]*{{(.*?)\n}}", source, re.S)
    if block is None:
        pytest.fail(f"No TypeScript interface for {name} in {CONTRACT.name}")
    return set(re.findall(r"^\s*(\w+)\??:", block.group(1), re.M))


@pytest.mark.parametrize("model", MIRRORED, ids=lambda model: model.__name__)
def test_every_field_reaches_the_browser(model: type[BaseModel]) -> None:
    missing = set(model.model_fields) - declared(CONTRACT.read_text("utf-8"), model.__name__)

    assert not missing, (
        f"{model.__name__} has fields the TypeScript contract does not cover: "
        f"{sorted(missing)}. Add them to {CONTRACT}."
    )
