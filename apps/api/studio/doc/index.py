"""Locating a node by id.

``apply_ops`` needs three things about any nid the model names: does it exist,
what kind is it, and which list does it live in (so it can be removed, moved or
reordered). ``NodeIndex`` answers all three in one pass over the document.

Built fresh per batch rather than cached on the document. A stale index that
silently resolves a removed node is a far worse failure than the microseconds a
rebuild costs on a document this size.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from studio.doc.nodes import NodeId, NodeKind, kind_of
from studio.doc.schema import StudioDoc


@dataclass(frozen=True)
class Location:
    """Where a node sits: the list holding it, and its position in that list.

    ``container`` is the actual mutable list object from the document, so a
    caller can splice directly. ``parent_nid`` is None for a top-level node
    (an experience entry) and set for a nested one (a bullet inside it).
    """

    node: Any
    kind: NodeKind
    container: list[Any] | None
    position: int | None
    parent_nid: NodeId | None
    field: str


class NodeIndex:
    """A by-id view over one document."""

    def __init__(self, doc: StudioDoc) -> None:
        self._by_id: dict[NodeId, Location] = {}
        self._build(doc)

    def _add(
        self,
        node: Any,
        *,
        container: list[Any] | None,
        position: int | None,
        parent_nid: NodeId | None,
        field: str,
    ) -> None:
        nid = getattr(node, "nid", None)
        if not nid:
            return
        kind = kind_of(nid)
        if kind is None:
            return
        self._by_id[nid] = Location(
            node=node,
            kind=kind,
            container=container,
            position=position,
            parent_nid=parent_nid,
            field=field,
        )

    def _add_bullets(self, owner: Any, bullets: list[Any]) -> None:
        for position, bullet in enumerate(bullets):
            self._add(
                bullet,
                container=bullets,
                position=position,
                parent_nid=owner.nid,
                field="bullets",
            )

    def _build(self, doc: StudioDoc) -> None:
        if doc.summary is not None:
            self._add(
                doc.summary, container=None, position=None, parent_nid=None, field="summary"
            )

        for field, entries in (
            ("experience", doc.experience),
            ("education", doc.education),
            ("projects", doc.projects),
        ):
            for position, entry in enumerate(entries):
                self._add(
                    entry,
                    container=entries,
                    position=position,
                    parent_nid=None,
                    field=field,
                )
                bullets = getattr(entry, "bullets", None)
                if bullets is not None:
                    self._add_bullets(entry, bullets)
                detail = getattr(entry, "detail", None)
                if detail is not None:
                    self._add(
                        detail,
                        container=None,
                        position=None,
                        parent_nid=entry.nid,
                        field="detail",
                    )

        for position, group in enumerate(doc.skills):
            self._add(
                group,
                container=doc.skills,
                position=position,
                parent_nid=None,
                field="skills",
            )
            for item_position, item in enumerate(group.items):
                self._add(
                    item,
                    container=group.items,
                    position=item_position,
                    parent_nid=group.nid,
                    field="items",
                )

        for position, section in enumerate(doc.custom):
            self._add(
                section,
                container=doc.custom,
                position=position,
                parent_nid=None,
                field="custom",
            )
            if section.text is not None:
                self._add(
                    section.text,
                    container=None,
                    position=None,
                    parent_nid=section.nid,
                    field="text",
                )
            for item_position, item in enumerate(section.items):
                self._add(
                    item,
                    container=section.items,
                    position=item_position,
                    parent_nid=section.nid,
                    field="items",
                )
                self._add_bullets(item, item.bullets)
            for string_position, string_item in enumerate(section.strings):
                self._add(
                    string_item,
                    container=section.strings,
                    position=string_position,
                    parent_nid=section.nid,
                    field="strings",
                )

        # Free text blocks. Indexed like any other content so the assistant can
        # find and rewrite them; text it cannot address is text the product
        # cannot help with.
        for position, block in enumerate(doc.blocks):
            self._add(
                block,
                container=doc.blocks,
                position=position,
                parent_nid=None,
                field="blocks",
            )
            for line_position, line in enumerate(block.lines):
                self._add(
                    line,
                    container=block.lines,
                    position=line_position,
                    parent_nid=block.nid,
                    field="lines",
                )

        # Layout. ``parent_nid`` on an element is its page, which is what makes
        # a cross-page move undoable: without it the recorded "before" names no
        # container and the inverse has nowhere to put the element back.
        for position, page in enumerate(doc.pages):
            self._add(
                page,
                container=doc.pages,
                position=position,
                parent_nid=None,
                field="pages",
            )
            for element_position, element in enumerate(page.elements):
                self._add(
                    element,
                    container=page.elements,
                    position=element_position,
                    parent_nid=page.nid,
                    field="elements",
                )

    def get(self, nid: NodeId) -> Location | None:
        return self._by_id.get(nid)

    def __contains__(self, nid: object) -> bool:
        return nid in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def ids(self) -> list[NodeId]:
        return list(self._by_id)

    # No ``duplicates()`` here, deliberately. The index deduplicates by
    # construction -- ``_add`` writes into a dict keyed by nid -- so it is
    # structurally incapable of reporting a collision, and a method that
    # returned an empty list forever would read like a check while being none.
    # The real one is ``apply.py``'s ``_first_duplicate``, which walks the
    # document rather than the index and is checked as a post-batch invariant.

    def nearest(self, nid: NodeId, limit: int = 5) -> list[tuple[NodeId, str]]:
        """Ids of the same kind, with their text — for an ``unknown_node`` reply.

        Returning candidates rather than a bare error is what lets a weak model
        self-correct instead of giving up: it is an inline ``find_text``.
        """
        kind = kind_of(nid)
        out: list[tuple[NodeId, str]] = []
        for candidate, location in self._by_id.items():
            if kind is not None and location.kind is not kind:
                continue
            text = getattr(location.node, "text", None)
            if text is None:
                text = getattr(location.node, "title", "") or getattr(
                    location.node, "name", ""
                )
            out.append((candidate, str(text)[:80]))
            if len(out) >= limit:
                break
        return out
