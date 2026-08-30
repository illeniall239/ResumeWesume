"""Drift guards.

The three tests that matter most are the ones encoding the behaviour change
from the engine this was ported from:

  - "change my email" must persist  (the old net clobbered it)
  - "remove Python" must persist    (the old net re-added it)
  - an unrequested name change must still revert

Getting the first two right without losing the third is the entire point of
scoping the guards to intent.
"""

from __future__ import annotations

from studio.doc.schema import (
    ExperienceNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)
from studio.guards.drift import (
    EntryIntegrityGuard,
    PersonalInfoGuard,
    QualityGuard,
    SkillsGuard,
    run_guards,
)
from studio.guards.grants import GrantScope, IntentGrant, IntentLedger, normalise_key

EXP = "exp_11111"
BULLET = "blt_aaaaa"


def base() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(
            name="Alex Morgan", email="alex@example.com", phone="+1-555-0142"
        ),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid=EXP,
                title="Senior Engineer",
                company="Northwind",
                years="2021 - Present",
                bullets=[TextNode(nid=BULLET, text="Rebuilt the ledger.")],
            )
        ],
        skills=[
            SkillGroup(
                nid="sgp_ggggg",
                key="technical",
                items=[
                    SkillItem(nid="skl_ppppp", text="Python"),
                    SkillItem(nid="skl_ggggo", text="Go"),
                ],
            )
        ],
    )


def ledger_with(*grants: IntentGrant) -> IntentLedger:
    ledger = IntentLedger(turn_id="t1")
    ledger.grant_many(list(grants))
    return ledger


class TestPersonalInfo:
    def test_granted_change_persists(self) -> None:
        """The regression the old engine could not express."""
        before = base()
        after = before.model_copy(deep=True)
        after.personal.email = "new@example.com"

        result, reports = PersonalInfoGuard().check(
            before,
            after,
            ledger_with(IntentGrant(GrantScope.PERSONAL_FIELD, "email")),
        )
        assert result.personal.email == "new@example.com"
        assert reports == []

    def test_ungranted_change_reverts(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.personal.name = "Someone Else"

        result, reports = PersonalInfoGuard().check(before, after, IntentLedger())
        assert result.personal.name == "Alex Morgan"
        assert reports[0].ref == "name"

    def test_a_grant_is_field_scoped(self) -> None:
        """Permission to change one field is not permission to change another."""
        before = base()
        after = before.model_copy(deep=True)
        after.personal.email = "new@example.com"
        after.personal.phone = "+1-555-9999"

        result, _ = PersonalInfoGuard().check(
            before,
            after,
            ledger_with(IntentGrant(GrantScope.PERSONAL_FIELD, "email")),
        )
        assert result.personal.email == "new@example.com"
        assert result.personal.phone == "+1-555-0142"


class TestSkills:
    def test_granted_removal_persists(self) -> None:
        """The old net re-appended anything removed, so this was impossible."""
        before = base()
        after = before.model_copy(deep=True)
        after.skills[0].items = [
            item for item in after.skills[0].items if item.text != "Python"
        ]

        result, reports = SkillsGuard().check(
            before,
            after,
            ledger_with(
                IntentGrant(GrantScope.SKILL_REMOVE, normalise_key("Python"))
            ),
        )
        assert [item.text for item in result.skills[0].items] == ["Go"]
        assert reports == []

    def test_ungranted_removal_is_restored(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.skills[0].items = []

        result, reports = SkillsGuard().check(before, after, IntentLedger())
        assert {item.text for item in result.skills[0].items} == {"Python", "Go"}
        assert len(reports) == 2

    def test_ungranted_addition_is_stripped(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.skills[0].items.append(SkillItem(nid="skl_rrrrr", text="Rust"))

        result, reports = SkillsGuard().check(before, after, IntentLedger())
        assert "Rust" not in {item.text for item in result.skills[0].items}
        assert reports[0].ref == "Rust"

    def test_granted_addition_persists(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.skills[0].items.append(SkillItem(nid="skl_rrrrr", text="Rust", source="user"))

        result, _ = SkillsGuard().check(
            before,
            after,
            ledger_with(IntentGrant(GrantScope.SKILL_ADD, normalise_key("Rust"))),
        )
        assert "Rust" in {item.text for item in result.skills[0].items}

    def test_reformatting_is_not_a_removal(self) -> None:
        """Node.js and nodejs are one skill; a case change must not read as a
        delete plus an add."""
        before = base()
        before.skills[0].items.append(SkillItem(nid="skl_nnnnn", text="Node.js"))
        after = before.model_copy(deep=True)
        after.skills[0].items[-1].text = "NodeJS"

        result, reports = SkillsGuard().check(before, after, IntentLedger())
        assert reports == []
        assert len(result.skills[0].items) == 3


class TestEntryIntegrity:
    def test_ungranted_deletion_is_restored(self) -> None:
        """The failure that actually loses a user: a job silently vanishing."""
        before = base()
        after = before.model_copy(deep=True)
        after.experience = []

        result, reports = EntryIntegrityGuard().check(before, after, IntentLedger())
        assert len(result.experience) == 1
        assert result.experience[0].company == "Northwind"
        assert reports[0].scope == "experience"

    def test_granted_deletion_persists(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.experience = []

        result, _ = EntryIntegrityGuard().check(
            before, after, ledger_with(IntentGrant(GrantScope.ENTRY_REMOVE, EXP))
        )
        assert result.experience == []

    def test_ungranted_identity_change_reverts(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.experience[0].company = "Fabricated Corp"

        result, reports = EntryIntegrityGuard().check(before, after, IntentLedger())
        assert result.experience[0].company == "Northwind"
        assert reports[0].ref == f"{EXP}.company"

    def test_granted_identity_change_persists(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.experience[0].company = "Northwind Systems"

        result, _ = EntryIntegrityGuard().check(
            before,
            after,
            ledger_with(IntentGrant(GrantScope.ENTRY_FIELD, f"{EXP}.company")),
        )
        assert result.experience[0].company == "Northwind Systems"

    def test_bullet_edits_are_left_alone(self) -> None:
        """Guards must not interfere with the ordinary case."""
        before = base()
        after = before.model_copy(deep=True)
        after.experience[0].bullets[0].text = "Cut settlement latency 96%."

        result, reports = EntryIntegrityGuard().check(before, after, IntentLedger())
        assert result.experience[0].bullets[0].text == "Cut settlement latency 96%."
        assert reports == []


class TestQuality:
    def test_never_reverts(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.experience[0].bullets[0].text = " ".join(["padding"] * 60)

        result, reports = QualityGuard().check(before, after, IntentLedger())
        assert result.experience[0].bullets[0].text.startswith("padding")
        assert all(report.reverted is False for report in reports)

    def test_flags_invented_metrics(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.experience[0].bullets[0].text = "Improved throughput by 340%."

        _, reports = QualityGuard().check(before, after, IntentLedger())
        assert any("340%" in report.detail for report in reports)

    def test_quiet_when_a_metric_was_already_there(self) -> None:
        before = base()
        before.experience[0].bullets[0].text = "Cut latency 96%."
        after = before.model_copy(deep=True)
        after.experience[0].bullets[0].text = "Reduced latency by 96%."

        _, reports = QualityGuard().check(before, after, IntentLedger())
        assert not any(report.ref == "metrics" for report in reports)


class TestChain:
    def test_a_clean_edit_passes_untouched(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.experience[0].bullets[0].text = "Cut settlement latency."

        result, reports = run_guards(before, after, IntentLedger())
        assert result.experience[0].bullets[0].text == "Cut settlement latency."
        assert [report for report in reports if report.reverted] == []

    def test_a_hostile_batch_is_fully_neutralised(self) -> None:
        before = base()
        after = before.model_copy(deep=True)
        after.personal.email = "attacker@evil.com"
        after.personal.name = "Someone Else"
        after.experience[0].company = "Fabricated Corp"
        after.skills[0].items = []

        result, reports = run_guards(before, after, IntentLedger())

        assert result.personal.email == "alex@example.com"
        assert result.personal.name == "Alex Morgan"
        assert result.experience[0].company == "Northwind"
        assert {item.text for item in result.skills[0].items} == {"Python", "Go"}
        assert len([report for report in reports if report.reverted]) >= 5

    def test_mixed_batch_keeps_only_what_was_asked_for(self) -> None:
        """The realistic case: one requested change alongside model drift."""
        before = base()
        after = before.model_copy(deep=True)
        after.personal.email = "new@example.com"  # requested
        after.personal.phone = "+1-555-9999"  # drift
        after.experience[0].bullets[0].text = "Tightened."  # ordinary edit

        result, _ = run_guards(
            before,
            after,
            ledger_with(IntentGrant(GrantScope.PERSONAL_FIELD, "email")),
        )
        assert result.personal.email == "new@example.com"
        assert result.personal.phone == "+1-555-0142"
        assert result.experience[0].bullets[0].text == "Tightened."
