"""Reading Resume-Matcher's ``ResumeData`` shape into a document.

Parsers, and every résumé already in the wild, speak the old shape. This is
where it becomes a ``StudioDoc``, and the only place node ids are created from
nothing.

There was a ``to_resume_data`` beside it, written for render templates that
were meant to consume the old parallel arrays. Nothing ever called it: the
templates render from ``StudioDoc``, and the PDF prints the same page the
browser shows. It went out with the ``GET /{id}/legacy`` route that exposed it.
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
