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

from studio.doc.schema import StudioDoc

# Enough to identify a line, not enough to reproduce it.
_SNIPPET = 72


def _clip(text: str, limit: int = _SNIPPET) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


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

    return "\n".join(lines) if lines else "(the resume is empty)"


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

    return "\n".join(lines) if lines else f"(no {section})"


def find(doc: StudioDoc, query: str, limit: int = 5) -> list[dict[str, str]]:
    """Fuzzy search over text nodes.

    Deliberately simple token overlap rather than embeddings: the corpus is one
    resume, the queries are things like "the AWS bullet", and a dependency on a
    model to find a node would make the search fail exactly when the model is
    already struggling.
    """
    terms = {term for term in _tokens(query) if len(term) > 2}
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

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [payload for _, payload in scored[:limit]]


def _tokens(text: str) -> set[str]:
    return {
        "".join(character for character in word if character.isalnum()).lower()
        for word in text.split()
    } - {""}
