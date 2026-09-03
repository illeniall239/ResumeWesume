"""The work list, and why it remembers without overruling.

Measured, not assumed. A 12B model asked to tailor a template makes one good
edit and writes "Here's your tailored resume:" -- it believes it has finished.
Told after each edit what is still outstanding, the same model works through the
whole document without a miss. Three other explanations were tested and killed
first: it can batch calls when given a list with values, the size of the tool
menu makes no difference, and suspending the conflicting prompt rules changed
nothing.

The list covers everything a whole-document pass could reach and decides none of
it. Every item can be waved off, and three declines in a row end it.

Which sections a request is about is asked of the model as an enumeration, not
as a label. Asked for a label -- "WHOLE or TARGETED?" -- a 12B model scored 6/12
and gave itself away by answering "Just specific lines. MOST": right reasoning,
opposite word. Asked which parts of the résumé the request requires changing, the
same model scored 12/12 on the same messages.
"""

from __future__ import annotations

from studio.agent.worklist import (
    MAX_ITEMS,
    PARTS,
    WHOLE_DOCUMENT_PARTS,
    Worklist,
    classify_parts,
    plan_for,
)
from studio.doc.schema import (
    ExperienceNode,
    PersonalInfo,
    SkillGroup,
    SkillItem,
    StudioDoc,
    TextNode,
)


def doc() -> StudioDoc:
    return StudioDoc(
        personal=PersonalInfo(name="Alex Morgan", title="Senior Backend Engineer"),
        summary=TextNode(nid="sum_00001", text="Backend engineer.", style="plain"),
        experience=[
            ExperienceNode(
                nid="exp_11111",
                title="Senior Engineer",
                company="Northwind",
                bullets=[
                    TextNode(nid="blt_aaaaa", text="Rebuilt the ledger."),
                    TextNode(nid="blt_bbbbb", text="Led the migration."),
                ],
            )
        ],
        skills=[
            SkillGroup(
                nid="sgp_ggggg",
                key="technical",
                items=[SkillItem(nid="skl_ppppp", text="Python")],
            )
        ],
    )


class FakeBackend:
    """Replies with whatever labels the test wants."""

    def __init__(self, reply: str = "", *, fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.seen: list[str] = []

    async def stream(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.seen.append(messages[-1]["content"])
        if self.fail:
            raise RuntimeError("provider is down")

        class Chunk:
            def __init__(self, text: str) -> None:
                self.text = text

        yield Chunk(self.reply)


class TestClassifyParts:
    """The question that works, and the one that did not.

    Asked for a label, a 12B model scored 6/12 and gave itself away: for
    "tighten my first bullet" it answered "Just specific lines. MOST" -- correct
    reasoning, opposite word. It could not bind an abstract category to its own
    conclusion. Asked which parts of the résumé the request requires changing,
    the same model scored 12/12 with clean separation.
    """

    async def test_it_reads_the_labels_back(self) -> None:
        backend = FakeBackend("HEADLINE SUMMARY JOB_TITLES BULLETS SKILLS")
        assert await classify_parts(backend, "tailor this") == {
            "HEADLINE",
            "SUMMARY",
            "JOB_TITLES",
            "BULLETS",
            "SKILLS",
        }

    async def test_a_narrow_request_names_one_part(self) -> None:
        parts = await classify_parts(FakeBackend("BULLETS"), "tighten my bullet")
        assert parts == {"BULLETS"}
        assert len(parts) < WHOLE_DOCUMENT_PARTS

    async def test_labels_survive_being_dressed_up(self) -> None:
        """Small models add punctuation, newlines and the odd stray word."""
        assert await classify_parts(FakeBackend("summary, bullets.\n"), "x") == {
            "SUMMARY",
            "BULLETS",
        }

    async def test_none_is_not_a_part(self) -> None:
        assert await classify_parts(FakeBackend("NONE"), "hello") == set()

    async def test_a_failed_classification_yields_no_list(self) -> None:
        """Falling back to the unaided behaviour, not to a full rewrite.

        Nothing is worse here than a turn marching through a résumé because a
        classification timed out.
        """
        assert await classify_parts(FakeBackend(fail=True), "tailor this") == set()

    async def test_it_asks_about_the_message_and_nothing_else(self) -> None:
        """No document goes into the classification.

        It is a question about a sentence, so it stays small enough to run
        alongside the first round without slowing anything down.
        """
        backend = FakeBackend("BULLETS")
        await classify_parts(backend, "tighten my bullet")
        assert len(backend.seen) == 1
        assert "tighten my bullet" in backend.seen[0]
        assert "Northwind" not in backend.seen[0]


class TestPlan:
    def test_it_covers_the_whole_document(self) -> None:
        keys = [item.key for item in plan_for(doc(), set(PARTS)).items]
        assert keys == [
            "personal.name",
            "personal.title",
            "sum_00001",
            "exp_11111",
            "blt_aaaaa",
            "blt_bbbbb",
            "skills",
        ]

    def test_the_named_parts_scope_the_list(self) -> None:
        """A request about bullets produces a list of bullets.

        The classification already says which sections are in play, so the list
        follows it rather than offering the whole document every time.
        """
        assert [item.key for item in plan_for(doc(), {"BULLETS"}).items] == [
            "blt_aaaaa",
            "blt_bbbbb",
        ]
        assert [item.key for item in plan_for(doc(), {"SUMMARY"}).items] == [
            "sum_00001"
        ]
        assert plan_for(doc(), set()).items == []

    def test_the_skills_item_names_what_is_already_there(self) -> None:
        """Rejecting a duplicate after the fact does not work.

        Told "Python is already in technical, add a different skill", the model
        proposed Python three more times and the turn stalled out having added
        nothing. It had no other candidate in mind; given the list up front it
        proposes something else.
        """
        skills = next(
            item
            for item in plan_for(doc(), set(PARTS)).items
            if item.key == "skills"
        )
        assert "do not repeat these: Python" in skills.instruction

    def test_identity_items_say_they_are_optional(self) -> None:
        """One list for a template and for somebody's real career.

        Which of these should change is in the request, and the model read the
        request. Leaving the items off for real résumés was the code deciding
        in advance, for a message it had not seen.
        """
        plan = plan_for(doc(), set(PARTS))
        name = next(item for item in plan.items if item.key == "personal.name")
        job = next(item for item in plan.items if item.key == "exp_11111")

        assert "only if" in name.instruction
        assert "skip this" in job.instruction

    def test_the_headline_comes_before_the_third_bullet(self) -> None:
        """A turn cut short by the wall clock should still be coherent.

        Whoever reads the résumé reads the name and the summary first, so those
        are done first and the rest is judged against them.
        """
        keys = [item.key for item in plan_for(doc(), set(PARTS)).items]
        assert keys.index("personal.title") < keys.index("blt_aaaaa")

    def test_a_long_resume_does_not_become_a_forty_round_turn(self) -> None:
        big = doc()
        big.experience[0].bullets = [
            TextNode(nid=f"blt_{index:05d}", text="x") for index in range(30)
        ]
        assert len(plan_for(big, set(PARTS)).items) == MAX_ITEMS


class TestProgress:
    def test_an_item_is_done_when_its_node_is_touched(self) -> None:
        plan = plan_for(doc(), set(PARTS))
        plan.mark(["sum_00001"])
        assert "sum_00001" not in [item.key for item in plan.remaining()]

    def test_a_field_edit_satisfies_its_entry(self) -> None:
        """`set_entry_identity` reports `exp_11111.title`, not `exp_11111`.

        Compared exactly, the item stayed outstanding and the turn nagged for
        work it had just done.
        """
        plan = plan_for(doc(), set(PARTS))
        plan.mark(["exp_11111.title"])
        assert "exp_11111" not in [item.key for item in plan.remaining()]

    def test_the_skills_item_wants_more_than_one(self) -> None:
        """One new skill is not a tailored skills section.

        Satisfied by the first `add_skill`, a résumé retargeted at AI
        engineering came back listing one addition -- and, before the tool
        learned to say so, a second copy of Python.
        """
        plan = plan_for(doc(), set(PARTS))
        plan.mark(["skl_9nEWx"])
        assert "skills" in [item.key for item in plan.remaining()]

        plan.mark(["skl_9nEWx", "skl_2bQ4r", "skl_7kLmz"])
        assert "skills" not in [item.key for item in plan.remaining()]

    def test_the_skills_item_cannot_be_satisfied_by_one_id_twice(self) -> None:
        """`mark` gets the whole accumulated set each round, not a delta."""
        plan = plan_for(doc(), set(PARTS))
        for _ in range(4):
            plan.mark(["skl_9nEWx"])
        assert "skills" in [item.key for item in plan.remaining()]

    def test_three_silent_rounds_end_the_list(self) -> None:
        """What "tighten my first bullet" looks like from in here.

        Silence, not settling: an item the model passed over while editing
        something else says nothing about how broad the request is.
        """
        plan = plan_for(doc(), set(PARTS))
        plan.note_silence()
        plan.note_silence()
        assert not plan.abandoned

        plan.note_silence()
        assert plan.abandoned

    def test_working_through_the_list_never_ends_it_early(self) -> None:
        """Settling is not silence.

        A model answering each offer by editing something -- not always the
        thing offered -- is working, and the list must not withdraw under it.
        """
        plan = plan_for(doc(), set(PARTS))
        for item in list(plan.items)[:4]:
            plan.settle(item.key)
        assert not plan.abandoned

    def test_doing_the_work_resets_the_run(self) -> None:
        """A thorough turn is not ended by silence scattered through it.

        Somebody's real résumé has items that should be waved off -- a real job
        title, a real name -- in among a great deal that should change.
        """
        plan = plan_for(doc(), set(PARTS))
        plan.note_silence()
        plan.note_silence()
        plan.mark(["sum_00001"])
        plan.note_silence()
        assert not plan.abandoned

    def test_a_settled_item_is_never_offered_twice(self) -> None:
        """The model's "no" is an answer, not a failure to comply.

        Asked to retitle a job on a real résumé, saying so in prose is right.
        This is what lets one list serve a template and a real career history.
        """
        plan = plan_for(doc(), set(PARTS))
        plan.settle("exp_11111")
        assert "exp_11111" not in [item.key for item in plan.remaining()]

    def test_settling_everything_ends_the_turn(self) -> None:
        plan = plan_for(doc(), set(PARTS))
        for item in list(plan.items):
            plan.settle(item.key)
        assert plan.remaining() == []
        assert plan.nudge() == ""


class TestNudge:
    def test_it_names_one_item_and_says_it_is_not_over(self) -> None:
        plan = plan_for(doc(), set(PARTS))
        plan.mark(["personal.name"])
        nudge = plan.nudge()

        assert "Not finished yet" in nudge
        assert "personal_info" in nudge and 'field="title"' in nudge
        # One item, never the whole list: handing over seven at once is what
        # produced a single edit and a farewell.
        assert "sum_00001" not in nudge

    def test_it_offers_the_way_out(self) -> None:
        assert "should not change" in plan_for(doc(), set(PARTS)).nudge()

    def test_the_last_item_says_so(self) -> None:
        plan = Worklist(items=plan_for(doc(), set(PARTS)).items[:1])
        assert "last one" in plan.nudge()

    def test_nothing_left_says_nothing(self) -> None:
        assert Worklist(items=[]).nudge() == ""


class TestSkillsSection:
    """Two failures that only showed up in a real tailoring.

    Both were found by reading the finished résumé rather than the event log,
    which is the only place either is visible.
    """

    def test_the_instruction_names_a_range(self) -> None:
        """"Three or more" was read as licence for thirty-four.

        Asked to tailor a résumé, Gemini added Agile Methodologies, Version
        Control and SQL among thirty-odd others, burying the four that mattered.
        """
        skills = next(
            item
            for item in plan_for(doc(), set(PARTS)).items
            if item.key == "skills"
        )
        assert "between 3 and 6" in skills.instruction
