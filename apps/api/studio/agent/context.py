"""What the model sees.

A full resume serialised as JSON runs 3,000 to 6,000 tokens. Ollama defaults to
a 4,096-token context and, critically, truncates the **front** of an oversized
prompt — which is where the system prompt and the tool schemas live. So a model
handed the whole document does not merely lose the document: it loses its
instructions and its tools first, then behaves as though it never had any. The
failure looks like model stupidity and is actually context arithmetic.

The outline below is roughly a tenth the size and carries the one thing the
model genuinely cannot work without: node ids paired with enough text to pick
the right one. Full text is available per section, on request, through
``read_document``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from studio.agent import formatting
from studio.doc.schema import (
    CustomSectionNode,
    SectionMeta,
    StudioDoc,
    prose_fields,
)

# Enough to identify a line, not enough to reproduce it.
_SNIPPET = 72


def _clip(text: str, limit: int = _SNIPPET) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def roster(boards: list[Any], working_on: str) -> str:
    """The other versions of this résumé, by name.

    Appended only when there is more than one, so the résumé almost everybody
    has pays nothing for it. Names rather than ids: a version is referred to
    the way a person refers to it, and an id in the prompt is one more thing
    for a model to confuse with a node.

    It is read-only context. The assistant edits one version per turn — the one
    it is working on — and telling it the others exist is what stops it
    proposing a change that already lives on another version, and lets it say
    which one to open instead of guessing.
    """
    if len(boards) < 2:
        return ""

    lines = [
        "VERSIONS: this résumé is kept in several versions, aimed at different "
        "jobs. You are editing one of them and cannot reach the others in this "
        "turn; name one if the user should open it instead."
    ]
    for board in boards:
        here = "  <- you are editing this one" if board.id == working_on else ""
        lines.append(f"  {board.title or 'Untitled'}{here}")
    return "\n".join(lines)


def uploads(assets: list[Any]) -> str:
    """The images this document holds, so the assistant can name one.

    Appended to the prompt only when there are any -- which is almost never --
    so a résumé with no pictures pays nothing for the capability. The assistant
    cannot upload anything itself; these ids are the only images it may place.
    """
    if not assets:
        return ""

    lines = [
        "UPLOADS: images the user has attached, for `set_photo` (the "
        "résumé's photo holder) or `add_image` (a picture placed on the page)."
    ]
    for asset in assets:
        lines.append(
            f"  {asset.id} ({asset.mime.split('/')[-1]}, "
            f"{asset.width}x{asset.height})"
        )
    return "\n".join(lines)


def outline(doc: StudioDoc, *, section: str | None = None) -> str:
    """A compact, id-annotated view of the document."""
    lines: list[str] = []

    def wanted(name: str) -> bool:
        return section is None or section == name

    # What the résumé calls each part of itself. The outline printed
    # "EXPERIENCE:" whatever the heading said, so a résumé whose owner had
    # renamed it to "Selected Work" was described to the assistant under a
    # name that is not on the page -- and `set_section` could rename a heading
    # the assistant had no way to read back.
    def heading(key: str, fallback: str) -> str:
        meta = next((row for row in doc.sections if row.key == key), None)
        label = (meta.label if meta else "") or fallback
        return f"{label.upper()} (section key: {key}):"

    if wanted("personal"):
        personal = doc.personal
        contact = ", ".join(
            value for value in (personal.email, personal.phone, personal.location) if value
        )
        lines.append(f"NAME: {personal.name or '(empty)'} — {personal.title or '(no title)'}")
        if contact:
            lines.append(f"CONTACT: {contact}")

    if wanted("summary"):
        title = heading("summary", "summary")
        if doc.summary:
            lines.append(f"{title} [{doc.summary.nid}] {_clip(doc.summary.text)}")
        else:
            lines.append(f"{title} (empty)")

    if wanted("experience") and doc.experience:
        lines.append(heading("experience", "experience"))
        for entry in doc.experience:
            lines.append(
                f"  [{entry.nid}] {entry.title} @ {entry.company} ({entry.years})"
            )
            for bullet in entry.bullets:
                lines.append(f"    [{bullet.nid}] {_clip(bullet.text)}")

    if wanted("education") and doc.education:
        lines.append(heading("education", "education"))
        for entry in doc.education:
            lines.append(
                f"  [{entry.nid}] {entry.degree}, {entry.institution} ({entry.years})"
            )

    if wanted("projects") and doc.projects:
        lines.append(heading("projects", "projects"))
        for entry in doc.projects:
            # The role, which is the line under the name on the page and was in
            # none of the three views of this document. Asked to change
            # "Founder" to "Creator", the assistant searched a résumé it could
            # not see the word in and reported it absent.
            role = f" — {entry.role}" if entry.role else ""
            lines.append(f"  [{entry.nid}] {entry.name}{role} ({entry.years})")
            for bullet in entry.bullets:
                lines.append(f"    [{bullet.nid}] {_clip(bullet.text)}")

    if wanted("skills") and doc.skills:
        lines.append(heading("skills", "skills"))
        for group in doc.skills:
            items = ", ".join(f"{item.text} [{item.nid}]" for item in group.items)
            lines.append(
                f"  {group.label} (group {group.nid}, {_shape(group.display)}): "
                f"{items or '(empty)'}"
            )

    if wanted("custom") and doc.custom:
        lines.append("CUSTOM SECTIONS:")
        for custom in doc.custom:
            # The key as well as the nid, because they are used by different
            # tools and only one of them was ever shown. Every other line in
            # this outline is addressed by nid, so a model asked to move a
            # custom section reached for the nid it could see and
            # `set_section` -- the one tool keyed by section key -- rejected
            # it. The call was reasonable, the failure was ours, and the model
            # only recovered because the error happened to list the real keys.
            lines.append(
                f"  [{custom.nid}] {custom.label or custom.key} "
                f"(section key: {custom.key})"
            )
            # And what is in it. Only the heading was listed, so a whole
            # Certifications or Awards section -- which is a `stringList`, and
            # keeps every line in `strings` -- was a title with no contents as
            # far as the assistant could see. It could not quote one, edit one,
            # or tell the user what was there.
            if custom.text:
                lines.append(f"    [{custom.text.nid}] {_clip(custom.text.text)}")
            for item in custom.items:
                lines.append(f"    [{item.nid}] {item.title} ({item.years})")
                for bullet in item.bullets:
                    lines.append(f"      [{bullet.nid}] {_clip(bullet.text)}")
            if custom.strings:
                lines.append(f"    ({_shape(custom.display)})")
            for line in custom.strings:
                lines.append(f"    [{line.nid}] {_clip(line.text)}")

    if wanted("blocks") and doc.blocks:
        lines.append("TEXT BOXES:")
        for block in doc.blocks:
            for line in block.lines:
                lines.append(f"  [{line.nid}] {_clip(line.text)}")

    # Sections the résumé already declares and has nothing in yet.
    #
    # They printed no line at all, so they did not exist as far as the
    # assistant could see -- and asked to add Projects to a résumé whose
    # Projects section was empty rather than absent, the only tool it could
    # reach for was `add_section`. The engine refused it, correctly, with
    # "'Projects' is already a section of this resume". The call was
    # reasonable; the outline was what made it wrong.
    #
    # Derived from `walk`, so a section counts as filled when it holds any
    # writing at all and this cannot drift from what the rest of the view says.
    if section is None:
        filled = {item.section for item in walk(doc)}
        empty = [
            meta.key
            for meta in sorted(doc.sections, key=lambda meta: meta.order)
            if meta.key not in filled
        ]
        if empty:
            lines.append(
                "EMPTY SECTIONS (already on this résumé, nothing in them yet): "
                + ", ".join(empty)
            )

    if section is None:
        lines.extend(_layout_preamble(doc))
        # How the page *looks*, which for a résumé is not a secondary concern.
        # Silent when there is nothing wrong, so a tidy document costs nothing
        # here and the notes only ever appear when they are actionable.
        lines.extend(formatting.notes(doc))

    return "\n".join(lines) if lines else "(the resume is empty)"


def _layout_preamble(doc: StudioDoc) -> list[str]:
    """What sits on which page, and what each thing is called.

    **Still no coordinates.** The assistant can name an element and say which
    page it is on; it cannot read a position or a size, because it has no tool
    that takes one. ``arrange`` takes a preset and a list of ids and does the
    arithmetic server-side, so ids are exactly what it needs and numbers are
    exactly what it must not have -- a model given coordinates it cannot act on
    invents instructions the user cannot follow.

    Ids rather than only section names, because ``arrange`` addresses elements:
    without them the assistant would have to guess a ``frm_`` id, and a guess
    is a rejected op.
    """
    if not doc.pages:
        return []

    lines = [f"LAYOUT: {len(doc.pages)} page(s). Ids below are for `arrange`."]
    for number, page in enumerate(doc.pages, start=1):
        named = [f"{element.nid} ({_describes(element)})" for element in page.elements]
        parts = ", ".join(named) if named else "empty"
        lines.append(f"  page {number}: {parts}")
    return lines


def _describes(element: Any) -> str:
    """What an element is, in the words the user would use for it."""
    ref = getattr(element, "ref", None)
    if ref is None:
        asset = getattr(element, "asset", None)
        if asset is not None:
            # Named, not just called "image". Without the id there is no way to
            # tell that a box on the page and an entry in UPLOADS are the same
            # picture, so "put that photo in the holder" could not be answered
            # about a picture already on the sheet.
            return f"image {asset}"
        return f"{getattr(element, 'shape', 'shape')} shape"
    if ref.startswith("txb_"):
        return "text box"
    if ref.startswith(("exp_", "edu_", "prj_")):
        return f"entry {ref}"
    return ref


@dataclass(frozen=True)
class Written:
    """One node, and the writing on it.

    ``nid`` is what a tool would address it by, which for the two things that
    have no node id is the path the tool already uses: ``personal`` for the
    header, ``section.<key>`` for a heading. Naming them any other way would
    make them findable and still unreachable.
    """

    nid: str
    #: The field that holds it -- ``experience``, ``bullets``, ``strings``.
    kind: str
    #: The part of the résumé it belongs to, by key, so a custom section is
    #: addressed by the name the person gave it.
    section: str
    depth: int
    fields: dict[str, str]

    @property
    def text(self) -> str:
        return " ".join(self.fields.values())


#: Not walked: a page holds geometry, addressed by ``set_geometry`` and
#: ``arrange``, and none of it is writing.
_NOT_CONTENT = frozenset({"pages"})


def walk(doc: StudioDoc) -> list[Written]:
    """Every piece of writing in the document, derived rather than listed.

    The whole document, from its own annotations: what is prose is decided by
    ``prose_fields``, and what to descend into is decided by finding a model
    there. Nothing here names a field, so a field added to the schema is
    searchable and readable the moment it exists -- which is the property the
    hand-written walks this replaces could not have.

    Three of them drifted apart exactly as you would expect. A project's role
    was in none: the outline printed ``name (years)``, the search matched on
    ``name``, and asked to change "Founder" to "Creator" the assistant searched
    a résumé that, as far as it could see, did not contain the word, and said
    so. A whole Certifications section was a heading with no contents for the
    same reason. Neither is possible to write now.

    The engine has walked the document this way all along -- ``_first_duplicate``
    in ``apply.py`` reads ``model_fields`` and has never had a gap of its own.
    """
    found: list[Written] = []

    def visit(node: Any, *, nid: str, kind: str, section: str, depth: int) -> None:
        fields = {
            name: value.strip()
            for name in prose_fields(type(node))
            if (value := getattr(node, name, None)) and str(value).strip()
        }
        if fields:
            found.append(
                Written(nid=nid, kind=kind, section=section, depth=depth, fields=fields)
            )
        for name in type(node).model_fields:
            value = getattr(node, name)
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, BaseModel):
                    visit(
                        child,
                        nid=getattr(child, "nid", nid),
                        kind=name,
                        section=section,
                        depth=depth + 1,
                    )

    for name in type(doc).model_fields:
        if name in _NOT_CONTENT:
            continue
        value = getattr(doc, name)
        for child in value if isinstance(value, list) else [value]:
            if not isinstance(child, BaseModel):
                continue
            key = getattr(child, "key", None)
            visit(
                child,
                # A heading is not a node. It is reached as `section.<key>`,
                # which is the address `set_field` takes, so the search returns
                # something the assistant can act on rather than a name.
                nid=(
                    f"section.{key}"
                    if isinstance(child, SectionMeta)
                    else getattr(child, "nid", name)
                ),
                kind="heading" if isinstance(child, SectionMeta) else name,
                # A custom section answers to the heading the person reads.
                section=key if isinstance(child, CustomSectionNode) else name,
                depth=0,
            )
    return found


#: Names a caller may use for a part of the résumé that is not a field on the
#: document. Everything else matches a section key directly.
_ALIASES = {"text": "blocks", "contact": "personal"}


def full_section(doc: StudioDoc, section: str) -> str:
    """Untruncated text for one section, every field of it.

    A dump, so it dumps: each node's writing under its own id, field by field,
    indented by how deeply it sits. Reading a project used to answer with its
    name and bullets and leave out the role and the links; reading a custom
    section answered "(no certifications)" about a section on the page; reading
    ``personal`` answered nothing at all, so contact details could not be read
    back in full by any means.
    """
    wanted = _matching(doc, section)
    lines = [
        "  " * item.depth
        + f"[{item.nid}] "
        + ", ".join(f"{name}: {value}" for name, value in item.fields.items())
        for item in walk(doc)
        if item.section in wanted
    ]
    return "\n".join(lines) if lines else f"(no {section})"


def _matching(doc: StudioDoc, section: str) -> set[str]:
    """Which sections a caller means, including by the heading they can see."""
    asked = _ALIASES.get(section, section)
    if asked == "custom":
        return {custom.key for custom in doc.custom}
    return {asked} | {
        custom.key
        for custom in doc.custom
        if asked.lower() in {custom.key.lower(), (custom.label or "").lower()}
    }


def texts(doc: StudioDoc) -> list[tuple[str, str, str]]:
    """Every addressable piece of writing: (nid, kind, text).

    The one inventory, and the reason the search cannot be narrower than the
    document: it is the document.
    """
    return [(item.nid, item.kind, item.text) for item in walk(doc)]


def find(doc: StudioDoc, query: str, limit: int = 5) -> list[dict[str, str]]:
    """Fuzzy search over everything written in the document.

    Deliberately simple token overlap rather than embeddings: the corpus is one
    résumé, the queries are things like "the AWS bullet", and a dependency on a
    model to find a node would make the search fail exactly when the model is
    already struggling.

    What it searches is not a list kept beside the schema -- it is ``walk``,
    which is the schema. A field that exists is a field this finds.
    """
    terms = _tokens(query) - _STOPWORDS
    if not terms:
        return []

    scored: list[tuple[float, dict[str, str]]] = []
    for nid, kind, text in texts(doc):
        tokens = _tokens(text)
        overlap = len(terms & tokens)
        if not tokens or not overlap:
            continue
        # Favour density: a short line matching two terms beats a long one that
        # matches the same two by accident.
        score = overlap + overlap / len(tokens)
        scored.append((score, {"nid": nid, "kind": kind, "text": _clip(text, 100)}))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [payload for _, payload in scored[:limit]]


#: Words carrying no signal in a search over a resume. Used instead of a
#: minimum length, which is what this replaced.
#:
#: The length rule dropped every term of two characters or fewer, and a resume
#: is full of two-character terms that are the whole point of the query: AI, ML,
#: UX, QA, Go, R, C. Searching for the skill "Go" -- a word sitting in the
#: document -- returned nothing, and so did "AI" for someone tailoring towards
#: an AI role. A miss for a word that is genuinely absent is an answer; a miss
#: for one that is present is a lie the caller cannot tell apart from it.
_STOPWORDS = {
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "can", "for",
    "from", "in", "is", "it", "its", "me", "my", "of", "on", "or", "our",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "to", "was", "were", "with", "you", "your",
}


#: How a run of short items is currently set, in words the model can act on.
#: Shown for every group because the shape of a résumé is not a detail the
#: assistant should have to ask about -- and because "auto" is a real answer,
#: not a missing one.
_SHAPES = {
    "list": "shown as a bulleted list",
    "inline": "shown as one comma-separated line",
    "auto": "line or list, whichever fits the items",
}


def _shape(display: str) -> str:
    return _SHAPES.get(display, _SHAPES["auto"])


def _tokens(text: str) -> set[str]:
    """The words a query can match, both joined and split at the punctuation.

    Stripping punctuation from a whitespace-separated word turns
    "github.com/amorgan/ledgerline" into one token no one would ever type, and
    "Python/Django" into one that hides "Django". Splitting instead loses the
    opposite case: "e-mail" would stop answering to "email".

    Both are cheap, so both are kept. A term matches if it is the word with its
    punctuation removed, or any run between the punctuation.
    """
    found: set[str] = set()
    for word in text.split():
        runs = _RUNS.findall(word.lower())
        if not runs:
            continue
        found.update(runs)
        if len(runs) > 1:
            found.add("".join(runs))
    return found


#: A run of letters or digits: what survives when punctuation is a separator.
_RUNS = re.compile(r"[^\W_]+", re.UNICODE)
