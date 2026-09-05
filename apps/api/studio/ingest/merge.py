"""Assembling parsed sections into a legacy ResumeData payload.

The target is the old flat shape rather than ``StudioDoc`` directly, and that
is deliberate on two counts. ``legacy.from_resume_data`` already mints every
node id, is already tolerant of ragged input, and is already fuzz-tested -- so
aiming at it means ids are minted once, server-side, in the one place that has
ever minted them. And a flat shape with no ids in it is a far easier thing to
ask a small model for than a node-addressed one.

Pure and synchronous: given the same sections it returns the same payload, with
no model, no clock and no I/O anywhere in it.
"""

from __future__ import annotations

from typing import Any

from studio.ingest.contact import Contact
from studio.ingest.schemas import (
    EducationOut,
    ExperienceOut,
    ProjectsOut,
    SummaryOut,
    _Section,
)

# Which legacy ``additional`` list each parsed section feeds.
_SKILL_FIELDS: dict[str, str] = {
    "skills": "technicalSkills",
    "languages": "languages",
    "certifications": "certificationsTraining",
    "awards": "awards",
}

_SECTION_LABELS: dict[str, str] = {
    "summary": "Summary",
    "experience": "Experience",
    "education": "Education",
    "projects": "Projects",
    "skills": "Skills",
}

# The order a resume falls back to when nothing was found in the source. Same
# order as ``DEFAULT_SECTIONS``.
_FALLBACK_ORDER = ["summary", "experience", "education", "projects", "skills"]


def _bullets(values: list[str]) -> tuple[list[str], list[str]]:
    """Build the parallel arrays together, so they cannot disagree.

    The same argument ``legacy._split_bullets`` makes on the way out: one
    iteration produces both, so a desync is not a bug that can be introduced
    later, it is a shape that cannot be expressed.
    """
    return list(values), ["bullet"] * len(values)


def _section_meta(
    order: list[str],
    populated: set[str],
    custom: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Section order and visibility, taken from the source document.

    Following the order the headings actually appeared in means an imported
    resume renders the way the person wrote it, rather than being silently
    reshuffled into our default on the way in -- which would read as the
    importer having rewritten their document.

    That applies to a section we had no schema for just as much as to one we
    did: Publications sitting between Experience and Education stays there. A
    custom section is named by its own heading, so it is its own key.
    """
    labels = {**_SECTION_LABELS, **{key: key for key in custom or {}}}

    seen: list[str] = []
    for key in order:
        if key in labels and key not in seen:
            seen.append(key)
    for key in _FALLBACK_ORDER:
        if key not in seen:
            seen.append(key)

    return [
        {
            "key": key,
            "displayName": labels[key],
            "isVisible": key in populated,
            "order": index,
        }
        for index, key in enumerate(seen)
    ]


def to_resume_data(
    *,
    contact: Contact,
    parts: dict[str, _Section | None],
    skills: dict[str, list[str]],
    order: list[str],
    custom: list[tuple[str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build the legacy payload ``from_resume_data`` consumes.

    ``parts`` maps a section key to its parsed schema, or to ``None`` where
    that section failed. A ``None`` is not an error here: it lands as an empty
    section and everything else is still imported, which is the containment
    promise the pipeline makes to the user.

    ``custom`` carries the sections we have no schema for, in the order they
    appeared, each as its own heading and the lines beneath it. They are keyed
    by that heading, which is the résumé's word rather than ours.
    """
    summary_part = parts.get("summary")
    summary = (
        summary_part.summary if isinstance(summary_part, SummaryOut) else ""
    )

    experience_part = parts.get("experience")
    work: list[dict[str, Any]] = []
    if isinstance(experience_part, ExperienceOut):
        for entry in experience_part.entries:
            description, styles = _bullets(entry.bullets)
            work.append(
                {
                    "title": entry.title,
                    "company": entry.company,
                    "location": entry.location,
                    "years": entry.years,
                    "description": description,
                    "descriptionStyles": styles,
                }
            )

    education_part = parts.get("education")
    education: list[dict[str, Any]] = []
    if isinstance(education_part, EducationOut):
        education = [
            {
                "institution": entry.institution,
                "degree": entry.degree,
                "years": entry.years,
                "description": entry.description,
            }
            for entry in education_part.entries
        ]

    projects_part = parts.get("projects")
    projects: list[dict[str, Any]] = []
    if isinstance(projects_part, ProjectsOut):
        for entry in projects_part.entries:
            description, styles = _bullets(entry.bullets)
            projects.append(
                {
                    "name": entry.name,
                    "role": entry.role,
                    "years": entry.years,
                    "github": entry.github,
                    "website": entry.website,
                    "description": description,
                    "descriptionStyles": styles,
                }
            )

    additional = {
        field: skills.get(key, [])
        for key, field in _SKILL_FIELDS.items()
        if skills.get(key)
    }

    custom_sections: dict[str, dict[str, Any]] = {}
    for heading, payload in custom or []:
        # Two sections under one heading is a résumé with a repeated word, not
        # a reason to drop the second one.
        key = heading
        suffix = 2
        while key in custom_sections:
            key = f"{heading} ({suffix})"
            suffix += 1
        custom_sections[key] = payload

    populated = {
        key
        for key, has in (
            ("summary", bool(summary)),
            ("experience", bool(work)),
            ("education", bool(education)),
            ("projects", bool(projects)),
            ("skills", any(additional.values())),
        )
        if has
    }
    populated.update(custom_sections)

    payload: dict[str, Any] = {
        "personalInfo": contact.as_personal_info(),
        "summary": summary,
        "workExperience": work,
        "education": education,
        "personalProjects": projects,
        "additional": additional,
        "sectionMeta": _section_meta(order, populated, custom_sections),
    }
    if custom_sections:
        payload["customSections"] = custom_sections
    return payload


def title_for(contact: Contact, filename: str) -> str:
    """What to call the imported document.

    The person's name if we found one, because that is what they will look for
    in a list of documents. The filename otherwise -- never "Untitled", which
    tells them nothing about which upload this was.
    """
    if contact.name:
        return contact.name
    stem = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
    return stem or "Imported resume"
