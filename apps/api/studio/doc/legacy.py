"""Conversion to and from Resume-Matcher's ``ResumeData`` shape.

Two reasons this exists rather than adopting the old shape outright:

*Import.* Parsers, and every resume already in the wild, speak the old shape.
Minting ids on the way in is the only place ids need to be created from nothing.

*Export.* The nine ported render templates and the PDF route consume
``description``/``descriptionStyles`` parallel arrays. Rebuilding them here — at
the boundary, from data that cannot be desynced — means the invariant that used
to be defended in three separate layers is now enforced by construction and
reconstructed once, at the edge.
"""

from __future__ import annotations

from typing import Any

from studio.doc.nodes import NodeKind, mint
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    CustomItemNode,
    CustomSectionNode,
    EducationNode,
    ExperienceNode,
    PersonalInfo,
    ProjectNode,
    SectionMeta,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

# The four flat lists the old ``additional`` object carried, and the group
# labels they become.
_SKILL_GROUPS: list[tuple[str, str, str]] = [
    ("technicalSkills", "technical", "Technical Skills"),
    ("languages", "languages", "Languages"),
    ("certificationsTraining", "certifications", "Certifications"),
    ("awards", "awards", "Awards"),
]


def _text_nodes(raw: Any, styles: Any = None) -> list[TextNode]:
    """Rebuild bullets from the old parallel arrays.

    This is the *only* place the positional pairing is interpreted, and it is
    tolerant by design: a styles array shorter than the descriptions (the common
    desync) simply defaults the remainder to "bullet" rather than failing.
    """
    if not isinstance(raw, list):
        return []
    style_list = styles if isinstance(styles, list) else []
    nodes: list[TextNode] = []
    for position, item in enumerate(raw):
        text = str(item or "").strip()
        if not text:
            continue
        style = style_list[position] if position < len(style_list) else "bullet"
        nodes.append(
            TextNode(nid=mint(NodeKind.BULLET), text=text, style=style)
        )
    return nodes


def _strings(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item or "").strip()]


def from_resume_data(data: dict[str, Any]) -> StudioDoc:
    """Build a StudioDoc from the old shape, minting ids as it goes."""
    personal_raw = data.get("personalInfo")
    personal = PersonalInfo.model_validate(
        personal_raw if isinstance(personal_raw, dict) else {}
    )

    summary_text = str(data.get("summary") or "").strip()
    summary = (
        TextNode(nid=mint(NodeKind.SUMMARY), text=summary_text, style="plain")
        if summary_text
        else None
    )

    experience = [
        ExperienceNode(
            nid=mint(NodeKind.EXPERIENCE),
            title=entry.get("title", ""),
            company=entry.get("company", ""),
            location=entry.get("location"),
            years=entry.get("years", ""),
            bullets=_text_nodes(entry.get("description"), entry.get("descriptionStyles")),
        )
        for entry in data.get("workExperience") or []
        if isinstance(entry, dict)
    ]

    education: list[EducationNode] = []
    for entry in data.get("education") or []:
        if not isinstance(entry, dict):
            continue
        detail_text = str(entry.get("description") or "").strip()
        education.append(
            EducationNode(
                nid=mint(NodeKind.EDUCATION),
                institution=entry.get("institution", ""),
                degree=entry.get("degree", ""),
                years=entry.get("years", ""),
                detail=(
                    TextNode(nid=mint(NodeKind.BULLET), text=detail_text, style="plain")
                    if detail_text
                    else None
                ),
            )
        )

    projects = [
        ProjectNode(
            nid=mint(NodeKind.PROJECT),
            name=entry.get("name", ""),
            role=entry.get("role", ""),
            years=entry.get("years", ""),
            github=entry.get("github"),
            website=entry.get("website"),
            bullets=_text_nodes(entry.get("description"), entry.get("descriptionStyles")),
        )
        for entry in data.get("personalProjects") or []
        if isinstance(entry, dict)
    ]

    additional = data.get("additional")
    additional = additional if isinstance(additional, dict) else {}
    skills = [
        SkillGroup(
            nid=mint(NodeKind.SKILL_GROUP),
            key=key,
            label=label,
            items=[
                SkillItem(nid=mint(NodeKind.SKILL), text=text)
                for text in _strings(additional.get(field))
            ],
        )
        for field, key, label in _SKILL_GROUPS
        if _strings(additional.get(field))
    ]

    custom: list[CustomSectionNode] = []
    raw_custom = data.get("customSections")
    if isinstance(raw_custom, dict):
        for key, section in raw_custom.items():
            if not isinstance(section, dict):
                continue
            kind = section.get("sectionType", "itemList")
            text_value = str(section.get("text") or "").strip()
            custom.append(
                CustomSectionNode(
                    nid=mint(NodeKind.CUSTOM_SECTION),
                    key=key,
                    label=key,
                    kind=kind if kind in {"text", "itemList", "stringList"} else "itemList",
                    text=(
                        TextNode(nid=mint(NodeKind.BULLET), text=text_value, style="plain")
                        if text_value
                        else None
                    ),
                    items=[
                        CustomItemNode(
                            nid=mint(NodeKind.CUSTOM_ITEM),
                            title=item.get("title", ""),
                            subtitle=item.get("subtitle"),
                            location=item.get("location"),
                            years=item.get("years", ""),
                            bullets=_text_nodes(
                                item.get("description"), item.get("descriptionStyles")
                            ),
                        )
                        for item in section.get("items") or []
                        if isinstance(item, dict)
                    ],
                    strings=[
                        SkillItem(nid=mint(NodeKind.SKILL), text=text)
                        for text in _strings(section.get("strings"))
                    ],
                )
            )

    sections = [
        SectionMeta(
            key=meta.get("key", ""),
            label=meta.get("displayName", ""),
            visible=bool(meta.get("isVisible", True)),
            order=int(meta.get("order", 0)),
        )
        for meta in data.get("sectionMeta") or []
        if isinstance(meta, dict) and meta.get("key")
    ] or list(DEFAULT_SECTIONS)

    return StudioDoc(
        personal=personal,
        summary=summary,
        experience=experience,
        education=education,
        projects=projects,
        skills=skills,
        custom=custom,
        sections=sections,
    )


def _split_bullets(bullets: list[TextNode]) -> tuple[list[str], list[str]]:
    """Rebuild the parallel arrays. Aligned by construction: same iteration."""
    return [b.text for b in bullets], [b.style for b in bullets]


def to_resume_data(doc: StudioDoc) -> dict[str, Any]:
    """Render the old shape, for the ported templates and the PDF route."""
    experience = []
    for position, entry in enumerate(doc.experience, start=1):
        description, styles = _split_bullets(entry.bullets)
        experience.append(
            {
                "id": position,
                "title": entry.title,
                "company": entry.company,
                "location": entry.location,
                "years": entry.years,
                "description": description,
                "descriptionStyles": styles,
            }
        )

    projects = []
    for position, entry in enumerate(doc.projects, start=1):
        description, styles = _split_bullets(entry.bullets)
        projects.append(
            {
                "id": position,
                "name": entry.name,
                "role": entry.role,
                "years": entry.years,
                "github": entry.github,
                "website": entry.website,
                "description": description,
                "descriptionStyles": styles,
            }
        )

    education = [
        {
            "id": position,
            "institution": entry.institution,
            "degree": entry.degree,
            "years": entry.years,
            "description": entry.detail.text if entry.detail else "",
        }
        for position, entry in enumerate(doc.education, start=1)
    ]

    by_key = {group.key: [item.text for item in group.items] for group in doc.skills}
    additional = {
        "technicalSkills": by_key.get("technical", []),
        "languages": by_key.get("languages", []),
        "certificationsTraining": by_key.get("certifications", []),
        "awards": by_key.get("awards", []),
    }

    # The way back out for a section we have no schema for. Without this the
    # legacy shape was write-only for custom sections: `from_resume_data` read
    # them and nothing ever wrote them, so any caller round-tripping a document
    # through this payload dropped somebody's Publications on the floor.
    custom_sections = {
        section.key: {
            "sectionType": section.kind,
            "text": section.text.text if section.text else "",
            "items": [
                {
                    "title": item.title,
                    "subtitle": item.subtitle,
                    "location": item.location,
                    "years": item.years,
                    **dict(
                        zip(
                            ("description", "descriptionStyles"),
                            _split_bullets(item.bullets),
                        )
                    ),
                }
                for item in section.items
            ],
            "strings": [entry.text for entry in section.strings],
        }
        for section in doc.custom
    }

    return {
        "personalInfo": doc.personal.model_dump(),
        "summary": doc.summary.text if doc.summary else "",
        "workExperience": experience,
        "education": education,
        "personalProjects": projects,
        "additional": additional,
        "customSections": custom_sections,
        "sectionMeta": [
            {
                "id": meta.key,
                "key": meta.key,
                "displayName": meta.label,
                "isVisible": meta.visible,
                "order": meta.order,
            }
            for meta in doc.sections
        ],
    }
