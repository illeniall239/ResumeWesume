"""The tool set.

Each tool is one narrow capability with its own argument model. That is a
deliberate choice over a single generic ``apply_edit(path, action, value)``:
a model in the 75%-reliability band does markedly better with
``remove_skill{skill: "Python"}`` than with a call where it must also synthesise
a path DSL and pick a correct action verb. Narrow tools move work from the
model, which is unreliable, into the schema, which is not.

A tool declares four things and nothing else:

* its **tier**, which decides whether it needs consent;
* its **arguments**, as a Pydantic model that becomes its JSON Schema;
* how to **compile** to primitive ops;
* what **grants** to mint, so the drift guards know it was requested.

Grants come from validated arguments, never from the user's prose, because
prose is exactly what an injected instruction imitates.
"""

from __future__ import annotations

from typing import Any, Callable, ClassVar, Literal

from pydantic import BaseModel, Field

from studio.doc.nodes import NodeKind, mint
from studio.doc.ops import (
    DocOp,
    InsertNode,
    MoveNode,
    RemoveNode,
    Reorder,
    SetField,
    SetSection,
    SetStyle,
    SetText,
)
from studio.doc.schema import StudioDoc
from studio.guards.grants import GrantScope, IntentGrant, normalise_key

Tier = Literal["R", "A", "B", "C"]


class ToolError(Exception):
    """The arguments cannot be compiled into ops."""

    def __init__(self, message: str, *, code: str = "invalid_args") -> None:
        super().__init__(message)
        self.code = code


class ToolSpec:
    """Base class. Subclasses declare the four things above."""

    name: ClassVar[str]
    tier: ClassVar[Tier] = "A"
    description: ClassVar[str] = ""
    Args: ClassVar[type[BaseModel]]

    def compile(self, args: BaseModel, doc: StudioDoc) -> list[DocOp]:
        raise NotImplementedError

    def grants(self, args: BaseModel, doc: StudioDoc) -> list[IntentGrant]:
        return []

    def label(self, args: BaseModel) -> str:
        """Short human phrase for the UI, e.g. "rewrote a bullet"."""
        return self.name.replace("_", " ")

    # --- schema ---------------------------------------------------------

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        schema = cls.Args.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description.strip(),
                "parameters": schema,
            },
        }


# --- Tier R: read-only ------------------------------------------------------
# The highest-leverage tools for a weak model. An outline costs roughly a tenth
# of the full document in tokens, and a fuzzy search means the model never has
# to guess an address.


class ReadDocumentArgs(BaseModel):
    section: str | None = Field(
        default=None,
        description="Limit to one section: experience, education, projects, skills, summary.",
    )
    detail: Literal["outline", "full"] = "outline"


class ReadDocument(ToolSpec):
    name = "read_document"
    tier = "R"
    description = """
    Read the resume. Returns node ids alongside the text, which you need before
    editing anything. Use detail="outline" unless you need the full text of a
    section.
    """
    Args = ReadDocumentArgs

    def compile(self, args: BaseModel, doc: StudioDoc) -> list[DocOp]:
        return []


class FindTextArgs(BaseModel):
    query: str = Field(description="Words to look for, e.g. 'the AWS bullet'.")
    limit: int = Field(default=5, ge=1, le=20)


class FindText(ToolSpec):
    name = "find_text"
    tier = "R"
    description = """
    Find nodes whose text matches a query. Use this instead of guessing a node
    id when the user refers to something by description.
    """
    Args = FindTextArgs

    def compile(self, args: BaseModel, doc: StudioDoc) -> list[DocOp]:
        return []


# --- Tier A: prose ----------------------------------------------------------


class RewriteTextArgs(BaseModel):
    nid: str = Field(description="Node id, e.g. blt_9c21x or sum_00001.")
    value: str = Field(description="The replacement text.")
    expect: str | None = Field(
        default=None,
        description="The current text, to guard against editing a stale value.",
    )
    reason: str = ""


class RewriteText(ToolSpec):
    name = "rewrite_text"
    tier = "A"
    description = """
    Replace the text of a bullet, the summary, or a skill. Pass `expect` with
    the current text so a stale edit is caught rather than silently applied.
    """
    Args = RewriteTextArgs

    def compile(self, args: RewriteTextArgs, doc: StudioDoc) -> list[DocOp]:
        return [
            SetText(
                nid=args.nid, value=args.value, expect=args.expect, reason=args.reason
            )
        ]

    def grants(self, args: RewriteTextArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.TEXT, args.nid)]

    def label(self, args: RewriteTextArgs) -> str:
        return "rewrote text"


class AddBulletArgs(BaseModel):
    parent: str = Field(description="Id of the experience or project to add to.")
    value: str = Field(description="The bullet text.")
    position: int = Field(default=-1, description="-1 appends.")
    reason: str = ""


class AddBullet(ToolSpec):
    name = "add_bullet"
    tier = "A"
    description = "Add a bullet to an experience entry or project."
    Args = AddBulletArgs

    def compile(self, args: AddBulletArgs, doc: StudioDoc) -> list[DocOp]:
        return [
            InsertNode(
                parent=args.parent,
                index=args.position,
                node={"nid": mint(NodeKind.BULLET), "text": args.value},
                reason=args.reason,
            )
        ]

    def label(self, args: AddBulletArgs) -> str:
        return "added a bullet"


class RemoveBulletArgs(BaseModel):
    nid: str
    expect: str | None = Field(
        default=None, description="The bullet's current text, to confirm the target."
    )
    reason: str = ""


class RemoveBullet(ToolSpec):
    name = "remove_bullet"
    tier = "A"
    description = """
    Delete a bullet. Pass `expect` with its current text so you cannot delete
    the wrong one if positions have shifted.
    """
    Args = RemoveBulletArgs

    def compile(self, args: RemoveBulletArgs, doc: StudioDoc) -> list[DocOp]:
        return [RemoveNode(nid=args.nid, expect=args.expect, reason=args.reason)]

    def label(self, args: RemoveBulletArgs) -> str:
        return "removed a bullet"


class ReorderBulletsArgs(BaseModel):
    parent: str
    order: list[str] = Field(description="All child node ids, in the new order.")
    reason: str = ""


class ReorderBullets(ToolSpec):
    name = "reorder_bullets"
    tier = "A"
    description = "Reorder the bullets of an entry. List every id."
    Args = ReorderBulletsArgs

    def compile(self, args: ReorderBulletsArgs, doc: StudioDoc) -> list[DocOp]:
        return [Reorder(parent=args.parent, order=args.order, reason=args.reason)]

    def label(self, args: ReorderBulletsArgs) -> str:
        return "reordered bullets"


class SetBulletStyleArgs(BaseModel):
    nid: str
    style: Literal["bullet", "plain"]
    reason: str = ""


class SetBulletStyle(ToolSpec):
    name = "set_bullet_style"
    tier = "A"
    description = "Show a line as a bullet, or as a plain paragraph."
    Args = SetBulletStyleArgs

    def compile(self, args: SetBulletStyleArgs, doc: StudioDoc) -> list[DocOp]:
        return [SetStyle(nid=args.nid, style=args.style, reason=args.reason)]


# --- Tier B: claims ---------------------------------------------------------


class AddSkillArgs(BaseModel):
    skill: str
    group: str = Field(
        default="technical", description="technical, languages, certifications, awards."
    )
    evidence: Literal["jd", "resume", "user_request"] = Field(
        description=(
            "Why this skill belongs: it is in the job description, it already "
            "appears in the resume text, or the user asked for it."
        )
    )
    reason: str = ""


class AddSkill(ToolSpec):
    name = "add_skill"
    tier = "B"
    description = """
    Add a skill. You must say where the evidence comes from. Never add a skill
    the user has not demonstrated or asked for.
    """
    Args = AddSkillArgs

    def compile(self, args: AddSkillArgs, doc: StudioDoc) -> list[DocOp]:
        group = next((item for item in doc.skills if item.key == args.group), None)
        if group is None:
            raise ToolError(
                f"No skill group {args.group!r}. Existing groups: "
                + ", ".join(item.key for item in doc.skills)
            )
        source = {"jd": "jd", "resume": "resume", "user_request": "user"}[args.evidence]
        return [
            InsertNode(
                parent=group.nid,
                index=-1,
                node={"nid": mint(NodeKind.SKILL), "text": args.skill, "source": source},
                reason=args.reason,
            )
        ]

    def grants(self, args: AddSkillArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.SKILL_ADD, normalise_key(args.skill))]

    def label(self, args: AddSkillArgs) -> str:
        return f"added {args.skill}"


class RemoveSkillArgs(BaseModel):
    skill: str = Field(description="The skill text, or its node id.")
    reason: str = ""


class RemoveSkill(ToolSpec):
    name = "remove_skill"
    tier = "B"
    description = "Remove a skill the user asked to drop."
    Args = RemoveSkillArgs

    def _locate(self, args: RemoveSkillArgs, doc: StudioDoc) -> str:
        if args.skill.startswith("skl_"):
            return args.skill
        wanted = normalise_key(args.skill)
        for group in doc.skills:
            for item in group.items:
                if normalise_key(item.text) == wanted:
                    return item.nid
        raise ToolError(f"No skill matching {args.skill!r}", code="unknown_node")

    def compile(self, args: RemoveSkillArgs, doc: StudioDoc) -> list[DocOp]:
        return [RemoveNode(nid=self._locate(args, doc), reason=args.reason)]

    def grants(self, args: RemoveSkillArgs, doc: StudioDoc) -> list[IntentGrant]:
        text = args.skill
        if args.skill.startswith("skl_"):
            for group in doc.skills:
                for item in group.items:
                    if item.nid == args.skill:
                        text = item.text
        return [IntentGrant(GrantScope.SKILL_REMOVE, normalise_key(text))]

    def label(self, args: RemoveSkillArgs) -> str:
        return f"removed {args.skill}"


class SetEntryFieldArgs(BaseModel):
    nid: str
    field: Literal["years", "location", "role"]
    value: str
    expect: str | None = None
    reason: str = ""


class SetEntryField(ToolSpec):
    name = "set_entry_field"
    tier = "B"
    description = """
    Change descriptive metadata on an entry: dates, location, or role. Employer
    and job title are identity and need set_entry_identity instead.
    """
    Args = SetEntryFieldArgs

    def compile(self, args: SetEntryFieldArgs, doc: StudioDoc) -> list[DocOp]:
        return [
            SetField(
                target=f"{args.nid}.{args.field}",
                value=args.value,
                expect=args.expect,
                reason=args.reason,
            )
        ]

    def grants(self, args: SetEntryFieldArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.ENTRY_FIELD, f"{args.nid}.{args.field}")]


# --- Tier C: identity and structure ----------------------------------------


class SetPersonalInfoArgs(BaseModel):
    field: Literal[
        "name", "title", "email", "phone", "location", "website", "linkedin", "github"
    ]
    value: str
    reason: str = ""


class SetPersonalInfo(ToolSpec):
    name = "set_personal_info"
    tier = "C"
    description = """
    Change the user's contact details. Only ever call this with a value the user
    themselves supplied in the conversation.
    """
    Args = SetPersonalInfoArgs

    def compile(self, args: SetPersonalInfoArgs, doc: StudioDoc) -> list[DocOp]:
        return [
            SetField(
                target=f"personal.{args.field}", value=args.value, reason=args.reason
            )
        ]

    def grants(self, args: SetPersonalInfoArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.PERSONAL_FIELD, args.field, origin="consent")]

    def label(self, args: SetPersonalInfoArgs) -> str:
        return f"set {args.field}"


class AddExperienceArgs(BaseModel):
    title: str
    company: str
    years: str
    location: str | None = None
    bullets: list[str] = Field(default_factory=list)
    reason: str = ""


class AddExperience(ToolSpec):
    name = "add_experience"
    tier = "C"
    description = "Add a job. Only with details the user gave you."
    Args = AddExperienceArgs

    def compile(self, args: AddExperienceArgs, doc: StudioDoc) -> list[DocOp]:
        nid = mint(NodeKind.EXPERIENCE)
        return [
            InsertNode(
                parent="experience",
                index=0,
                node={
                    "nid": nid,
                    "title": args.title,
                    "company": args.company,
                    "years": args.years,
                    "location": args.location,
                    "bullets": [
                        {"nid": mint(NodeKind.BULLET), "text": text}
                        for text in args.bullets
                    ],
                },
                reason=args.reason,
            )
        ]

    def grants(self, args: AddExperienceArgs, doc: StudioDoc) -> list[IntentGrant]:
        # The nid is minted inside compile, so the grant is resolved by the
        # executor from the applied op rather than guessed here.
        return []

    def label(self, args: AddExperienceArgs) -> str:
        return f"added {args.company}"


class RemoveEntryArgs(BaseModel):
    nid: str
    expect_title: str | None = Field(
        default=None, description="The entry's title, to confirm the right target."
    )
    reason: str = ""


class RemoveEntry(ToolSpec):
    name = "remove_entry"
    tier = "C"
    description = """
    Delete a whole job, degree or project. Always destructive: confirm with the
    user before calling it.
    """
    Args = RemoveEntryArgs

    def compile(self, args: RemoveEntryArgs, doc: StudioDoc) -> list[DocOp]:
        return [RemoveNode(nid=args.nid, reason=args.reason)]

    def grants(self, args: RemoveEntryArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.ENTRY_REMOVE, args.nid, origin="consent")]

    def label(self, args: RemoveEntryArgs) -> str:
        return "removed an entry"


class SetEntryIdentityArgs(BaseModel):
    nid: str
    field: Literal["company", "title", "institution", "degree", "name"]
    value: str
    expect: str | None = None
    reason: str = ""


class SetEntryIdentity(ToolSpec):
    name = "set_entry_identity"
    tier = "C"
    description = """
    Change an employer, job title, institution or degree. These are factual
    claims about the user's history: only ever with a value they supplied.
    """
    Args = SetEntryIdentityArgs

    def compile(self, args: SetEntryIdentityArgs, doc: StudioDoc) -> list[DocOp]:
        return [
            SetField(
                target=f"{args.nid}.{args.field}",
                value=args.value,
                expect=args.expect,
                reason=args.reason,
            )
        ]

    def grants(self, args: SetEntryIdentityArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [
            IntentGrant(
                GrantScope.ENTRY_FIELD, f"{args.nid}.{args.field}", origin="consent"
            )
        ]


class MoveEntryArgs(BaseModel):
    nid: str
    index: int
    section: Literal["experience", "education", "projects"]
    reason: str = ""


class MoveEntry(ToolSpec):
    name = "move_entry"
    # Reordering destroys nothing, so it needs no consent despite acting on
    # entries.
    tier = "A"
    description = "Move an entry to a different position in its section."
    Args = MoveEntryArgs

    def compile(self, args: MoveEntryArgs, doc: StudioDoc) -> list[DocOp]:
        return [
            MoveNode(
                nid=args.nid, parent=args.section, index=args.index, reason=args.reason
            )
        ]


class SetSectionArgs(BaseModel):
    key: str
    visible: bool | None = None
    order: int | None = None
    reason: str = ""


class SetSectionTool(ToolSpec):
    name = "set_section"
    tier = "A"
    description = "Show, hide or reorder a whole section."
    Args = SetSectionArgs

    def compile(self, args: SetSectionArgs, doc: StudioDoc) -> list[DocOp]:
        return [
            SetSection(
                key=args.key,
                visible=args.visible,
                order=args.order,
                reason=args.reason,
            )
        ]

    def grants(self, args: SetSectionArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.SECTION, args.key)]


# --- registry ---------------------------------------------------------------


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs if specs is not None else _default_specs():
            self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    @property
    def names(self) -> set[str]:
        return set(self._specs)

    def for_tiers(self, tiers: set[str]) -> list[ToolSpec]:
        return [spec for spec in self._specs.values() if spec.tier in tiers]

    def schemas(self, tiers: set[str]) -> list[dict[str, Any]]:
        """JSON Schemas for the tools available at these tiers.

        Tier C is included only when the turn may need it. Every tool sent costs
        prompt tokens, and on a local model with a small context those tokens
        come out of the document.
        """
        return [spec.json_schema() for spec in self.for_tiers(tiers)]


def _default_specs() -> list[ToolSpec]:
    return [
        ReadDocument(),
        FindText(),
        RewriteText(),
        AddBullet(),
        RemoveBullet(),
        ReorderBullets(),
        SetBulletStyle(),
        MoveEntry(),
        SetSectionTool(),
        AddSkill(),
        RemoveSkill(),
        SetEntryField(),
        SetPersonalInfo(),
        AddExperience(),
        RemoveEntry(),
        SetEntryIdentity(),
    ]


REGISTRY = ToolRegistry()

# Keyword gate for including Tier C schemas. Crude on purpose: a false positive
# costs a few hundred prompt tokens, a false negative costs the user a
# capability they explicitly asked for.
_TIER_C_HINTS = (
    "email",
    "phone",
    "name",
    "linkedin",
    "github",
    "website",
    "contact",
    "add a job",
    "add my",
    "new job",
    "new role",
    "delete",
    "remove the",
    "drop the",
    "employer",
    "company",
    "job title",
    "degree",
    "university",
)


def tiers_for_message(message: str) -> set[str]:
    """Which tiers to expose for this user message."""
    tiers = {"R", "A", "B"}
    lowered = message.lower()
    if any(hint in lowered for hint in _TIER_C_HINTS):
        tiers.add("C")
    return tiers
