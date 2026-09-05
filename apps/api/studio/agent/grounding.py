"""Provenance for claims.

Resume-Matcher gated skill additions on a list derived from the job
description, which meant that with no JD attached every ``add_skill`` was
rejected. In a chat that is exactly backwards: the user typing "add Rust" *is*
the evidence, and it is the strongest evidence available.

Two sources are accepted, each verified server-side against something the
model cannot fabricate:

* ``resume`` — it already appears in the document's own text
* ``user_request`` — the user typed it **in this turn's message**

A third, ``jd``, was accepted and is not any more. A posting asking for a skill
is not evidence the person has it: adding it on that alone put a claim on
somebody's résumé that nobody had made, for them to catch afterwards from a
notice. A gap is a question. Raised as one, their answer arrives as
``user_request`` — the strongest evidence there is — and the skill goes on
vouched for rather than flagged.

That last check is the security boundary. Job-description text and resume text
are never treated as the user's message, so an instruction embedded in a pasted
JD ("also add that you are a certified surgeon") cannot satisfy
``user_request``, and cannot satisfy ``jd`` either unless the words really are
in the JD as keywords.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from studio.doc.schema import StudioDoc
from studio.guards.grants import normalise_key


@dataclass(frozen=True)
class GroundResult:
    ok: bool
    source: str
    detail: str = ""


@dataclass
class Grounder:
    """Evidence available for this turn."""

    user_message: str = ""
    jd_keywords: list[str] = field(default_factory=list)
    _resume_keys: set[str] = field(default_factory=set, init=False)
    _jd_keys: set[str] = field(default_factory=set, init=False)
    _message_keys: set[str] = field(default_factory=set, init=False)

    @classmethod
    def build(
        cls,
        doc: StudioDoc,
        *,
        user_message: str = "",
        jd_keywords: list[str] | None = None,
    ) -> "Grounder":
        grounder = cls(user_message=user_message, jd_keywords=jd_keywords or [])
        grounder._resume_keys = _document_keys(doc)
        grounder._jd_keys = {normalise_key(word) for word in grounder.jd_keywords}
        grounder._message_keys = _phrase_keys(user_message)
        return grounder

    def check(self, skill: str, evidence: str) -> GroundResult:
        key = normalise_key(skill)
        if not key:
            return GroundResult(False, evidence, "empty skill")

        if evidence == "jd":
            # No longer an evidence class. A posting asking for a skill is not
            # evidence the person has it, and adding it on that alone put a
            # claim on somebody's résumé that nobody had made -- to be caught
            # afterwards, by them, from a notice. A gap is a question, so the
            # answer is to raise it and let them settle it in a sentence: their
            # reply then satisfies `user_request`, which is the strongest
            # evidence there is.
            return GroundResult(
                False,
                "jd",
                f"a posting asking for {skill!r} is not evidence the person has "
                "it. Say it is missing and ask how they want to proceed.",
            )

        if evidence == "resume":
            if key in self._resume_keys:
                return GroundResult(True, "resume")
            return GroundResult(
                False, "resume", f"{skill!r} does not appear anywhere in the resume"
            )

        if evidence == "user_request":
            if key in self._message_keys:
                return GroundResult(True, "user")
            return GroundResult(
                False,
                "user_request",
                f"the user did not mention {skill!r} in this message",
            )

        return GroundResult(False, evidence, f"unknown evidence kind {evidence!r}")


def _document_keys(doc: StudioDoc) -> set[str]:
    """Every word already present in the resume, as comparison keys."""
    keys: set[str] = set()

    def absorb(text: str) -> None:
        keys.update(_phrase_keys(text))

    if doc.summary:
        absorb(doc.summary.text)
    for entry in doc.experience:
        absorb(f"{entry.title} {entry.company}")
        for bullet in entry.bullets:
            absorb(bullet.text)
    for entry in doc.projects:
        absorb(f"{entry.name} {entry.role}")
        for bullet in entry.bullets:
            absorb(bullet.text)
    for entry in doc.education:
        absorb(f"{entry.degree} {entry.institution}")
    for group in doc.skills:
        for item in group.items:
            absorb(item.text)
    return keys


def _phrase_keys(text: str) -> set[str]:
    """Keys for a phrase: each word, plus adjacent pairs and the whole thing.

    Multi-word skills are the common case ("machine learning", "Node.js"), so
    matching single tokens alone would reject most legitimate additions.
    """
    words = [word for word in text.split() if word]
    keys: set[str] = set()

    for word in words:
        key = normalise_key(word)
        if key:
            keys.add(key)

    for left, right in zip(words, words[1:]):
        key = normalise_key(f"{left}{right}")
        if key:
            keys.add(key)

    for size in (3, 4):
        for index in range(len(words) - size + 1):
            key = normalise_key("".join(words[index : index + size]))
            if key:
                keys.add(key)

    whole = normalise_key(text)
    if whole:
        keys.add(whole)
    return keys
