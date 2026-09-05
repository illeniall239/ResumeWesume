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

import re
from typing import Any, Callable, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from studio.doc.arrange import ArrangeError, Paper, paper_of
from studio.doc.arrange import arrange as arrange_ops
from studio.doc.nodes import NodeKind, mint
from studio.doc.ops import (
    DocOp,
    InsertNode,
    MoveNode,
    RemoveNode,
    Reorder,
    SetElementStyle,
    SetField,
    SetGeometry,
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


class ForkBoardArgs(BaseModel):
    name: str = Field(
        description=(
            "What to call the new version, from what the user asked for: "
            "'Stripe - Payments', 'Teaching roles'. Short and specific."
        )
    )
    reason: str = ""


class ForkBoard(ToolSpec):
    """Start a new version of this résumé, and work on that instead.

    Tier A, which looks wrong for something that creates a document and is not.
    Tier is blast radius, and this has none: it copies the current version and
    leaves it untouched, so the worst outcome is a version nobody wanted, sitting
    beside the one they had. Every editing tool that follows is gated on its own
    terms, exactly as before -- they simply land on the copy.

    Its whole reason for existing is that tailoring destroys. A résumé cut down
    for one job is thin material for the next, and doing it in place means the
    general version is gone. So the assistant makes a copy first and narrows
    that, which is what a person does with a file.
    """

    name = "fork_board"
    tier = "A"
    description = """
    Start a new version of this resume and continue working on that one instead
    of the original. Call this FIRST, before any edits, when the user asks you
    to tailor or adapt the resume to a particular job, or asks for a separate
    version. Never tailor in place: the original is the general resume every
    other version is cut from.

    For several alternatives at once ("give me three versions"), call this once
    per version, editing each before starting the next. Every one is cut from
    the original resume, not from the version before it.
    """
    Args = ForkBoardArgs

    def compile(self, args: BaseModel, doc: StudioDoc) -> list[DocOp]:
        # No ops. This acts on the repository rather than on the document, so
        # the loop executes it directly -- the same shape as a read.
        return []

    def label(self, args: ForkBoardArgs) -> str:
        return f"started {args.name}"


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


def frames_bound_to(doc: StudioDoc, ref: str) -> list[str]:
    """Ids of every placed frame rendering ``ref``.

    Content and layout are separate subtrees, so removing one leaves the other
    dangling unless the caller says otherwise. This is the lookup that lets a
    delete take its own boxes with it.
    """
    return [
        element.nid
        for page in doc.pages
        for element in page.elements
        if getattr(element, "ref", None) == ref
    ]


def orphaned_blocks(doc: StudioDoc, removing: list[str]) -> list[str]:
    """Free text blocks that nothing would render once ``removing`` is gone.

    The mirror image of :func:`frames_bound_to`. A frame bound to a section is a
    *view* of content that lives in the resume, so removing it deletes nothing
    and the words return to the flow. A frame bound to a ``txb_`` block is the
    only home those words have -- leave the block behind and the coverage gate
    calls it stranded and refuses the whole batch.
    """
    doomed = set(removing)
    surviving = {
        element.ref
        for page in doc.pages
        for element in page.elements
        if getattr(element, "ref", None) is not None and element.nid not in doomed
    }

    blocks: list[str] = []
    for page in doc.pages:
        for element in page.elements:
            ref = getattr(element, "ref", None)
            if element.nid not in doomed or ref is None:
                continue
            if not ref.startswith("txb_") or ref in surviving or ref in blocks:
                continue
            blocks.append(ref)
    return blocks


def _without_trailing_ellipsis(value: str) -> str:
    """Drop a trailing ellipsis the model copied from its own reading material.

    The agent reads the document as an outline that clips long values and marks
    the cut with "…". After forty such lines it writes one into the replacement
    text, and a resume bullet that trails off mid-thought is a defect the user
    has to notice and fix by hand.

    Only the agent's output is normalised, never a direct edit: a person typing
    an ellipsis into their own resume meant to.
    """
    trimmed = value.rstrip()
    for mark in ("…", "..."):
        if trimmed.endswith(mark):
            return trimmed[: -len(mark)].rstrip(" ,;:-–—") or value
    return value


class RewriteTextArgs(BaseModel):
    nid: str = Field(description="Node id, e.g. blt_9c21x or sum_00001.")
    value: str = Field(description="The replacement text.")
    expect: str | None = Field(
        default=None,
        description="The current text, to guard against editing a stale value.",
    )
    reason: str = ""

    @field_validator("value")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _said_something(value)


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
                nid=args.nid,
                value=_without_trailing_ellipsis(args.value),
                expect=args.expect,
                reason=args.reason,
            )
        ]

    def grants(self, args: RewriteTextArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.TEXT, args.nid)]

    def label(self, args: RewriteTextArgs) -> str:
        return "rewrote text"


def _said_something(value: str) -> str:
    """Reject text the assistant left empty.

    Nothing stopped a tool writing "" or "   ", and an empty node is not
    invisible: the page draws a skill as a bullet and a bullet as a bullet, so
    a blank one is a dot with no words after it -- on the screen and in the
    PDF.

    Only the assistant is held to this. A person clearing a line they are about
    to retype is editing, and direct edits never come through a tool. To take a
    line *out*, there is `remove_bullet` and `remove_skill`, which is what the
    message says so the model repairs rather than retries.
    """
    if not value.strip():
        raise ValueError(
            "this cannot be empty -- an empty line still draws a bullet. Use "
            "remove_bullet or remove_skill to take one out"
        )
    return value


def _entry_holding(doc: StudioDoc, nid: str) -> Any | None:
    """The experience or project with this id, if either has it."""
    for entry in (*doc.experience, *doc.projects):
        if entry.nid == nid:
            return entry
    return None


class AddBulletArgs(BaseModel):
    parent: str = Field(description="Id of the experience or project to add to.")
    value: str = Field(description="The bullet text.")
    position: int = Field(default=-1, description="-1 appends.")
    reason: str = ""

    @field_validator("value")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _said_something(value)


class AddBullet(ToolSpec):
    name = "add_bullet"
    tier = "A"
    description = "Add a bullet to an experience entry or project."
    Args = AddBulletArgs

    def compile(self, args: AddBulletArgs, doc: StudioDoc) -> list[DocOp]:
        # The same skeleton slot as a blank skill, and the same defect: the
        # starter ships two empty bullets to click into, and appending past
        # them leaves a bullet with no words after it on the page.
        #
        # Written into rather than replaced, unlike a skill: a bullet carries no
        # `source`, so there is nothing about the empty one that would be a
        # false claim once it holds text -- and reusing the node keeps the id
        # anything already pointing at it was given.
        owner = _entry_holding(doc, args.parent)
        blank = _blank_slot(owner.bullets) if owner is not None else None
        if blank is not None and args.position == -1:
            return [SetText(nid=blank.nid, value=args.value, reason=args.reason)]

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


#: Past this a skills section stops being scannable and starts being a keyword
#: dump, which reads as padding to a person and adds nothing for a filter.
MAX_SKILLS_PER_GROUP = 14


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

    @field_validator("skill")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _said_something(value)


def _match_group(wanted: str, doc: StudioDoc) -> Any:
    """Find a skill group by a name the model guessed.

    Three rungs, loosest last: the key as given, the key ignoring case and
    punctuation, then a prefix either way so "technical" reaches
    "technicalSkills" and "skills" reaches "skillsTechnical". A résumé imported
    from a PDF carries whatever group names its author used, and the model is
    working from a default it was handed.
    """
    for group in doc.skills:
        if group.key == wanted:
            return group

    target = normalise_key(wanted)
    if not target:
        return None
    for group in doc.skills:
        if normalise_key(group.key) == target:
            return group
    for group in doc.skills:
        key = normalise_key(group.key)
        if key.startswith(target) or target.startswith(key):
            return group
    return None


class AddSkill(ToolSpec):
    name = "add_skill"
    tier = "B"
    description = """
    Add a skill. You must say where the evidence comes from. Never add a skill
    the user has not demonstrated or asked for.
    """
    Args = AddSkillArgs

    def compile(self, args: AddSkillArgs, doc: StudioDoc) -> list[DocOp]:
        # Matched loosely, because the group key is whatever the résumé happened
        # to be imported with. This argument defaults to "technical"; a real
        # résumé came in with the group named "technicalSkills", so every
        # add_skill on it failed with "No skill group 'technical'" while the
        # group sat right there. An exact comparison here makes the default
        # wrong for any document that did not come from our own templates.
        group = _match_group(args.group, doc)
        if group is None:
            raise ToolError(
                f"No skill group {args.group!r}. Existing groups: "
                + ", ".join(item.key for item in doc.skills)
            )
        # A skill the résumé already lists. Seen for real: a tailoring turn was
        # asked for the skills an AI role expects and added "Python" to a group
        # that already said Python, which reads as carelessness on the page.
        # Told plainly, the model spends its next call on a skill that is
        # actually missing; rejecting would have cost it the round.
        # A skills section is read at a glance, so there is a length past which
        # more entries subtract. Asked for "three or more", Gemini added
        # thirty-four in one turn -- Agile Methodologies, Version Control, SQL --
        # and buried the four that mattered. The instruction now names a range;
        # this is the backstop for when it is read loosely.
        if len(group.items) >= MAX_SKILLS_PER_GROUP:
            raise ToolError(
                f"{args.group!r} already lists {len(group.items)} skills, which "
                "is as many as a reader takes in. Remove one before adding "
                "another, or move on."
            )

        existing = {
            normalise_key(item.text) for item in group.items if item.text.strip()
        }
        if normalise_key(args.skill) in existing:
            raise ToolError(
                f"{args.skill!r} is already in {args.group!r}. Add a different "
                "skill, or move on."
            )

        source = {"jd": "jd", "resume": "resume", "user_request": "user"}[args.evidence]
        insert = InsertNode(
            parent=group.nid,
            index=-1,
            node={"nid": mint(NodeKind.SKILL), "text": args.skill, "source": source},
            reason=args.reason,
        )

        # Take the skeleton's blank slot rather than appending past it. Removed
        # and replaced rather than written into, because a blank ships as the
        # user's own and this skill may not be -- and `source` is what the
        # grounding notice reads to decide which lines nobody vouched for.
        blank = _blank_slot(group.items)
        if blank is not None:
            return [RemoveNode(nid=blank.nid, reason=args.reason), insert]

        return [insert]

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
    # "role" was on this list and is not a field an entry has -- the job title
    # is `title`, and it lives behind `set_entry_identity` because it is a
    # factual claim rather than metadata. Offered the word, models used it: one
    # retargeting turn spent every round calling set_entry_field with
    # field="role", being told the entry had no such field, and calling it again
    # until the turn stalled with nothing changed. It was using the vocabulary
    # we published.
    field: Literal["years", "location"]
    value: str
    expect: str | None = None
    reason: str = ""

    @field_validator("field", mode="before")
    @classmethod
    def _point_at_the_right_tool(cls, value: object) -> object:
        """Say where the job title actually lives.

        A bare Literal error lists the two valid values and leaves the model to
        infer that the thing it wanted is somewhere else entirely.
        """
        if isinstance(value, str) and value.strip().lower() in {
            "role",
            "title",
            "position",
            "job_title",
            "jobtitle",
        }:
            raise ValueError(
                "the job title is not entry metadata -- use set_entry_identity "
                "with title= to change it"
            )
        return value


class SetEntryField(ToolSpec):
    name = "set_entry_field"
    tier = "B"
    description = """
    Change descriptive metadata on an entry: dates or location. Employer and
    job title are identity and need set_entry_identity instead.
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
            ),
            *cover_for(doc, "experience"),
        ]

    def grants(self, args: AddExperienceArgs, doc: StudioDoc) -> list[IntentGrant]:
        # The nid is minted inside compile, so the grant is resolved by the
        # executor from the applied op rather than guessed here.
        return []

    def label(self, args: AddExperienceArgs) -> str:
        return f"added {args.company}"


def cover_for(doc: StudioDoc, section: str) -> list[DocOp]:
    """A frame for a section nothing on the page is drawing yet.

    The coverage gate refuses content no frame renders, which is what keeps a
    document from holding words that never appear. That is right, and it means
    adding the *first* project to a resume laid out without a projects section
    is rejected -- "Nothing on the page would render prj_wzwdb" -- for a request
    that was perfectly reasonable.

    So the entry brings its own frame. Placed below everything else on the last
    page with a nominal height: `autogrow` means the browser measures the text
    and the reflow pass corrects both the height and the position, exactly as it
    does for a frame the server placed at import.

    Empty when the section is already covered, which is the common case -- a
    second job goes inside the frame the first one is already in.
    """
    if frames_bound_to(doc, section) or not doc.pages:
        return []

    page = doc.pages[-1]
    bottom = max(
        (element.rect.y + element.rect.h for element in page.elements),
        default=_PAGE_MARGIN,
    )

    return [
        InsertNode(
            parent=page.nid,
            index=-1,
            node={
                "nid": mint(NodeKind.FRAME),
                "ref": section,
                "rect": {
                    "x": _PAGE_MARGIN,
                    "y": bottom + _SECTION_GAP,
                    "w": paper_of(page).width - _PAGE_MARGIN * 2,
                    "h": _NOMINAL_HEIGHT,
                },
                "rotation": 0.0,
                "autogrow": "height",
                "visible": True,
                "locked": False,
                "style": {},
            },
            reason=f"a frame to render the {section} section",
        )
    ]


#: Space between one section's frame and the next, in points.
_SECTION_GAP = 12.0

#: A frame's stored height is the server's guess and always wrong -- it has no
#: fonts and cannot measure text. The browser corrects it on first paint.
_NOMINAL_HEIGHT = 64.0

#: The margin the PDF export passes to Chromium.
_PAGE_MARGIN = 28.35


def _slug(label: str) -> str:
    """A section key from the label a person typed.

    Keys are how the layout, the renderer and ``set_section`` address a
    section, so they have to be stable and free of the punctuation a label
    carries: "Certifications & Training" becomes ``certificationsTraining``,
    which is the shape imported résumés already use.
    """
    words = re.findall(r"[A-Za-z0-9]+", label)
    if not words:
        return "section"
    head, *rest = words
    return head.lower() + "".join(word.capitalize() for word in rest)


def _blank_slot(items: list[Any]) -> Any | None:
    """A skeleton node still waiting to be filled, if there is one.

    ``starter_doc`` ships one blank skill and two blank bullets on purpose:
    they are what a person clicks into to start typing, and without them a
    freshly created résumé is a blank sheet with nothing to type into.

    Nothing ever consumed them. So a résumé the assistant filled with nine
    skills carried a tenth empty one, which the page draws as a bullet with no
    words after it -- straight into the PDF.

    Returned rather than removed here: the caller decides whether to fill it or
    step past it, and only the first addition should claim it.
    """
    return next((item for item in items if not (item.text or "").strip()), None)


class AddSkillGroupArgs(BaseModel):
    label: str = Field(
        description='What the group is called on the page, e.g. "Languages".'
    )
    skills: list[str] = Field(
        default_factory=list, description="Skills to put in it straight away."
    )
    reason: str = ""


class AddSkillGroup(ToolSpec):
    name = "add_skill_group"
    tier = "B"
    description = """
    Start a new group of skills -- Languages, Tools, Certifications. Use this
    when add_skill says the group does not exist. Adding to a group that is
    already there needs add_skill, not this.
    """
    Args = AddSkillGroupArgs

    def compile(self, args: AddSkillGroupArgs, doc: StudioDoc) -> list[DocOp]:
        key = _slug(args.label)
        existing = _match_group(key, doc)
        if existing is not None:
            raise ToolError(
                f"{existing.key!r} already exists; use add_skill to put "
                "something in it."
            )

        return [
            InsertNode(
                parent="skills",
                index=-1,
                node={
                    "nid": mint(NodeKind.SKILL_GROUP),
                    "key": key,
                    "label": args.label,
                    "items": [
                        # `source` is how the grounding notice knows which lines
                        # nobody has vouched for. A skill arriving with a group
                        # is the user's own, because they just named it.
                        {"nid": mint(NodeKind.SKILL), "text": text, "source": "user"}
                        for text in args.skills
                    ],
                },
                reason=args.reason,
            ),
            *cover_for(doc, "skills"),
        ]

    def label(self, args: AddSkillGroupArgs) -> str:
        return f"added the {args.label} group"


class AddSectionArgs(BaseModel):
    label: str = Field(
        description='The heading, e.g. "Certifications" or "Publications".'
    )
    items: list[str] = Field(
        default_factory=list,
        description="One line each. Only what the user gave you.",
    )
    reason: str = ""


class AddSection(ToolSpec):
    name = "add_section"
    tier = "C"
    description = """
    Add a section the resume does not have: Certifications, Publications,
    Volunteering, Awards. Each item is one line. Only with details the user
    gave you.
    """
    Args = AddSectionArgs

    def compile(self, args: AddSectionArgs, doc: StudioDoc) -> list[DocOp]:
        key = _slug(args.label)
        if any(section.key == key for section in doc.custom):
            raise ToolError(f"There is already a {args.label!r} section.")

        return [
            InsertNode(
                parent="custom",
                index=-1,
                node={
                    "nid": mint(NodeKind.CUSTOM_SECTION),
                    "key": key,
                    "label": args.label,
                    # `stringList` rather than `itemList`: a certification or an
                    # award is one line, and the item shape carries a title, a
                    # subtitle, dates and bullets that would all render empty.
                    "kind": "stringList",
                    "strings": [
                        {"nid": mint(NodeKind.SKILL), "text": text, "source": "user"}
                        for text in args.items
                    ],
                },
                reason=args.reason,
            ),
            *cover_for(doc, "custom"),
        ]

    def label(self, args: AddSectionArgs) -> str:
        return f"added a {args.label} section"


class AddShapeArgs(BaseModel):
    shape: Literal["rect", "ellipse", "line"] = Field(
        default="line", description="A box, an ellipse, or a rule."
    )
    where: Literal[
        "under_the_name",
        "top_of_page",
        "bottom_of_page",
        "left_edge",
        "right_edge",
    ] = Field(
        default="under_the_name",
        description="Where to put it. You cannot see the page, so say a place.",
    )
    page: int = Field(default=1, ge=1)
    colour: str | None = Field(
        default=None,
        description='A hex colour like "#334155". Left out, it follows the ink.',
    )
    reason: str = ""


class AddShape(ToolSpec):
    name = "add_shape"
    tier = "A"
    description = """
    Draw a rule, a box or an ellipse on the page: a line under the name, a band
    down one edge. Decoration only -- nothing here is read by an ATS, so never
    put information in a shape.
    """
    Args = AddShapeArgs

    def compile(self, args: AddShapeArgs, doc: StudioDoc) -> list[DocOp]:
        if not doc.pages:
            raise ToolError("This document has no pages yet. Add a page first.")
        if args.page > len(doc.pages):
            raise ToolError(
                f"There is no page {args.page}; this resume has {len(doc.pages)}."
            )

        page = doc.pages[args.page - 1]
        rect = _shape_rect(args.where, args.shape, paper_of(page), page)
        ink = _colour(args.colour) or _DEFAULT_INK

        return [
            InsertNode(
                parent=page.nid,
                index=-1,
                node={
                    "nid": mint(NodeKind.SHAPE),
                    "shape": args.shape,
                    "rect": rect,
                    "rotation": 0.0,
                    # A line is a rule, not a box: it is drawn with a stroke and
                    # has no fill, and giving it one paints a filled sliver.
                    "fill": None if args.shape == "line" else ink,
                    "stroke": ink if args.shape == "line" else None,
                    "stroke_width": 1.0 if args.shape == "line" else 0.0,
                    "visible": True,
                    "locked": False,
                },
                reason=args.reason,
            )
        ]

    def label(self, args: AddShapeArgs) -> str:
        return f"added a {args.shape}"


#: The ink a shape takes when nobody named a colour. A mid slate, dark enough
#: to read on white and quiet enough not to compete with the words.
_DEFAULT_INK = "#334155"


#: Colour names worth understanding without being told a hex code.
#:
#: Not the full CSS list. These are the words somebody actually says when they
#: ask for a colour on a résumé, plus the greys, and every one of them is a
#: real CSS keyword -- so the value passes through to the browser unchanged and
#: renders identically in the PDF.
_COLOUR_WORDS: frozenset[str] = frozenset(
    {
        "black", "white", "grey", "gray", "silver", "lightgrey", "lightgray",
        "darkgrey", "darkgray", "dimgrey", "dimgray", "slategrey", "slategray",
        "red", "crimson", "firebrick", "darkred", "maroon", "tomato", "salmon",
        "orange", "darkorange", "orangered", "coral", "gold", "goldenrod",
        "yellow", "olive", "khaki",
        "green", "darkgreen", "forestgreen", "seagreen", "olivedrab", "teal",
        "lime", "limegreen",
        "blue", "navy", "midnightblue", "royalblue", "steelblue", "dodgerblue",
        "cornflowerblue", "skyblue", "lightblue", "cadetblue", "turquoise",
        "cyan", "aqua",
        "purple", "indigo", "violet", "orchid", "plum", "magenta", "fuchsia",
        "pink", "hotpink", "brown", "sienna", "chocolate", "tan", "beige",
        "ivory", "linen",
    }
)

_HEX = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$")
_RGB = re.compile(
    r"^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*"
    r"(?:,\s*(?:0|1|0?\.\d+)\s*)?\)$"
)


def _colour(value: str | None) -> str | None:
    """Read a colour the way a person writes one.

    Deliberately generous. Somebody asking for a red heading says "red", and a
    model asked to colour something reaches for "#dc2626" or "crimson" about
    equally often -- refusing two of those three would make the feature look
    broken for a reason that is nothing to do with the résumé.

    A value that is understood is returned ready for CSS, so it renders the
    same on the board and in the exported PDF. One that is not raises rather
    than passing through: an unknown keyword is silently dropped by CSS, and a
    tool that reports success while nothing changes colour is the worse
    failure of the two.
    """
    if value is None:
        return None

    text = value.strip().lower()
    if not text:
        return None
    if text in _COLOUR_WORDS:
        return text
    if _HEX.match(text) or _RGB.match(text):
        return text
    # A bare hex code is what someone pasting from a palette produces.
    if _HEX.match(f"#{text}"):
        return f"#{text}"

    raise ToolError(
        f"{value!r} is not a colour I can read. Use a hex code like '#b91c1c', "
        "an rgb() value, or a plain name like 'red' or 'navy'."
    )

#: Fallback for where the name ends, when the page has no header frame to
#: measure. A constant is a guess; the frame's own height is not, and the
#: browser has already corrected it -- so the real one is preferred below.
_NAME_BAND = 76.0

#: How thick an edge band is.
_BAND = 6.0


def _under_the_name(page: Any) -> float:
    """Where to draw a rule that belongs to the header.

    Measured from the *top* of the personal frame, not its bottom. A frame's
    stored height is the server's guess -- it has no fonts and cannot measure
    text -- and for the header that guess is 64pt against a real 120pt, so
    "below the frame" put the rule through the SUMMARY heading underneath.

    The top edge is real geometry. A name and a title is two lines whatever the
    template, so a fixed drop from there lands under the pair without needing a
    height nobody has measured yet.
    """
    for element in page.elements:
        if getattr(element, "ref", None) == "personal":
            return element.rect.y + _HEADER_DROP
    return _NAME_BAND


#: How far below the top of the header a rule sits: enough to clear a name and
#: a title at the sizes every template uses.
_HEADER_DROP = 52.0


def _shape_rect(
    where: str, shape: str, paper: "Paper", page: Any = None
) -> dict[str, float]:
    """A named place on the page, as a rectangle.

    Named for the same reason `arrange` takes presets and `add_text_box` takes
    a corner: the model cannot see the page, so a coordinate from it is a
    guess. "Under the name" it can mean.
    """
    width = paper.width - _PAGE_MARGIN * 2
    height = 0.0 if shape == "line" else _BAND

    places: dict[str, dict[str, float]] = {
        "under_the_name": {
            "x": _PAGE_MARGIN,
            "y": _under_the_name(page) if page is not None else _NAME_BAND,
            "w": width,
            "h": height,
        },
        "top_of_page": {"x": _PAGE_MARGIN, "y": _PAGE_MARGIN, "w": width, "h": height},
        "bottom_of_page": {
            "x": _PAGE_MARGIN,
            "y": paper.height - _PAGE_MARGIN - max(height, 1.0),
            "w": width,
            "h": height,
        },
        "left_edge": {
            "x": _PAGE_MARGIN,
            "y": _PAGE_MARGIN,
            "w": 0.0 if shape == "line" else _BAND,
            "h": paper.height - _PAGE_MARGIN * 2,
        },
        "right_edge": {
            "x": paper.width - _PAGE_MARGIN - (0.0 if shape == "line" else _BAND),
            "y": _PAGE_MARGIN,
            "w": 0.0 if shape == "line" else _BAND,
            "h": paper.height - _PAGE_MARGIN * 2,
        },
    }
    return places[where]


class AddImageArgs(BaseModel):
    asset: str = Field(
        description=(
            "Id of an image already uploaded to this resume, from the UPLOADS "
            "list. You cannot upload one yourself."
        )
    )
    where: Literal["top_left", "top_right", "top_center"] = Field(
        default="top_right", description="Where to put it."
    )
    page: int = Field(default=1, ge=1)
    alt: str = Field(default="", description="What the image shows, for screen readers.")
    reason: str = ""


class AddImage(ToolSpec):
    name = "add_image"
    tier = "B"
    description = """
    Place an image the user has already uploaded -- a headshot, a logo. Only
    ids from the UPLOADS list work; you cannot upload anything yourself, so if
    there are none, say so rather than guessing an id.
    """
    Args = AddImageArgs

    def compile(self, args: AddImageArgs, doc: StudioDoc) -> list[DocOp]:
        if not doc.pages:
            raise ToolError("This document has no pages yet. Add a page first.")
        if args.page > len(doc.pages):
            raise ToolError(
                f"There is no page {args.page}; this resume has {len(doc.pages)}."
            )

        page = doc.pages[args.page - 1]
        paper = paper_of(page)
        side = _IMAGE_SIDE
        x = {
            "top_left": _PAGE_MARGIN,
            "top_right": paper.width - _PAGE_MARGIN - side,
            "top_center": (paper.width - side) / 2,
        }[args.where]

        return [
            InsertNode(
                parent=page.nid,
                index=-1,
                node={
                    "nid": mint(NodeKind.IMAGE),
                    "asset": args.asset,
                    "rect": {"x": x, "y": _PAGE_MARGIN, "w": side, "h": side},
                    "rotation": 0.0,
                    # `contain`, not `cover`: a headshot cropped to a square by
                    # the renderer is a worse default than one that fits.
                    "fit": "contain",
                    "crop": None,
                    "alt": args.alt,
                    "visible": True,
                    "locked": False,
                    "style": {},
                },
                reason=args.reason,
            )
        ]

    def label(self, args: AddImageArgs) -> str:
        return "placed an image"


#: A square to fit the image inside. Square because the aspect ratio is the
#: asset's business and `fit: contain` honours it; a guess here would letterbox
#: every portrait photo.
_IMAGE_SIDE = 96.0


class SetPhotoArgs(BaseModel):
    asset: str | None = Field(
        default=None,
        description=(
            "Id of an image from the UPLOADS list, or null to take the photo "
            "off. You cannot upload one yourself."
        ),
    )
    reason: str = ""


class SetPhoto(ToolSpec):
    name = "set_photo"
    tier = "C"
    description = """
    Put a photograph the user has uploaded into the résumé's photo holder, or
    take it out again. Only ids from the UPLOADS list work.

    The holder is drawn by the templates that have one -- Portrait, Profile and
    Badge. Setting a photo on any other template stores it and shows nothing
    until they switch, so say that rather than letting them wonder.

    If that picture is already placed on the page as a box, this moves it: the
    box goes and the holder gets it. You do not need to remove it yourself.
    """
    Args = SetPhotoArgs

    def compile(self, args: SetPhotoArgs, doc: StudioDoc) -> list[DocOp]:
        # The id is not checked here and cannot be: assets belong to the repo,
        # and `compile` is handed the document alone -- the same reason
        # `add_image` does not check its own. An id the model invented instead
        # of reading off UPLOADS therefore reaches the page, where the renderer
        # drops the image rather than drawing a broken one.
        ops: list[DocOp] = [
            SetField(
                target="personal.photo",
                value=args.asset or "",
                reason=args.reason,
            )
        ]

        # Placing it in the holder *moves* it there.
        #
        # The route this exists for: somebody adds a headshot with the toolbar,
        # which drops it on the page as a positioned box, and then asks for it
        # in the holder. Setting the field alone left the box where it was, so
        # the same face appeared on the résumé twice -- once floating and once
        # in the header -- and nothing in the reply said so.
        #
        # Only the copies of *this* asset, and only when a photo is being set:
        # taking one out of the holder is not a reason to delete a picture
        # somebody placed deliberately.
        if args.asset:
            ops.extend(
                RemoveNode(nid=element.nid, reason=args.reason)
                for page in doc.pages
                for element in page.elements
                if getattr(element, "asset", None) == args.asset
            )

        return ops

    def _placed(self, args: SetPhotoArgs, doc: StudioDoc) -> int:
        if not args.asset:
            return 0
        return sum(
            1
            for page in doc.pages
            for element in page.elements
            if getattr(element, "asset", None) == args.asset
        )

    def grants(self, args: SetPhotoArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.PERSONAL_FIELD, "photo", origin="consent")]

    def label(self, args: SetPhotoArgs) -> str:
        # `label` is given the arguments alone, so it cannot say whether a
        # placed copy was taken with it -- the wording covers both readings
        # rather than claiming the narrower one.
        return "set the photo" if args.asset else "removed the photo"


class AddEducationArgs(BaseModel):
    institution: str
    degree: str
    years: str = ""
    detail: str = Field(
        default="", description="One line of detail: honours, thesis, coursework."
    )
    reason: str = ""


class AddEducation(ToolSpec):
    name = "add_education"
    tier = "C"
    description = """
    Add a degree. Only with details the user gave you -- an institution or a
    date nobody said is a false claim on a document they will be asked about.
    """
    Args = AddEducationArgs

    def compile(self, args: AddEducationArgs, doc: StudioDoc) -> list[DocOp]:
        node: dict[str, Any] = {
            "nid": mint(NodeKind.EDUCATION),
            "institution": args.institution,
            "degree": args.degree,
            "years": args.years,
        }
        # `detail` is a single optional node, not a list. Sending an empty one
        # would put a blank line under the degree on the page.
        if args.detail.strip():
            node["detail"] = {
                "nid": mint(NodeKind.BULLET),
                "text": args.detail.strip(),
            }

        return [
            # Index 0: education is read newest-first like experience, and a
            # degree somebody just added is the one they are adding *now*.
            InsertNode(parent="education", index=0, node=node, reason=args.reason),
            *cover_for(doc, "education"),
        ]

    def label(self, args: AddEducationArgs) -> str:
        return f"added {args.institution}"


class AddProjectArgs(BaseModel):
    name: str
    role: str = ""
    years: str = ""
    github: str | None = None
    website: str | None = None
    bullets: list[str] = Field(default_factory=list)
    reason: str = ""


class AddProject(ToolSpec):
    name = "add_project"
    tier = "C"
    description = """
    Add a project. Only with details the user gave you.
    """
    Args = AddProjectArgs

    def compile(self, args: AddProjectArgs, doc: StudioDoc) -> list[DocOp]:
        return [
            InsertNode(
                parent="projects",
                index=0,
                node={
                    "nid": mint(NodeKind.PROJECT),
                    "name": args.name,
                    "role": args.role,
                    "years": args.years,
                    "github": args.github,
                    "website": args.website,
                    "bullets": [
                        {"nid": mint(NodeKind.BULLET), "text": text}
                        for text in args.bullets
                    ],
                },
                reason=args.reason,
            ),
            *cover_for(doc, "projects"),
        ]

    def label(self, args: AddProjectArgs) -> str:
        return f"added {args.name}"


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
        # Take the boxes that were showing it with it. Deleting the content
        # alone would leave a frame pointing at nothing, which the coverage
        # gate refuses -- so the tool would simply stop working the moment a
        # document had a layout. Expanded here rather than cascaded inside
        # ``_do_remove`` so that each removal stays one op with one inverse,
        # which is what keeps undo total.
        return [
            *(RemoveNode(nid=nid, reason=args.reason) for nid in frames_bound_to(doc, args.nid)),
            RemoveNode(nid=args.nid, reason=args.reason),
        ]

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
        ops: list[DocOp] = [
            SetSection(
                key=args.key,
                visible=args.visible,
                order=args.order,
                reason=args.reason,
            )
        ]

        # A section's visibility is a fact about content, but on a canvas the
        # frames are what actually draw it. Setting one without the other means
        # "hide my education" leaves education fully visible -- the flag flips
        # and nothing on the page changes.
        if args.visible is not None:
            for nid in frames_bound_to(doc, args.key):
                ops.append(
                    SetElementStyle(nid=nid, patch={"visible": args.visible}, reason=args.reason)
                )
        return ops

    def grants(self, args: SetSectionArgs, doc: StudioDoc) -> list[IntentGrant]:
        return [IntentGrant(GrantScope.SECTION, args.key)]


class AddPageArgs(BaseModel):
    after: int | None = Field(
        default=None,
        description="1-based page number to insert after. Omit to add at the end.",
    )
    reason: str = ""


class AddPage(ToolSpec):
    name = "add_page"
    tier = "A"
    description = """
    Add a blank page. Tier A because blank paper destroys nothing: it adds
    space without touching a word of the resume.
    """
    Args = AddPageArgs

    def compile(self, args: AddPageArgs, doc: StudioDoc) -> list[DocOp]:
        index = -1 if args.after is None else min(max(args.after, 0), len(doc.pages))
        return [
            InsertNode(
                parent="pages",
                index=index,
                node={
                    "nid": mint(NodeKind.PAGE),
                    "size": "A4",
                    "orientation": "portrait",
                    "background": None,
                    "elements": [],
                },
                reason=args.reason,
            )
        ]


class RemoveElementArgs(BaseModel):
    nid: str = Field(description="Id of the image, shape or box to remove.")
    reason: str = ""


class RemoveElement(ToolSpec):
    name = "remove_element"
    tier = "B"
    description = """
    Remove an image, a shape, or a box from the page. Removing a box that was
    showing part of the resume does not delete those words -- they return to
    the normal flow. Removing a box of free text you or the user added does
    delete its words, because that box is the only place they exist. To delete
    a job or a bullet outright, use remove_entry.
    """
    Args = RemoveElementArgs

    def compile(self, args: RemoveElementArgs, doc: StudioDoc) -> list[DocOp]:
        # Restricted by prefix, and raised from `compile` because that is the
        # only hook the loop actually calls -- a separate `validate` method
        # would look like a guard while never running. A model asked to
        # "remove the photo" that reaches for a bullet id gets told what it did
        # wrong; without this the bullet is quietly deleted instead.
        if args.nid.startswith("pag_"):
            raise ToolError(
                f"{args.nid} is a page. Use remove_page, which will tell you if "
                "the page still holds part of the resume."
            )
        if not args.nid.startswith(("img_", "shp_", "frm_")):
            raise ToolError(
                f"{args.nid} is not a page element. This tool removes images, "
                "shapes and boxes; use remove_entry for resume content."
            )
        return [
            # A box bound to a free text block is the only thing rendering it.
            # Removing the frame alone leaves the block covered by nothing,
            # which the coverage gate reads as stranded content and refuses --
            # so without this the tool simply fails on the one kind of box a
            # user is most likely to ask to remove. Cascading in the same batch
            # rather than inside the engine keeps one op to one effect and the
            # whole removal to one undo step, as ``remove_entry`` does.
            *(
                RemoveNode(nid=nid, reason=args.reason)
                for nid in orphaned_blocks(doc, [args.nid])
            ),
            RemoveNode(nid=args.nid, reason=args.reason),
        ]


class ArrangeArgs(BaseModel):
    preset: Literal[
        "align_left",
        "align_right",
        "align_top",
        "align_bottom",
        "align_horizontal_centers",
        "align_vertical_centers",
        "distribute_horizontally",
        "distribute_vertically",
        "center_on_page_horizontally",
        "center_on_page_vertically",
        "center_on_page",
        "snap_page_left",
        "snap_page_right",
        "snap_page_top",
        "snap_page_bottom",
        "match_width",
        "match_height",
    ] = Field(description="Which arrangement to apply.")
    nids: list[str] = Field(
        description=(
            "Ids of the boxes, images or shapes to arrange, as listed under "
            "LAYOUT. All must be on the same page."
        )
    )
    reason: str = ""


class Arrange(ToolSpec):
    name = "arrange"
    tier = "A"
    description = """
    Line up, space out, centre or size-match the boxes, images and shapes on a
    page. You choose the arrangement and which elements it applies to; the
    positions are worked out here, so you never give coordinates.

    Presets: align_left, align_right, align_top, align_bottom,
    align_horizontal_centers, align_vertical_centers, distribute_horizontally,
    distribute_vertically (three or more elements), center_on_page_horizontally,
    center_on_page_vertically, center_on_page, match_width, match_height.

    This is the only way to change where anything sits. There is no tool that
    takes a position, and nothing here can move an element off the page.
    """
    Args = ArrangeArgs

    def compile(self, args: ArrangeArgs, doc: StudioDoc) -> list[DocOp]:
        # Raised from `compile` because it is the only hook the loop calls, and
        # every message names the thing to do differently -- a model told
        # "those are on different pages" retries usefully, where a silent empty
        # list would have it report success for a layout that never changed.
        try:
            ops = arrange_ops(doc, args.preset, args.nids)
        except ArrangeError as error:
            raise ToolError(str(error)) from error

        if not ops:
            raise ToolError(
                "Those elements are already arranged that way; nothing to do."
            )

        # A frame the assistant places is placed by hand as surely as one the
        # user drags: without this the reflow pass owns it again and stacks it
        # back into the column on the next load, so the arrangement would
        # survive the turn and be gone by morning.
        #
        # Every frame in the arrangement, not only the ones that moved. A box
        # already sitting on the target line is still part of a deliberate
        # arrangement, and leaving it unpinned means the reflow shifts it later
        # and breaks the alignment that everything else was moved to make.
        pinned: list[DocOp] = [
            SetElementStyle(nid=nid, patch={"pinned": True}, reason=args.reason)
            for nid in dict.fromkeys(args.nids)
            if _is_unpinned_frame(doc, nid)
        ]
        for op in ops:
            op.reason = args.reason
        return [*pinned, *ops]

    def label(self, args: ArrangeArgs) -> str:
        return f"arranged {len(args.nids)} element(s)"


def _is_unpinned_frame(doc: StudioDoc, nid: str) -> bool:
    for page in doc.pages:
        for element in page.elements:
            if element.nid == nid:
                return hasattr(element, "pinned") and not element.pinned
    return False


class RemovePageArgs(BaseModel):
    page: int = Field(description="1-based page number to remove.", ge=1)
    reason: str = ""


class RemovePage(ToolSpec):
    name = "remove_page"
    tier = "C"
    description = """
    Remove a page. Only works on a page that holds nothing but images, shapes
    and boxes -- a page still showing part of the resume cannot be deleted
    until that content is moved somewhere else.
    """
    Args = RemovePageArgs

    def compile(self, args: RemovePageArgs, doc: StudioDoc) -> list[DocOp]:
        if args.page > len(doc.pages):
            raise ToolError(
                f"There is no page {args.page}; the resume has {len(doc.pages)}."
            )
        if len(doc.pages) <= 1:
            raise ToolError("A resume needs at least one page.")

        page = doc.pages[args.page - 1]
        # Refused here rather than left to the coverage gate, so the model is
        # told *why* and can offer to move the content instead. The gate would
        # reject the batch with a node id, which is not something the user can
        # act on.
        covered_elsewhere = {
            element.ref
            for other in doc.pages
            if other.nid != page.nid
            for element in other.elements
            if hasattr(element, "ref")
        }
        stranded = [
            element.ref
            for element in page.elements
            if hasattr(element, "ref")
            and not element.ref.startswith("txb_")
            and element.ref not in covered_elsewhere
        ]
        if stranded:
            raise ToolError(
                f"Page {args.page} is the only place showing {', '.join(stranded)}. "
                "Move that onto another page first, or ask to delete the content itself."
            )

        # Hand-placed text dies with its box; leaving it behind would keep the
        # words in the ATS export while being invisible in the editor.
        blocks = [
            element.ref
            for element in page.elements
            if hasattr(element, "ref") and element.ref.startswith("txb_")
        ]
        return [
            *(RemoveNode(nid=nid, reason=args.reason) for nid in blocks),
            RemoveNode(nid=page.nid, reason=args.reason),
        ]

    def grants(self, args: RemovePageArgs, doc: StudioDoc) -> list[IntentGrant]:
        page = doc.pages[args.page - 1] if args.page <= len(doc.pages) else None
        return [IntentGrant(GrantScope.ENTRY_REMOVE, page.nid, origin="consent")] if page else []


class AddTextBoxArgs(BaseModel):
    # `value`, not `text`, because salvage normalises `text` to `value` for
    # every tool -- every other one calls this field `value`, and models reach
    # for the synonym often enough to be worth correcting. Named `text` here,
    # the argument was renamed out from under the only tool expecting it, and
    # the call was rejected as "text: Field required" while the model insisted
    # it had sent one. It had.
    value: str = Field(description="What the box should say.")
    corner: Literal[
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
        "top_center",
        "bottom_center",
    ] = Field(
        default="bottom_right",
        description="Where on the page to put it.",
    )
    page: int = Field(default=1, ge=1, description="Which page, counting from 1.")
    align: Literal["left", "center", "right", "auto"] = Field(
        default="auto",
        description=(
            "How the text sits inside the box. 'auto' follows the corner, "
            "which is almost always what you want."
        ),
    )
    reason: str = ""

    @field_validator("value")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _said_something(value)


class AddTextBox(ToolSpec):
    name = "add_text_box"
    tier = "A"
    description = """
    Put a standalone line of text on the page: a footer, a caption, a note in a
    corner. Unlike a bullet or the summary, this belongs to no section and sits
    where you place it. Say which corner -- you cannot see the page, so you
    cannot give a position.
    """
    Args = AddTextBoxArgs

    def compile(self, args: AddTextBoxArgs, doc: StudioDoc) -> list[DocOp]:
        if not doc.pages:
            raise ToolError(
                "This document has no pages yet, so there is nowhere to put a "
                "box. Add a page first."
            )
        if args.page > len(doc.pages):
            raise ToolError(
                f"There is no page {args.page}; this resume has "
                f"{len(doc.pages)}."
            )

        page = doc.pages[args.page - 1]
        paper = paper_of(page)
        rect = _corner_rect(args.corner, paper)
        # The box is wider than a short line, so a left-aligned footer in the
        # bottom-right corner sits a box-width in from the edge -- which is
        # what "it was not all the way to the right" meant. The corner already
        # says which edge was asked for.
        align = _ALIGN_FOR_CORNER[args.corner] if args.align == "auto" else args.align

        block_nid = mint(NodeKind.BLOCK)
        line_nid = mint(NodeKind.SUMMARY)
        frame_nid = mint(NodeKind.FRAME)

        return [
            # Content before the frame that renders it: the coverage gate
            # rejects a frame whose `ref` does not resolve yet, and ops within
            # a batch apply in order.
            InsertNode(
                parent="blocks",
                index=-1,
                node={
                    "nid": block_nid,
                    "role": "caption",
                    "lines": [
                        {"nid": line_nid, "text": args.value, "style": "plain"}
                    ],
                },
                reason=args.reason,
            ),
            InsertNode(
                parent=page.nid,
                index=-1,
                node={
                    "nid": frame_nid,
                    "ref": block_nid,
                    "rect": rect,
                    "rotation": 0.0,
                    "autogrow": "height",
                    "visible": True,
                    "locked": False,
                    # Hand-placed, so the reflow pass leaves it where it was
                    # put. Without this the column would sweep a footer back
                    # into the flow on the next measure.
                    "pinned": True,
                    "style": {"align": align},
                },
                reason=args.reason,
            ),
        ]

    def label(self, args: AddTextBoxArgs) -> str:
        return f"added a text box: {args.value[:40]}"


#: Which way the words face in each corner. A box against the right edge whose
#: text is left-aligned is not against the right edge as far as a reader is
#: concerned.
_ALIGN_FOR_CORNER: dict[str, str] = {
    "top_left": "left",
    "bottom_left": "left",
    "top_center": "center",
    "bottom_center": "center",
    "top_right": "right",
    "bottom_right": "right",
}

#: A corner box, in points. Wide enough for a line of small print and short
#: enough not to cover anything; `autogrow` corrects the height once the
#: browser has measured the text.
_BOX = (200.0, 18.0)

def _corner_rect(corner: str, paper: "Paper") -> dict[str, float]:
    """Turn a named corner into a rectangle on this page.

    Named rather than numeric, for the same reason `arrange` takes a preset:
    the model cannot see the page, so a coordinate from it is a guess. A corner
    is something it can mean.
    """
    width, height = _BOX
    left = _PAGE_MARGIN
    right = paper.width - _PAGE_MARGIN - width
    top = _PAGE_MARGIN
    bottom = paper.height - _PAGE_MARGIN - height
    middle = (paper.width - width) / 2

    x, y = {
        "top_left": (left, top),
        "top_right": (right, top),
        "top_center": (middle, top),
        "bottom_left": (left, bottom),
        "bottom_right": (right, bottom),
        "bottom_center": (middle, bottom),
    }[corner]
    return {"x": x, "y": y, "w": width, "h": height}


class StyleElementArgs(BaseModel):
    nid: str = Field(
        description="Id of the box, image or shape, as listed under LAYOUT."
    )
    align: Literal["left", "center", "right"] | None = Field(
        default=None, description="How text sits inside the box."
    )
    font_scale: float | None = Field(
        default=None, ge=0.5, le=2.0, description="Text size, 1.0 being normal."
    )
    opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    padding: float | None = Field(default=None, ge=0.0, le=48.0)
    color: str | None = Field(
        default=None,
        description=(
            "Colour of the text in the box. A hex code like '#b91c1c', or a "
            "plain name like 'red' or 'navy'."
        ),
    )
    background: str | None = Field(
        default=None, description="Colour behind the box. Same forms as color."
    )
    reason: str = ""


class StyleElement(ToolSpec):
    """Named for the class, not the tool: ``SetElementStyle`` is already the
    *op* this compiles to, and shadowing it here silently rebound every other
    tool that emits one."""

    name = "set_element_style"
    tier = "A"
    description = """
    Change how a placed box looks: which way its text is aligned, its size on
    the page, how solid it is. Never what it says -- use rewrite_text for that.
    Pass only what you want changed.
    """
    Args = StyleElementArgs

    def compile(self, args: StyleElementArgs, doc: StudioDoc) -> list[DocOp]:
        patch = {
            key: value
            for key, value in (
                ("align", args.align),
                ("font_scale", args.font_scale),
                ("opacity", args.opacity),
                ("padding", args.padding),
                ("color", _colour(args.color)),
                ("background", _colour(args.background)),
            )
            if value is not None
        }
        if not patch:
            raise ToolError(
                "Say what to change: align, font_scale, opacity, padding, "
                "color or background."
            )

        return [SetElementStyle(nid=args.nid, patch=patch, reason=args.reason)]

    def label(self, args: StyleElementArgs) -> str:
        if args.color:
            return "recoloured a box"
        if args.background:
            return "filled a box"
        if args.align:
            return f"aligned a box {args.align}"
        return "restyled a box"


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
        ForkBoard(),
        RewriteText(),
        AddBullet(),
        RemoveBullet(),
        ReorderBullets(),
        SetBulletStyle(),
        MoveEntry(),
        SetSectionTool(),
        AddPage(),
        Arrange(),
        RemoveElement(),
        RemovePage(),
        AddSkill(),
        RemoveSkill(),
        SetEntryField(),
        SetPersonalInfo(),
        AddExperience(),
        AddEducation(),
        AddProject(),
        AddSkillGroup(),
        AddSection(),
        AddShape(),
        AddImage(),
        SetPhoto(),
        RemoveEntry(),
        SetEntryIdentity(),
        AddTextBox(),
        StyleElement(),
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
    # Pages. Without these a confirmation follow-up ("yes, remove page 3")
    # matches no hint, tier C is not exposed, and the assistant reports that it
    # has no tool for something it proposed one turn earlier.
    "page",
    "pages",
    "blank page",
    "get rid of",
    "yes",
    "go ahead",
    "confirm",
    "do it",
)


def tiers_for_message(message: str) -> set[str]:
    """Which tiers to expose for this user message."""
    tiers = {"R", "A", "B"}
    lowered = message.lower()
    if any(hint in lowered for hint in _TIER_C_HINTS):
        tiers.add("C")
    return tiers
