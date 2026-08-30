"""Intent tracking.

The safety nets inherited from Resume-Matcher were written for a one-shot
"improve" call, where the model returned a whole rewritten document and any
change to identity was by definition unrequested. They are absolute:
``_preserve_personal_info`` overwrites PII unconditionally, and
``_preserve_original_skills`` re-appends anything removed.

Absolute is wrong for conversation. "Change my email to X" would silently
revert; "remove Python" would come back. The guarantee is still right, but its
*scope* has to be explicit.

An ``IntentLedger`` records what the user actually authorised this turn. Grants
are minted from **validated tool arguments**, never from parsing the user's
prose, because prose is exactly what an injected instruction can imitate. A
guard then reverts drift that no grant covers, and leaves the rest alone.

Grants are turn-scoped and deliberately have no memory: "you let me edit your
email last turn" must not authorise this turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class GrantScope(StrEnum):
    PERSONAL_FIELD = "personal_field"  # ref = field name
    ENTRY_ADD = "entry_add"  # ref = nid
    ENTRY_REMOVE = "entry_remove"  # ref = nid
    ENTRY_FIELD = "entry_field"  # ref = "nid.field"
    SKILL_ADD = "skill_add"  # ref = normalised skill key
    SKILL_REMOVE = "skill_remove"  # ref = normalised skill key
    TEXT = "text"  # ref = nid
    SECTION = "section"  # ref = section key
    CUSTOM_ITEM = "custom_item"  # ref = nid


@dataclass(frozen=True)
class IntentGrant:
    scope: GrantScope
    ref: str
    origin: str = "tool_call"  # tool_call | user_direct | consent
    call_id: str | None = None


def normalise_key(text: str) -> str:
    """Fold a skill to a comparison key.

    Skills are matched case- and punctuation-insensitively so "Node.js",
    "nodejs" and "Node JS" are one item. Without this a guard would treat a
    reformatting as a removal plus an addition.
    """
    return "".join(
        character for character in text.lower() if character.isalnum()
    )


@dataclass
class IntentLedger:
    """What this turn is allowed to have changed."""

    turn_id: str = ""
    _grants: set[tuple[str, str]] = field(default_factory=set, init=False)
    _records: list[IntentGrant] = field(default_factory=list, init=False)

    def grant(self, grant: IntentGrant) -> None:
        self._grants.add((str(grant.scope), grant.ref))
        self._records.append(grant)

    def grant_many(self, grants: list[IntentGrant]) -> None:
        for item in grants:
            self.grant(item)

    def covers(self, scope: GrantScope, ref: str) -> bool:
        return (str(scope), ref) in self._grants

    @property
    def records(self) -> list[IntentGrant]:
        return list(self._records)

    def __len__(self) -> int:
        return len(self._grants)
