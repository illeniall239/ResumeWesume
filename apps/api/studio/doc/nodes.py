"""Node identity.

Every addressable thing in a resume carries a stable id whose **prefix encodes
its kind**: ``exp_7f3a2``, ``blt_9c21x``, ``skl_4d10p``.

Three properties make this the backbone of the whole editing model:

1. *Stable.* An id survives reordering, template changes and turns. The old
   engine addressed ``workExperience[0].description[1]``; that breaks the moment
   a concurrent insert shifts the index, and it forces the model to synthesise a
   path DSL. An id does neither.
2. *Kind-encoding.* ``apply_ops`` can reject "set a bullet style on a company
   name" from the prefix alone, with no document lookup — and a weak model can
   sanity-check its own reference before emitting it.
3. *Never reused.* A removed id is not recycled, so a stale reference in a
   queued op fails loudly instead of silently hitting an unrelated node.
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from typing import Final

# Crockford-ish base32: no padding, no vowels-that-become-words, case-stable.
_ALPHABET: Final = "0123456789abcdefghjkmnpqrstvwxyz"
_SUFFIX_LEN: Final = 5


class NodeKind(StrEnum):
    """The kinds of node an id can name. The value IS the id prefix."""

    EXPERIENCE = "exp"
    EDUCATION = "edu"
    PROJECT = "prj"
    BULLET = "blt"
    SKILL = "skl"
    SKILL_GROUP = "sgp"
    CUSTOM_SECTION = "cst"
    CUSTOM_ITEM = "cit"
    SUMMARY = "sum"

    # Layout. Content says what the resume says; these say where it sits. They
    # are separate kinds rather than one "element" kind so that a check like
    # "you cannot set a fill colour on a frame" is answerable from the prefix
    # alone, which is the argument the whole id scheme rests on.
    PAGE = "pag"
    FRAME = "frm"
    IMAGE = "img"
    SHAPE = "shp"
    BLOCK = "txb"


NodeId = str


def mint(kind: NodeKind) -> NodeId:
    """Mint a fresh id for ``kind``.

    ~5.5 bytes of entropy (32**5 ≈ 33.5M). Collisions inside one document are
    checked explicitly by the uniqueness invariant in ``apply_ops`` rather than
    being assumed away, so the suffix stays short and readable in prompts —
    which matters when a local model has to echo it back.
    """
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(_SUFFIX_LEN))
    return f"{kind.value}_{suffix}"


def kind_of(nid: NodeId) -> NodeKind | None:
    """Return the kind encoded in ``nid``, or None if it is not a valid id.

    Returns None rather than raising: this is called on untrusted model output
    on the hot path, where an unparseable id is an expected rejection, not an
    exceptional condition.
    """
    prefix, _, suffix = nid.partition("_")
    if not suffix or len(suffix) != _SUFFIX_LEN:
        return None
    if any(character not in _ALPHABET for character in suffix):
        return None
    try:
        return NodeKind(prefix)
    except ValueError:
        return None


def is_valid(nid: NodeId) -> bool:
    """Whether ``nid`` is a well-formed node id of any kind."""
    return kind_of(nid) is not None
