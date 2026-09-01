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

from studio.doc.arrange import ArrangeError
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
        AddPage(),
        Arrange(),
        RemoveElement(),
        RemovePage(),
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
