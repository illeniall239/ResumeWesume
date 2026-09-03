"""Post-batch drift guards.

Defence in depth, and their role has changed from the engine they came from.

In Resume-Matcher the safety nets were the *primary* defence, because the model
returned a whole rewritten document and anything could have changed. Here tools
are the only mutation path and ``apply_ops`` already gates every op, so these
run as **invariant assertions**: in normal operation they fire zero times, and a
firing guard means either a bug in the gates or an attempt to get around them.
Both deserve to be logged loudly and surfaced to the client, which is why
``drift_suppressed`` is an event type rather than a silent correction.

Each guard reverts only drift that no grant covers. The quality guard never
reverts: word growth and invented metrics are advisory signals, and a guard that
silently rewrites the model's prose would be indistinguishable from the model
being bad at its job.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

from studio.doc.schema import StudioDoc
from studio.guards.grants import GrantScope, IntentLedger, normalise_key

logger = logging.getLogger(__name__)

# Numbers a model likes to invent: percentages, multipliers, money.
_METRIC = re.compile(r"\d+\s?%|\d+x\b|[$£€]\s?\d[\d,.]*")

# Growth beyond this ratio usually means the model padded rather than tightened.
_WORD_GROWTH_LIMIT = 1.8


@dataclass
class DriftReport:
    guard: str
    scope: str
    ref: str
    detail: str = ""
    reverted: bool = True


class DriftGuard(Protocol):
    name: str

    def check(
        self, before: StudioDoc, after: StudioDoc, ledger: IntentLedger
    ) -> tuple[StudioDoc, list[DriftReport]]:
        ...


class PersonalInfoGuard:
    """Identity may only change where a grant says so.

    Replaces an unconditional overwrite. The old behaviour made "change my
    email" impossible; reverting only ungranted fields keeps the protection and
    restores the capability.
    """

    name = "personal_info"

    def check(
        self, before: StudioDoc, after: StudioDoc, ledger: IntentLedger
    ) -> tuple[StudioDoc, list[DriftReport]]:
        reports: list[DriftReport] = []
        result = after.model_copy(deep=True)

        for field_name in type(before.personal).model_fields:
            original = getattr(before.personal, field_name)
            current = getattr(result.personal, field_name)
            if original == current:
                continue
            if ledger.covers(GrantScope.PERSONAL_FIELD, field_name):
                continue
            setattr(result.personal, field_name, original)
            reports.append(
                DriftReport(
                    guard=self.name,
                    scope="personal",
                    ref=field_name,
                    detail=f"reverted unrequested change to {field_name}",
                )
            )
        return result, reports


class EntryIntegrityGuard:
    """Entries may not appear, vanish or change identity without a grant.

    Covers the failure that actually loses a user: a job silently deleted, or an
    employer name quietly rewritten into something they never worked for.
    """

    name = "entry_integrity"
    _IDENTITY = {
        "experience": ("company", "title"),
        "education": ("institution", "degree"),
        "projects": ("name",),
    }

    def check(
        self, before: StudioDoc, after: StudioDoc, ledger: IntentLedger
    ) -> tuple[StudioDoc, list[DriftReport]]:
        reports: list[DriftReport] = []
        result = after.model_copy(deep=True)

        for section, identity_fields in self._IDENTITY.items():
            original_entries = {entry.nid: entry for entry in getattr(before, section)}
            current_entries = list(getattr(result, section))
            current_by_id = {entry.nid: entry for entry in current_entries}

            # Something was removed without permission: put it back where it was.
            for nid, entry in original_entries.items():
                if nid in current_by_id:
                    continue
                if ledger.covers(GrantScope.ENTRY_REMOVE, nid):
                    continue
                position = min(
                    list(original_entries).index(nid), len(current_entries)
                )
                current_entries.insert(position, entry.model_copy(deep=True))
                _restore_frames(before, result, nid)
                reports.append(
                    DriftReport(
                        guard=self.name,
                        scope=section,
                        ref=nid,
                        detail="restored an entry removed without a request",
                    )
                )

            # Something appeared without permission.
            for entry in list(current_entries):
                if entry.nid in original_entries:
                    continue
                if ledger.covers(GrantScope.ENTRY_ADD, entry.nid):
                    continue
                current_entries.remove(entry)
                reports.append(
                    DriftReport(
                        guard=self.name,
                        scope=section,
                        ref=entry.nid,
                        detail="removed an entry added without a request",
                    )
                )

            # Identity fields drifted.
            for entry in current_entries:
                original = original_entries.get(entry.nid)
                if original is None:
                    continue
                for field_name in identity_fields:
                    if getattr(original, field_name) == getattr(entry, field_name):
                        continue
                    if ledger.covers(
                        GrantScope.ENTRY_FIELD, f"{entry.nid}.{field_name}"
                    ):
                        continue
                    setattr(entry, field_name, getattr(original, field_name))
                    reports.append(
                        DriftReport(
                            guard=self.name,
                            scope=section,
                            ref=f"{entry.nid}.{field_name}",
                            detail=f"reverted unrequested change to {field_name}",
                        )
                    )

            setattr(result, section, current_entries)

        return result, reports


class SkillsGuard:
    """Skills may not be dropped or invented without a grant.

    The old net re-appended everything removed, so "remove Python" was undone.
    Scoping to grants keeps the no-silent-loss guarantee while letting an
    explicit removal succeed.
    """

    name = "skills"

    def check(
        self, before: StudioDoc, after: StudioDoc, ledger: IntentLedger
    ) -> tuple[StudioDoc, list[DriftReport]]:
        reports: list[DriftReport] = []
        result = after.model_copy(deep=True)

        before_groups = {group.key: group for group in before.skills}
        after_groups = {group.key: group for group in result.skills}

        for key, original_group in before_groups.items():
            current_group = after_groups.get(key)
            if current_group is None:
                continue

            current_keys = {normalise_key(item.text) for item in current_group.items}

            for item in original_group.items:
                item_key = normalise_key(item.text)
                if item_key in current_keys:
                    continue
                # A blank is not a skill. `starter_doc` ships one as the slot
                # to click into, and filling it removes the empty key from the
                # group -- which read here as a skill dropped without a
                # request, so the guard restored the blank it had just been
                # rid of. A tailored resume ended with a dot and no words
                # after it, every time, and the restore left no op behind to
                # explain why.
                if not item_key:
                    continue
                if ledger.covers(GrantScope.SKILL_REMOVE, item_key):
                    continue
                current_group.items.append(item.model_copy(deep=True))
                reports.append(
                    DriftReport(
                        guard=self.name,
                        scope=key,
                        ref=item.text,
                        detail="restored a skill dropped without a request",
                    )
                )

            original_keys = {normalise_key(item.text) for item in original_group.items}
            original_nids = {item.nid for item in original_group.items}
            for item in list(current_group.items):
                item_key = normalise_key(item.text)
                if item_key in original_keys:
                    continue
                if ledger.covers(GrantScope.SKILL_ADD, item_key):
                    continue
                # A skill whose *text* changed is not a skill added: it is the
                # same node, rewritten. Identity here is the nid, and a
                # requested rewrite carries a TEXT grant against it. Without
                # this the two halves of this guard fought each other -- one
                # restoring the old reading, the other deleting the new one --
                # so filling the starter's blank slot was undone twice over.
                if item.nid in original_nids and ledger.covers(
                    GrantScope.TEXT, item.nid
                ):
                    continue
                current_group.items.remove(item)
                reports.append(
                    DriftReport(
                        guard=self.name,
                        scope=key,
                        ref=item.text,
                        detail="removed a skill added without a request",
                    )
                )

        return result, reports


class QualityGuard:
    """Advisory only. Never reverts.

    Word growth and invented metrics are signals a human should look at, not
    errors. Auto-reverting them would make the assistant silently refuse work it
    was asked to do, which is far more confusing than a warning.
    """

    name = "quality"

    def check(
        self, before: StudioDoc, after: StudioDoc, ledger: IntentLedger
    ) -> tuple[StudioDoc, list[DriftReport]]:
        reports: list[DriftReport] = []

        original_words = _count_words(before)
        current_words = _count_words(after)
        if original_words and current_words / original_words > _WORD_GROWTH_LIMIT:
            reports.append(
                DriftReport(
                    guard=self.name,
                    scope="document",
                    ref="word_count",
                    detail=(
                        f"text grew {current_words / original_words:.1f}x; "
                        "check it was tightened rather than padded"
                    ),
                    reverted=False,
                )
            )

        introduced = _metrics(after) - _metrics(before)
        if introduced:
            reports.append(
                DriftReport(
                    guard=self.name,
                    scope="document",
                    ref="metrics",
                    detail=(
                        "new figures appeared that were not in the original: "
                        + ", ".join(sorted(introduced)[:5])
                    ),
                    reverted=False,
                )
            )

        return after, reports


def _restore_frames(before: StudioDoc, result: StudioDoc, nid: str) -> None:
    """Put back the boxes that were rendering a restored entry.

    Restoring content without its layout leaves a node no frame will draw. The
    coverage gate would refuse that as an op batch, but a guard correction is
    written through ``repo.replace`` and never passes through it -- so the
    invalid document persists silently, and the *next* edit the user makes is
    rejected for a state they did not create.

    Only frames that are actually missing are restored, and only onto pages
    that still exist, so a correction never duplicates a box or resurrects a
    page the user legitimately deleted. Coverage is over containers, so this is
    usually a no-op: a section frame already covers the entry, and this matters
    only when the user has arranged that section by hand.
    """
    present = {
        element.nid
        for page in result.pages
        for element in page.elements
    }
    live_pages = {page.nid: page for page in result.pages}

    for page in before.pages:
        target = live_pages.get(page.nid)
        if target is None:
            continue
        for index, element in enumerate(page.elements):
            if getattr(element, "ref", None) != nid or element.nid in present:
                continue
            # Back at its old position in the list, which is paint order.
            target.elements.insert(min(index, len(target.elements)), element.model_copy(deep=True))


def _all_text(doc: StudioDoc) -> list[str]:
    out: list[str] = []
    if doc.summary:
        out.append(doc.summary.text)
    for entries in (doc.experience, doc.projects):
        for entry in entries:
            out.extend(bullet.text for bullet in entry.bullets)
    for entry in doc.education:
        if entry.detail:
            out.append(entry.detail.text)
    for group in doc.skills:
        out.extend(item.text for item in group.items)
    for section in doc.custom:
        out.extend(item.text for item in section.items)
    # Hand-placed text is content like any other. Without it the quality guard
    # reads a word-count collapse on a turn that touched nothing.
    for block in doc.blocks:
        out.extend(line.text for line in block.lines)
    return out


def _count_words(doc: StudioDoc) -> int:
    return sum(len(text.split()) for text in _all_text(doc))


def _metrics(doc: StudioDoc) -> set[str]:
    found: set[str] = set()
    for text in _all_text(doc):
        found.update(match.group(0).strip() for match in _METRIC.finditer(text))
    return found


# Order matters: identity first, then structure, then content, then advice.
GUARD_CHAIN: list[DriftGuard] = [
    PersonalInfoGuard(),
    EntryIntegrityGuard(),
    SkillsGuard(),
    QualityGuard(),
]


def run_guards(
    before: StudioDoc,
    after: StudioDoc,
    ledger: IntentLedger,
    *,
    chain: list[DriftGuard] | None = None,
) -> tuple[StudioDoc, list[DriftReport]]:
    """Run the chain, returning the corrected document and what it caught."""
    result = after
    reports: list[DriftReport] = []
    for guard in chain or GUARD_CHAIN:
        result, guard_reports = guard.check(before, result, ledger)
        reports.extend(guard_reports)

    reverted = [report for report in reports if report.reverted]
    if reverted:
        # Should be unreachable in normal operation: the gates ran first.
        logger.error(
            "Drift guards reverted %s change(s) that no tool call requested: %s",
            len(reverted),
            [f"{report.guard}:{report.ref}" for report in reverted],
        )
    return result, reports
