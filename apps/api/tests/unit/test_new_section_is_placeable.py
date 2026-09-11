"""A section the résumé did not have can be put where it was asked for.

`add_section` makes a custom section -- Certifications, Publications,
Volunteering -- and used to make *only* that: a node in `doc.custom` and
nothing else. The document then held a section the order had never heard of.

Both renderers already cope with that by drawing such a section in a tail after
everything the order accounts for, which is why one always landed at the very
bottom. And because `set_section` rejected a key with no row, it could not be
moved afterwards either: "add Certifications after Education" had nowhere to
write the answer, and neither did "now move it up". A section nobody can place
is a section stuck at the foot of the résumé forever.

So the section now arrives with a row in the order, positioned by `before` /
`after` at the moment it is made, and the displaced sections are renumbered so
nothing collides.
"""

from __future__ import annotations

import pytest

from studio.agent.tools import REGISTRY, ToolError
from studio.doc.apply import OpContext, apply_ops
from studio.doc.autolayout import layout
from studio.doc.schema import (
    DEFAULT_SECTIONS,
    EducationNode,
    ExperienceNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)

AUTHORIZED = OpContext(granted_tiers={"A", "B", "C"})


def a_resume() -> StudioDoc:
    doc = StudioDoc(
        personal=PersonalInfo(name="Alex Morgan"),
        summary=TextNode(nid="sum_aaaaa", text="Ships things."),
        experience=[
            ExperienceNode(nid="exp_aaaaa", title="Engineer", company="Northwind")
        ],
        education=[EducationNode(nid="edu_aaaaa", institution="Tessell", degree="BSc")],
        skills=[
            SkillGroup(nid="sgp_aaaaa", items=[SkillItem(nid="skl_aaaaa", text="Python")])
        ],
        sections=[meta for meta in DEFAULT_SECTIONS if meta.key != "projects"],
    )
    doc.pages = layout(doc)
    return doc


def drawn(doc: StudioDoc) -> list[str]:
    """The order a reader gets, with custom sections named by their key.

    A custom section's frame is bound to its nid -- that is what `autolayout`
    places it by and what the renderer resolves -- so the nid is mapped back to
    the key to make the assertion readable.
    """
    pages = {page.nid: index for index, page in enumerate(doc.pages)}
    by_nid = {section.nid: section.key for section in doc.custom}
    placed = [
        (pages[page.nid], element.rect.y, element.ref)
        for page in doc.pages
        for element in page.elements
        if getattr(element, "ref", None)
    ]
    return [by_nid.get(ref, ref) for _, _, ref in sorted(placed)]


def add(doc: StudioDoc, **args):
    spec = REGISTRY.get("add_section")
    return apply_ops(doc, spec.compile(spec.Args(**args), doc), AUTHORIZED)


class TestAddingASectionWhereItWasAskedFor:
    def test_after_another_section(self) -> None:
        doc = a_resume()

        after, _, rejected = add(
            doc, label="Certifications", items=["IBM Data Analysis"], after="education"
        )

        assert rejected == []
        order = drawn(after)
        assert order.index("education") < order.index("certifications")
        assert order.index("certifications") < order.index("skills")

    def test_before_another_section(self) -> None:
        doc = a_resume()

        after, _, rejected = add(
            doc, label="Publications", items=["A paper"], before="skills"
        )

        assert rejected == []
        order = drawn(after)
        assert order.index("education") < order.index("publications")
        assert order.index("publications") < order.index("skills")

    def test_at_the_top(self) -> None:
        doc = a_resume()

        after, _, _ = add(doc, label="Awards", items=["A prize"], before="summary")

        assert drawn(after).index("awards") < drawn(after).index("summary")

    def test_it_gets_a_row_in_the_order(self) -> None:
        # The row is the whole reason it can be placed at all, and the reason
        # it can be moved again later.
        doc = a_resume()

        after, _, _ = add(
            doc, label="Certifications", items=["IBM"], after="education"
        )

        assert "certifications" in {meta.key for meta in after.sections}

    def test_it_can_be_moved_again_afterwards(self) -> None:
        # `set_section` used to reject the key outright, so a section added at
        # the bottom stayed there for good.
        doc = a_resume()
        doc, _, _ = add(doc, label="Certifications", items=["IBM"], after="education")

        spec = REGISTRY.get("set_section")
        after, _, rejected = apply_ops(
            doc,
            spec.compile(spec.Args(key="certifications", before="experience"), doc),
            AUTHORIZED,
        )

        assert rejected == []
        order = drawn(after)
        assert order.index("certifications") < order.index("experience")

    def test_nothing_shares_a_position(self) -> None:
        doc = a_resume()

        after, _, _ = add(
            doc, label="Certifications", items=["IBM"], after="education"
        )

        orders = [meta.order for meta in after.sections]
        assert len(set(orders)) == len(orders)

    def test_two_new_sections_each_get_their_own_frame(self) -> None:
        # Bound to "custom", one frame drew every custom section as a single
        # lump, so the second had nothing of its own to sit in.
        doc = a_resume()
        doc, _, _ = add(doc, label="Certifications", items=["IBM"], after="education")
        doc, _, rejected = add(doc, label="Publications", items=["A paper"], after="certifications")

        assert rejected == []
        order = drawn(doc)
        assert order.index("certifications") < order.index("publications")
        assert order.index("publications") < order.index("skills")

    def test_with_no_placement_it_still_works(self) -> None:
        # The argument is optional; omitting it must not break the old path.
        doc = a_resume()

        after, _, rejected = add(doc, label="Volunteering", items=["A charity"])

        assert rejected == []
        assert "volunteering" in drawn(after)

    def test_an_unknown_anchor_says_what_there_is(self) -> None:
        doc = a_resume()

        with pytest.raises(ToolError) as caught:
            add(doc, label="Certifications", items=["IBM"], after="publications")

        assert "publications" in str(caught.value)
        assert "education" in str(caught.value)

    def test_before_and_after_together_read_as_between(self) -> None:
        # See the note in test_section_placement: both together is the natural
        # phrasing, and refusing it made the model drop the placement entirely.
        doc = a_resume()

        after, _, rejected = add(
            doc,
            label="Certifications",
            items=["IBM"],
            after="education",
            before="skills",
        )

        assert rejected == []
        order = drawn(after)
        assert order.index("education") < order.index("certifications")
        assert order.index("certifications") < order.index("skills")

    def test_a_duplicate_is_still_refused(self) -> None:
        doc = a_resume()
        doc, _, _ = add(doc, label="Certifications", items=["IBM"], after="education")

        with pytest.raises(ToolError, match="already"):
            add(doc, label="Certifications", items=["Another"], after="education")


class TestTheNodeTheClientMirrorReceives:
    """Every field the renderer reads, not only the ones the server needs.

    The server fills defaults on validation, so a partial node is valid there.
    The *client* mirror splices an `insert_node` payload in verbatim -- which is
    what makes an optimistic edit instant -- so a field the renderer maps over
    and the payload omits arrives as `undefined`.

    `CustomBlock` maps `section.items` unguarded. Omitting it threw
    "section.items is not iterable" in the panel for the whole turn; the section
    then rendered correctly the moment the server's document landed, which is
    the worst shape for a bug to have: real, visible, and self-healing.
    """

    def test_the_payload_carries_what_the_renderer_maps_over(self) -> None:
        doc = a_resume()
        spec = REGISTRY.get("add_section")

        ops = spec.compile(
            spec.Args(label="Certifications", items=["IBM"], after="education"), doc
        )

        node = ops[0].node
        # Mapped over unguarded by CustomBlock.
        assert node["items"] == []
        # Read behind a truthiness check, but the convention is the same.
        assert "text" in node
        assert node["strings"], "the content itself must still be there"


class TestAddressingASectionByItsNodeId:
    """The nid works wherever a section key does.

    The document outline shows a custom section as `[cst_6bs82] Awards`, and
    every other line in it is addressed by nid. `set_section` was the one tool
    keyed by section *key*, so asked to move Awards the model used the only
    identifier it had been shown and was refused:

        set section  X  No section 'cst_6bs82'. This resume has: awards, …

    It recovered only because the error happened to list the real keys. The
    call was reasonable; the distinction was ours and nothing had mentioned it.
    """

    def nid_of(self, doc: StudioDoc, key: str) -> str:
        return next(section.nid for section in doc.custom if section.key == key)

    def test_a_custom_section_can_be_moved_by_nid(self) -> None:
        doc = a_resume()
        doc, _, _ = add(doc, label="Awards", items=["Dean's List"], after="education")
        nid = self.nid_of(doc, "awards")

        spec = REGISTRY.get("set_section")
        after, _, rejected = apply_ops(
            doc, spec.compile(spec.Args(key=nid, after="summary"), doc), AUTHORIZED
        )

        assert rejected == []
        order = drawn(after)
        assert order.index("summary") < order.index("awards")
        assert order.index("awards") < order.index("experience")

    def test_a_nid_works_as_an_anchor_too(self) -> None:
        doc = a_resume()
        doc, _, _ = add(doc, label="Awards", items=["Dean's List"], after="education")
        nid = self.nid_of(doc, "awards")

        spec = REGISTRY.get("set_section")
        after, _, rejected = apply_ops(
            doc, spec.compile(spec.Args(key="skills", after=nid), doc), AUTHORIZED
        )

        assert rejected == []
        order = drawn(after)
        assert order.index("awards") < order.index("skills")

    def test_a_key_still_works(self) -> None:
        # Widening what is accepted must not narrow it.
        doc = a_resume()
        doc, _, _ = add(doc, label="Awards", items=["Dean's List"], after="education")

        spec = REGISTRY.get("set_section")
        after, _, rejected = apply_ops(
            doc, spec.compile(spec.Args(key="awards", after="summary"), doc), AUTHORIZED
        )

        assert rejected == []
        assert drawn(after).index("summary") < drawn(after).index("awards")

    def test_the_outline_shows_the_key_as_well_as_the_nid(self) -> None:
        # The other half: the model should not have to infer the key from a
        # label. Both identifiers, on the line it reads.
        from studio.agent.context import outline

        doc = a_resume()
        doc, _, _ = add(doc, label="Awards", items=["Dean's List"], after="education")
        nid = self.nid_of(doc, "awards")

        text = outline(doc)

        assert nid in text
        assert "section key: awards" in text
