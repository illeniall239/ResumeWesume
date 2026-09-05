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

from typing import Any

from studio.doc.schema import StudioDoc

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

    if wanted("personal"):
        personal = doc.personal
        contact = ", ".join(
            value for value in (personal.email, personal.phone, personal.location) if value
        )
        lines.append(f"NAME: {personal.name or '(empty)'} — {personal.title or '(no title)'}")
        if contact:
            lines.append(f"CONTACT: {contact}")

    if wanted("summary"):
        if doc.summary:
            lines.append(f"SUMMARY [{doc.summary.nid}]: {_clip(doc.summary.text)}")
        else:
            lines.append("SUMMARY: (empty)")

    if wanted("experience") and doc.experience:
        lines.append("EXPERIENCE:")
        for entry in doc.experience:
            lines.append(
                f"  [{entry.nid}] {entry.title} @ {entry.company} ({entry.years})"
            )
            for bullet in entry.bullets:
                lines.append(f"    [{bullet.nid}] {_clip(bullet.text)}")

    if wanted("education") and doc.education:
        lines.append("EDUCATION:")
        for entry in doc.education:
            lines.append(
                f"  [{entry.nid}] {entry.degree}, {entry.institution} ({entry.years})"
            )

    if wanted("projects") and doc.projects:
        lines.append("PROJECTS:")
        for entry in doc.projects:
            lines.append(f"  [{entry.nid}] {entry.name} ({entry.years})")
            for bullet in entry.bullets:
                lines.append(f"    [{bullet.nid}] {_clip(bullet.text)}")

    if wanted("skills") and doc.skills:
        lines.append("SKILLS:")
        for group in doc.skills:
            items = ", ".join(f"{item.text} [{item.nid}]" for item in group.items)
            lines.append(f"  {group.label} (group {group.nid}): {items or '(empty)'}")

    if wanted("custom") and doc.custom:
        lines.append("CUSTOM SECTIONS:")
        for custom in doc.custom:
            lines.append(f"  [{custom.nid}] {custom.label or custom.key}")

    if wanted("blocks") and doc.blocks:
        lines.append("TEXT BOXES:")
        for block in doc.blocks:
            for line in block.lines:
                lines.append(f"  [{line.nid}] {_clip(line.text)}")

    if section is None:
        lines.extend(_layout_preamble(doc))

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


def full_section(doc: StudioDoc, section: str) -> str:
    """Untruncated text for one section."""
    lines: list[str] = []

    if section == "summary" and doc.summary:
        lines.append(f"[{doc.summary.nid}] {doc.summary.text}")

    if section in {"experience", "projects"}:
        entries = doc.experience if section == "experience" else doc.projects
        for entry in entries:
            heading = (
                f"{entry.title} @ {entry.company}"
                if section == "experience"
                else entry.name
            )
            lines.append(f"[{entry.nid}] {heading} ({entry.years})")
            for bullet in entry.bullets:
                lines.append(f"  [{bullet.nid}] ({bullet.style}) {bullet.text}")

    if section == "education":
        for entry in doc.education:
            lines.append(f"[{entry.nid}] {entry.degree}, {entry.institution}")
            if entry.detail:
                lines.append(f"  [{entry.detail.nid}] {entry.detail.text}")

    if section == "skills":
        for group in doc.skills:
            lines.append(f"[{group.nid}] {group.label}")
            for item in group.items:
                lines.append(f"  [{item.nid}] {item.text} (source: {item.source})")

    if section in {"blocks", "text"}:
        for block in doc.blocks:
            lines.append(f"[{block.nid}] {block.role} text box")
            for line in block.lines:
                lines.append(f"  [{line.nid}] {line.text}")

    return "\n".join(lines) if lines else f"(no {section})"


def find(doc: StudioDoc, query: str, limit: int = 5) -> list[dict[str, str]]:
    """Fuzzy search over text nodes.

    Deliberately simple token overlap rather than embeddings: the corpus is one
    resume, the queries are things like "the AWS bullet", and a dependency on a
    model to find a node would make the search fail exactly when the model is
    already struggling.
    """
    terms = _tokens(query) - _STOPWORDS
    if not terms:
        return []

    scored: list[tuple[float, dict[str, str]]] = []

    def consider(nid: str, kind: str, text: str) -> None:
        tokens = _tokens(text)
        if not tokens:
            return
        overlap = len(terms & tokens)
        if not overlap:
            return
        # Favour density: a short line matching two terms beats a long one that
        # matches the same two by accident.
        score = overlap + overlap / len(tokens)
        scored.append((score, {"nid": nid, "kind": kind, "text": _clip(text, 100)}))

    if doc.summary:
        consider(doc.summary.nid, "summary", doc.summary.text)
    for entry in doc.experience:
        consider(entry.nid, "experience", f"{entry.title} {entry.company}")
        for bullet in entry.bullets:
            consider(bullet.nid, "bullet", bullet.text)
    for entry in doc.projects:
        consider(entry.nid, "project", entry.name)
        for bullet in entry.bullets:
            consider(bullet.nid, "bullet", bullet.text)
    for entry in doc.education:
        consider(entry.nid, "education", f"{entry.degree} {entry.institution}")
    for group in doc.skills:
        for item in group.items:
            consider(item.nid, "skill", item.text)
    for section in doc.custom:
        for item in section.items:
            consider(item.nid, "custom", item.text)
    # Text the user placed by hand. Without this the assistant cannot act on
    # "change the note next to my photo" -- it would search, find nothing, and
    # report that the resume does not contain it.
    for block in doc.blocks:
        for line in block.lines:
            consider(line.nid, "text box", line.text)

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


def _tokens(text: str) -> set[str]:
    return {
        "".join(character for character in word if character.isalnum()).lower()
        for word in text.split()
    } - {""}
