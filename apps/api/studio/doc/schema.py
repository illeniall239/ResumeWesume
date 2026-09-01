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

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator

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


# --------------------------------------------------------------------------
# Layout
#
# Content says what the resume says. Layout says where it sits, and lives in a
# separate subtree for one reason above all: the importer, the agent's sixteen
# tools and the ATS export all address content and must not have to know that
# pages exist. Keeping the two apart is what lets free placement be added
# without rewriting any of them.
#
# A frame does not *hold* content, it *points at* it: `ref` names a section key
# or a node id, and the frame renders that node's whole subtree minus anything
# another frame has claimed. The invariant is coverage -- every content node is
# rendered by exactly one frame -- and because coverage is over containers
# rather than leaves, a bullet added later is covered by its ancestor
# automatically. That is what keeps this from becoming another parallel
# structure that can silently desync from the content it describes.
# --------------------------------------------------------------------------


class Rect(BaseModel):
    """Position and size in points, page-relative, origin top-left.

    Points rather than pixels because the PDF is the artifact of record and
    ``page.pdf()`` reasons in inches; the browser converts once, in one place.
    """

    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0


class ElementStyle(BaseModel):
    """Presentation of one element. Never affects what the ATS export reads."""

    align: Literal["left", "center", "right"] = "left"
    font_scale: float = 1.0
    color: str | None = None
    background: str | None = None
    padding: float = 0.0
    radius: float = 0.0
    opacity: float = 1.0


class FrameElement(BaseModel):
    """A placed window onto content.

    ``autogrow="height"`` means the stored height is advisory: the browser is
    the only thing that can measure text, so it sizes the frame from its content
    and corrects ``h``. The server never estimates -- a character-metrics guess
    would disagree with Chromium, which is the whole class of bug the page
    measurement code exists to avoid.

    ``pinned`` says the frame was placed by hand and the reflow pass must leave
    it alone. Without it that pass owns every frame, so dragging a job onto page
    two survived until the next reload and was then stacked back into the
    column -- the correction undoing the user's work rather than the server's
    guess. Defaults false, so a migrated document is still the column's to
    arrange until somebody moves something.
    """

    nid: NodeId
    ref: str
    rect: Rect = Field(default_factory=Rect)
    rotation: float = 0.0
    autogrow: Literal["none", "height"] = "height"
    visible: bool = True
    locked: bool = False
    pinned: bool = False
    style: ElementStyle = Field(default_factory=ElementStyle)


class ImageElement(BaseModel):
    """A picture. Decorative: the ATS export reads only ``alt``."""

    nid: NodeId
    asset: str = ""
    rect: Rect = Field(default_factory=Rect)
    rotation: float = 0.0
    fit: Literal["cover", "contain"] = "cover"
    crop: Rect | None = None
    alt: str = ""
    visible: bool = True
    locked: bool = False
    style: ElementStyle = Field(default_factory=ElementStyle)


class ShapeElement(BaseModel):
    """A rectangle, ellipse or line. Contributes nothing to any text export."""

    nid: NodeId
    shape: Literal["rect", "ellipse", "line"] = "rect"
    rect: Rect = Field(default_factory=Rect)
    rotation: float = 0.0
    fill: str | None = None
    stroke: str | None = None
    stroke_width: float = 0.0
    visible: bool = True
    locked: bool = False


def _element_kind(value: Any) -> str:
    """Which element model a payload is, read from its id prefix.

    The prefix *is* the kind (see ``nodes.py``), so discriminating on it costs
    nothing and is exact. Matching on field shape instead is not: every field
    of ``ImageElement`` has a default, so a shape payload validates cleanly as
    an image and silently loses its ``shape`` and ``fill`` on the way through.
    """
    nid = value.get("nid") if isinstance(value, dict) else getattr(value, "nid", "")
    prefix = str(nid or "").split("_", 1)[0]
    return prefix if prefix in {"frm", "img", "shp"} else "frm"


AnyElement = Annotated[
    Union[
        Annotated[FrameElement, Tag("frm")],
        Annotated[ImageElement, Tag("img")],
        Annotated[ShapeElement, Tag("shp")],
    ],
    Discriminator(_element_kind),
]


class PageNode(BaseModel):
    """One sheet.

    **Z-order is list order**: the last element paints on top. An integer `z`
    would need normalising, tie-breaking and an invariant of its own; list order
    needs none of those, and makes "bring to front" an ordinary ``reorder`` and
    "move behind the photo on page 2" an ordinary ``move_node``.
    """

    nid: NodeId
    size: Literal["A4", "Letter"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    background: str | None = None
    elements: list[AnyElement] = Field(default_factory=list)


class TextBlockNode(BaseModel):
    """Free text belonging to no section.

    Still a node with an id, so the assistant can read and rewrite it. Text that
    the agent cannot address is text the product cannot help with.
    """

    nid: NodeId
    role: Literal["heading", "body", "caption", "contact", "none"] = "body"
    lines: list[TextNode] = Field(default_factory=list)


class StudioDoc(BaseModel):
    """The whole resume. Every mutation in the system produces one of these."""

    schema_version: Literal[1, 2] = 2
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    summary: TextNode | None = None
    experience: list[ExperienceNode] = Field(default_factory=list)
    education: list[EducationNode] = Field(default_factory=list)
    projects: list[ProjectNode] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    custom: list[CustomSectionNode] = Field(default_factory=list)
    sections: list[SectionMeta] = Field(default_factory=list)

    # Layout. An empty ``pages`` means "lay this out as one flowing column",
    # which is what every v1 document and every fresh import is until it is
    # migrated, and what the ATS export always renders regardless.
    blocks: list[TextBlockNode] = Field(default_factory=list)
    pages: list[PageNode] = Field(default_factory=list)
    # Overrides the derived reading order for the designed PDF. Only set when
    # the user has said the automatic order is wrong.
    reading_order: list[NodeId] | None = None


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
