"""The canonical resume document.

Differences from Resume-Matcher's ``ResumeData`` that are load-bearing, not
cosmetic:

**Bullets are nodes, not strings.** The old schema paired ``description:
list[str]`` with a positional ``descriptionStyles: list[str]``. Index ``i`` of
one described index ``i`` of the other, and any code that filtered, reordered or
appended to one had to do the identical thing to the other. A desync did not
raise; it silently shifted every later bullet's marker onto its neighbour, and
three separate layers had to defend the invariant. Here ``style`` lives on the
bullet, so there is no parallel array and the desync is unrepresentable.

**Everything addressable has a stable id.** See ``nodes.py``.

**Skills carry provenance.** ``SkillItem.source`` records *why* a skill is in
the document, which is what lets a guard tell "the user asked for this" apart
from "the model invented it" after the fact.

Coercion validators are deliberately forgiving in ``mode="before"``: they run on
LLM output, where a string arriving where a list belongs is routine. Being
strict there converts a recoverable shape problem into a failed turn.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from studio.doc.nodes import NodeId

SectionKey = Literal[
    "summary", "experience", "education", "projects", "skills", "custom"
]

SkillSource = Literal["original", "jd", "resume", "user"]
BulletStyle = Literal["bullet", "plain"]


def _as_text(value: Any) -> str:
    """Coerce anything scalar-ish to a stripped string.

    Guards the real failure seen in the old app: a model returning a number for
    a text field, which then blows up on ``.strip()`` several layers away.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


class TextNode(BaseModel):
    """One bullet, or the summary paragraph. Style rides on the node."""

    nid: NodeId
    text: str = ""
    style: BulletStyle = "bullet"

    @field_validator("text", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> str:
        return _as_text(value)

    @field_validator("style", mode="before")
    @classmethod
    def _coerce_style(cls, value: Any) -> str:
        # Anything unrecognised means "ordinary bullet" — the common case.
        return "plain" if value == "plain" else "bullet"


class PersonalInfo(BaseModel):
    """Identity. Never editable except through a consent-gated tool."""

    name: str = ""
    title: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    website: str | None = None
    linkedin: str | None = None
    github: str | None = None

    @field_validator("name", "title", "email", "phone", "location", mode="before")
    @classmethod
    def _coerce_required(cls, value: Any) -> str:
        return _as_text(value)


class ExperienceNode(BaseModel):
    nid: NodeId
    title: str = ""
    company: str = ""
    location: str | None = None
    # Kept verbatim from the source. Never reformatted: month precision is
    # routinely dropped by models and is expensive to recover afterwards.
    years: str = ""
    bullets: list[TextNode] = Field(default_factory=list)

    @field_validator("title", "company", "years", mode="before")
    @classmethod
    def _coerce(cls, value: Any) -> str:
        return _as_text(value)


class EducationNode(BaseModel):
    nid: NodeId
    institution: str = ""
    degree: str = ""
    years: str = ""
    # Scalar in the source schema, so it stays scalar here; a single node keeps
    # it addressable without inventing a list that has never had two entries.
    detail: TextNode | None = None

    @field_validator("institution", "degree", "years", mode="before")
    @classmethod
    def _coerce(cls, value: Any) -> str:
        return _as_text(value)


class ProjectNode(BaseModel):
    nid: NodeId
    name: str = ""
    role: str = ""
    years: str = ""
    github: str | None = None
    website: str | None = None
    bullets: list[TextNode] = Field(default_factory=list)

    @field_validator("name", "role", "years", mode="before")
    @classmethod
    def _coerce(cls, value: Any) -> str:
        return _as_text(value)


class SkillItem(BaseModel):
    """One skill, certification, language or award.

    ``source`` is provenance, not decoration: ``SkillsGuard`` uses it to decide
    whether an item that appeared this turn was requested or invented.
    """

    nid: NodeId
    text: str = ""
    source: SkillSource = "original"

    @field_validator("text", mode="before")
    @classmethod
    def _coerce(cls, value: Any) -> str:
        return _as_text(value)


class SkillGroup(BaseModel):
    nid: NodeId
    key: str = "technical"
    label: str = "Technical Skills"
    items: list[SkillItem] = Field(default_factory=list)


class CustomItemNode(BaseModel):
    nid: NodeId
    title: str = ""
    subtitle: str | None = None
    location: str | None = None
    years: str = ""
    bullets: list[TextNode] = Field(default_factory=list)


class CustomSectionNode(BaseModel):
    nid: NodeId
    key: str
    label: str = ""
    kind: Literal["text", "itemList", "stringList"] = "itemList"
    text: TextNode | None = None
    items: list[CustomItemNode] = Field(default_factory=list)
    strings: list[SkillItem] = Field(default_factory=list)


class SectionMeta(BaseModel):
    """Ordering and visibility. Ported wholesale — it already works."""

    key: str
    label: str = ""
    visible: bool = True
    order: int = 0


class StudioDoc(BaseModel):
    """The whole resume. Every mutation in the system produces one of these."""

    schema_version: Literal[1] = 1
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    summary: TextNode | None = None
    experience: list[ExperienceNode] = Field(default_factory=list)
    education: list[EducationNode] = Field(default_factory=list)
    projects: list[ProjectNode] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    custom: list[CustomSectionNode] = Field(default_factory=list)
    sections: list[SectionMeta] = Field(default_factory=list)


DEFAULT_SECTIONS: list[SectionMeta] = [
    SectionMeta(key="summary", label="Summary", order=0),
    SectionMeta(key="experience", label="Experience", order=1),
    SectionMeta(key="education", label="Education", order=2),
    SectionMeta(key="projects", label="Projects", order=3),
    SectionMeta(key="skills", label="Skills", order=4),
]

AnyNode = Annotated[
    TextNode
    | ExperienceNode
    | EducationNode
    | ProjectNode
    | SkillItem
    | SkillGroup
    | CustomItemNode
    | CustomSectionNode,
    Field(union_mode="left_to_right"),
]
