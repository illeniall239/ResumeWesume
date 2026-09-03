"""Keeping the assistant's punctuation out of the résumé.

An em dash is the tell. Not because it is wrong -- it is perfectly good
punctuation, and a person who types one means it -- but because a résumé full
of them reads as machine-written to anyone who has seen a few, and the whole
point of this application is a document that reads as the person's own.

So this rewrites what the *assistant* produces, and only that. Somebody typing
into their own résumé is left alone: the same character arriving from the
keyboard is a choice, and silently rewriting it would be the application
disagreeing with its user about their own writing.

The replacement is chosen by what the dash was doing:

    Rebuilt the ledger -- and cut latency        parenthetical -> comma
    2021 -- Present                              a range      -> hyphen
    Python -- Go -- Rust                         a separator  -> comma

Applied at the one point every tool passes through, not per tool, so a tool
added next month is covered without anyone remembering this file exists.
"""

from __future__ import annotations

import re
from typing import Any

#: The dashes worth catching. The en dash is here too: models reach for it in
#: date ranges, and it is the same tell in a shorter coat.
_DASHES = "—–"

#: A dash between two numbers, or between month-year style words, is a range,
#: and a range wants a hyphen: "Sep 2025 - Present", "2021 - 2024".
_RANGE = re.compile(
    rf"(?<=[\w)])\s*[{_DASHES}]\s*(?=[\w(])"
)

#: A dash with a space on at least one side is doing a comma's job -- setting a
#: clause apart -- so it becomes a comma. Matched first, because the range rule
#: above would otherwise claim "latency -- and" as a range.
_PARENTHETICAL = re.compile(rf"\s+[{_DASHES}]+\s+")

#: A dash glued to the words on both sides, which is the typographic style for
#: a parenthetical: "ledger—and". Also a comma -- unless it joins two
#: endpoints, which is a closed-up range and wants a hyphen.
_TIGHT = re.compile(rf"(?<=\w)[{_DASHES}]+(?=\w)")


def _looks_like_a_range(before: str, after: str) -> bool:
    """Whether a dash joins two endpoints rather than two clauses.

    Deliberately narrow. "2021 - Present" and "Sep 2025 - Present" are ranges;
    "the ledger - and the settlement path" is not, and turning that into a
    hyphen would leave a sentence that reads as a typo.
    """
    tail = before.rsplit(None, 1)[-1] if before.split() else ""
    head = after.split(None, 1)[0] if after.split() else ""
    if not tail or not head:
        return False

    def endpoint(word: str) -> bool:
        word = word.strip(",.;:()")
        return bool(word) and (
            word.isdigit()
            or word.lower() in {"present", "current", "now", "date", "today"}
        )

    return endpoint(tail) and endpoint(head)


def clean(text: str) -> str:
    """Replace em and en dashes with punctuation a person would have typed.

    Idempotent, and a no-op for text that contains none -- which is most text,
    so the common path costs one ``in`` check.
    """
    if not text or not any(dash in text for dash in _DASHES):
        return text

    def replace(match: re.Match[str]) -> str:
        before = text[: match.start()]
        after = text[match.end() :]
        return " - " if _looks_like_a_range(before, after) else ", "

    cleaned = _PARENTHETICAL.sub(replace, text)

    def tight(source: str):
        """A dash with no spaces: "ledger—and", or a closed-up "2021—2024".

        Bound to the string being scanned rather than closing over ``cleaned``,
        which the substitution reassigns -- a closure over the name reads the
        pre-substitution text and answers the range question against the wrong
        offsets.
        """

        def decide(match: re.Match[str]) -> str:
            before = source[: match.start()]
            after = source[match.end() :]
            return "-" if _looks_like_a_range(before, after) else ", "

        return decide

    cleaned = _TIGHT.sub(tight(cleaned), cleaned)
    # Anything left has punctuation or nothing beside it rather than a word.
    cleaned = re.sub(rf"[{_DASHES}]", "-", cleaned)

    # A comma or hyphen pushed against punctuation that already ends the clause
    # reads as a slip, and the substitutions above cannot see what follows.
    # Repeated until it settles: "— ," becomes "- ," becomes ",", and a single
    # pass leaves the middle step on the page.
    while True:
        collapsed = re.sub(r"[,\-]\s*([,.;:!?])", r"\1", cleaned)
        if collapsed == cleaned:
            break
        cleaned = collapsed

    return re.sub(r"\s+([.,;:!?])", r"\1", cleaned).strip()


#: Op fields that carry words a person will read. ``expect`` is deliberately
#: absent: it is a guard against a stale edit and must match the document
#: byte for byte, so cleaning it would make every guarded rewrite fail.
_TEXT_FIELDS = ("value",)

#: Keys inside an inserted node that hold prose. A node arrives as a plain dict
#: -- a whole job, with its bullets -- so the walk has to reach into it.
_NODE_FIELDS = ("text", "title", "company", "years", "location", "label", "degree",
                "institution", "name", "summary")


def clean_op(op: Any) -> Any:
    """Return ``op`` with the assistant's dashes replaced.

    Applied to compiled ops rather than to each tool's arguments, because every
    tool funnels through one call site on its way to the document -- so a tool
    added next month is covered without anyone remembering this file exists.

    Mutates nothing: a copy is returned when something changed, and the original
    when nothing did.
    """
    changes: dict[str, Any] = {}

    for field in _TEXT_FIELDS:
        current = getattr(op, field, None)
        if isinstance(current, str):
            cleaned = clean(current)
            if cleaned != current:
                changes[field] = cleaned

    node = getattr(op, "node", None)
    if isinstance(node, dict):
        cleaned_node = _clean_node(node)
        if cleaned_node is not node:
            changes["node"] = cleaned_node

    return op.model_copy(update=changes) if changes else op


def _clean_node(node: dict[str, Any]) -> dict[str, Any]:
    """A node dict, with its prose cleaned however deeply it nests."""
    out: dict[str, Any] = {}
    changed = False

    for key, value in node.items():
        if key in _NODE_FIELDS and isinstance(value, str):
            cleaned = clean(value)
            out[key] = cleaned
            changed = changed or cleaned != value
        elif isinstance(value, list):
            items = [
                _clean_node(item) if isinstance(item, dict) else item for item in value
            ]
            out[key] = items
            changed = changed or any(a is not b for a, b in zip(items, value))
        elif isinstance(value, dict):
            nested = _clean_node(value)
            out[key] = nested
            changed = changed or nested is not value
        else:
            out[key] = value

    return out if changed else node
