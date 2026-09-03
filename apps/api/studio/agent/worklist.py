"""What still needs doing, and saying so one item at a time.

Built for a measured failure rather than a theory. Asked to tailor a template,
a 12B model makes one good edit and then writes "Here's your tailored resume:"
-- it believes it has finished, nothing contradicts it, and the turn ends with
a résumé that has a new name and nothing else changed.

The same model, told after each edit what is still outstanding, works through
the whole document without a single miss:

    round 1: set_personal_info
    round 2: rewrite_text
    round 3: rewrite_text
    round 4: rewrite_text

So the limit is not capability, and it is not the size of the tool menu, and it
is not the instruction wording -- all three were tested and none of them moved
it. A small model composes *one* piece of content per response. Ask for seven
and you get one, plus a sentence announcing it is done.

That is a shape the loop can supply, because the loop already knows every node
in the document and already re-prompts each round. This turns "tailor this
résumé" into the enumerated list the model executes reliably, and hands it back
one line at a time.

**The list remembers; the model decides.** Three earlier versions got that
backwards in different ways. The first built a list only for templates, which
said a real résumé deserves a lazier assistant. The second kept different items
per document type, so the *code* decided that retitling a job was fine here and
not there. The third asked the model to classify the request up front -- "does
this mean the whole résumé or one line?" -- and that failed for a reason worth
recording, because it is the same reason the list exists at all.

Asked that question directly, mistral-nemo answered TARGETED for every request
put to it, including "tailor this résumé for an AI engineer" and "I'm applying
for an ML role at Stripe, can you sort this out". Reordering the options, asking
it as yes/no, and giving it four worked examples scored 3/6, 4/6 and 4/6 -- all
within noise of always saying no. Abstract judgement about a request is exactly
what a small model is worst at, and building on it would have put the good
behaviour back behind a large model.

Concrete judgement about one item is a different question, and the same model is
good at it. Told "next: the headline -- call the tool, or say why it should not
change", it changes the ones that should change and declines the ones that
should not, in its own words: *"The title is already correct for the role."*

Building the list for every turn was tried too, and is worse than it looks. On
"tighten my first bullet" the model took the second offer and changed the name
-- an edit nobody asked for, on the strength of being asked.

**Asking the right question.** The classifier that failed was asking for a
label: "is this WHOLE or TARGETED?" The model's own output shows what went
wrong -- given "tighten my first bullet" it replied *"Just specific lines.
MOST"*, reasoning correctly and then attaching the opposite label. It could not
bind an abstract category to its own conclusion.

Asked the concrete version of the same question -- *which parts of the résumé
does this request require changing?*, answered by picking from a fixed list of
section names -- the same 12B model scores 12/12 on the same messages, with
clean separation: every narrow request names exactly one part, every broad one
names four or more. So the classification is an enumeration, and the counting
happens here rather than being asked for.

That is the same lesson as everything else in this file. The model is excellent
at concrete judgement about named things and poor at abstract judgement about
categories, and both failures looked like "the model is not capable" until the
question was rewritten.

**A detector, not a scoper.** The first version of this used the named parts to
build the list as well, which is wrong and produced a résumé worth keeping as a
warning. For "tailor this resume for an AI engineer named Rao Muhammad Hamza"
the model names CONTACT, HEADLINE, SUMMARY and JOB_TITLES -- four parts, plenty
to detect a whole-document request, and it leaves out BULLETS and SKILLS. Scoped
to that, the turn changed the name, the headline, the summary and the job title,
and left "Rebuilt the payments ledger" and "Led the migration of 40 services to
async Python" sitting under it. A tailored résumé that still describes somebody
else's job.

The count separates broad from narrow perfectly and the membership does not, so
only the count is used. Whatever the parts, the list covers the document, and
three declines in a row withdraw it. The list guarantees nothing is silently
forgotten; it does not overrule the model about what should change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from studio.doc.schema import StudioDoc

#: The sections a request can be about. Named rather than numbered because the
#: model has to recognise them in its own vocabulary, and "JOB_TITLES" is a
#: thing on a résumé in a way that "tier C" is not.
PARTS = ("CONTACT", "HEADLINE", "SUMMARY", "JOB_TITLES", "BULLETS", "SKILLS")

#: How many parts make a request whole-document. Four is where the measured gap
#: sits -- every narrow request in the sample named exactly one part and every
#: broad one named at least four -- so three is comfortably inside it and
#: tolerates the model naming one part fewer than it might.
WHOLE_DOCUMENT_PARTS = 3

#: How many skills make a tailored skills section. The item used to be satisfied
#: by the first `add_skill`, so a résumé retargeted at AI engineering came back
#: with one new entry against a role that expects a handful. Three is what the
#: instruction asks for, so three is what completes it.
SKILLS_WANTED = 3

#: Enough of the résumé to be worth the rounds. A turn that walks forty nodes is
#: slower than the wall clock allows and further than anyone asked; the items
#: below are ordered so that the ones that matter most are done first and a turn
#: cut short is still coherent.
MAX_ITEMS = 14

@dataclass(frozen=True)
class WorkItem:
    """One edit to consider, and the id that proves it was made."""

    #: What the op will report as touched: a node id, or ``personal.<field>``.
    key: str
    #: Said to the model, in its own terms. Names the tool and the target.
    instruction: str


@dataclass
class Worklist:
    """The outstanding items of a whole-document turn."""

    items: list[WorkItem]
    done: set[str] = field(default_factory=set)
    declined: set[str] = field(default_factory=set)
    #: Declines since the last item that was acted on.
    run_of_declines: int = 0
    #: Skill nodes touched this turn; the skills item wants several.
    skills_touched: set[str] = field(default_factory=set)

    def mark(self, touched: list[str]) -> None:
        """Record what a round actually changed.

        Matched loosely on purpose. A bullet rewritten inside an entry reports
        the bullet, while setting a job title reports ``exp_7f3a2.title``, so an
        exact comparison would leave items outstanding that are plainly done and
        the turn would nag about work it already did.
        """
        for nid in touched:
            self.done.add(nid)
            head, _, _ = nid.partition(".")
            self.done.add(head)
            # `add_skill` mints a fresh `skl_` id, so the skills item can never
            # be satisfied by matching its own key. Left unhandled the turn asks
            # for skills again every round and the model obliges -- twelve
            # `add_skill` calls before the wall clock ended it.
            if head.startswith(("skl_", "sgp_")):
                self.skills_touched.add(head)
                if len(self.skills_touched) >= SKILLS_WANTED:
                    self.done.add("skills")
        if touched:
            self.run_of_declines = 0

    def settle(self, key: str) -> None:
        """Move past an item that was offered and did not get done.

        Every item is offered exactly once. Asking twice was the bug that made a
        whole tailoring do nothing: the model answered the first offer by
        calling a different tool, so the item stayed outstanding, so the
        identical nudge went out again -- and a model that has already answered
        a question answers the repeat with silence. Three silent rounds later
        the turn stalled out having changed nothing.

        Settling also keeps the list from overruling anyone. Asked to retitle a
        job on a résumé where that would be a lie, a capable model says so in
        prose instead of calling a tool, and that is the right answer.
        """
        self.declined.add(key)

    def note_silence(self) -> None:
        """A round that called no tool at all.

        Counted apart from settling, because "worked on something else" and "had
        nothing to do" are different answers, and only the second one, repeated,
        means the request was narrower than the list assumed.
        """
        self.run_of_declines += 1

    @property
    def abandoned(self) -> bool:
        """Whether the request was narrower than the list assumed.

        Three items waved off in a row is what "tighten my first bullet" looks
        like from in here, and continuing to offer the other eleven is the list
        overruling somebody who was perfectly clear. Reached by watching the
        model answer concrete questions rather than by asking it an abstract one
        up front -- which was tried, and which it could not do.
        """
        return self.run_of_declines >= 3

    def remaining(self) -> list[WorkItem]:
        settled = self.done | self.declined
        return [item for item in self.items if item.key not in settled]

    def nudge(self) -> str:
        """The one line that keeps a small model going.

        Deliberately one item, not the whole list: handing over seven at once is
        what produced one edit and a farewell. It says the turn is not over,
        because the failure being corrected is a model that believes it has
        finished -- and it says the item can be waved off, because the model is
        the one that can see whether this particular change makes sense.
        """
        outstanding = self.remaining()
        if not outstanding:
            return ""

        item = outstanding[0]
        left = len(outstanding) - 1
        tail = f" {left} more after this one." if left else " This is the last one."
        return (
            f"Not finished yet. Next: {item.instruction}{tail} Call the tool now. "
            "If this one should not change, say why in one line and I will move "
            "on -- do not summarise the work so far."
        )


_SCOPE_PROMPT = """A person asked their resume assistant:

"{message}"

Which parts of the resume does that request require changing? Reply with only
the labels that apply, separated by spaces, from this list:

CONTACT HEADLINE SUMMARY JOB_TITLES BULLETS SKILLS NONE

Labels only, no other words."""


def is_scope_probe(messages: list[dict[str, Any]]) -> bool:
    """Whether these messages are the scope question rather than a real round.

    Exists for the scripted backend, which has to tell the two apart. Keying on
    "a call with no tools" was close enough to be wrong: résumé ingest parses
    sections with tool-free calls too, and answering those from here silently
    broke ten import tests.
    """
    if not messages:
        return False
    content = messages[-1].get("content", "")
    return "Which parts of the resume does that request require changing?" in content


async def classify_parts(backend: Any, message: str) -> set[str]:
    """Which sections this request is about, in the model's own words.

    Asked as an enumeration rather than as a label, which is the whole finding.
    The label version -- "is this WHOLE or TARGETED?" -- scored 6/12 on a 12B
    model, and its failures show why: it answered "Just specific lines. MOST",
    reasoning correctly and then attaching the opposite word. The enumeration
    scores 12/12 on the same messages.

    Run concurrently with the first round in :mod:`studio.agent.loop`, so the
    answer is waiting by the time that round ends and it costs no wall-clock
    time.

    An empty set on failure, which means no list and the unaided behaviour --
    the same as a request that turned out to be about one bullet.

    The token budget is not tuned to the answer, which is six words at most. A
    reasoning model spends its budget thinking before it writes anything, and at
    the 40 tokens this used to ask for, Gemini returned an empty string, the set
    came back empty, and every request looked narrow -- silently, on exactly the
    models most able to answer. ``think=False`` where the provider honours it,
    and room to think where it does not.
    """
    prompt = _SCOPE_PROMPT.format(message=message)
    try:
        reply = ""
        async for chunk in backend.stream(
            [{"role": "user", "content": prompt}],
            max_tokens=1024,
            temperature=0.0,
            think=False,
        ):
            text = getattr(chunk, "text", None)
            if text:
                reply += text
    except Exception:  # noqa: BLE001 -- a classifier must never fail a turn
        return set()

    upper = reply.upper()
    return {part for part in PARTS if re.search(rf"\b{part}\b", upper)}


def plan_for(doc: StudioDoc, parts: set[str] | None = None) -> Worklist:
    """Everything a whole-document pass could reach, most important first.

    Order is the point. A turn stopped early by the wall clock should have done
    the headline and the summary rather than the third bullet of the second job,
    because those are what a person reads first and what the rest is judged
    against.

    Identity -- the name, the headline, job titles -- is on the list for every
    document, and the instructions say plainly that those are optional. On a
    template it is placeholder text that has to go. On a real résumé, changing
    an employer is how a document starts saying something untrue, the model
    knows that from the standing rules, and asked here it declines and the turn
    moves on. Leaving those items off entirely was the code making that call in
    advance, for requests it had not read.

    ``parts`` exists for tests that want a narrower list. The turn itself always
    passes everything: :func:`classify_parts` counts reliably and enumerates
    unreliably, and scoping the list to what it named left tailored résumés with
    the previous job's bullets under a new headline.
    """
    wanted = parts if parts is not None else set(PARTS)
    items: list[WorkItem] = []
    if "CONTACT" in wanted:
        items.append(
            WorkItem(
                "personal.name",
                'set_personal_info field="name" -- only if the request names '
                "someone other than the person whose résumé this is",
            )
        )
    if "HEADLINE" in wanted:
        items.append(
            WorkItem(
                "personal.title",
                'set_personal_info field="title" -- the headline, for the target '
                "role",
            )
        )

    if doc.summary is not None and "SUMMARY" in wanted:
        items.append(
            WorkItem(
                doc.summary.nid,
                f"rewrite_text {doc.summary.nid} -- the summary, for the target role",
            )
        )

    for entry in doc.experience:
        # A real employer and job title are the person's history, so this item
        # says so and expects to be waved off on anything but a template.
        if "JOB_TITLES" in wanted:
            items.append(
                WorkItem(
                    entry.nid,
                    f"set_entry_identity {entry.nid} -- a job title fitting the "
                    f"target role (currently {entry.title!r}); skip this if that "
                    "is the person's real title",
                )
            )
        if "BULLETS" in wanted:
            for bullet in entry.bullets:
                items.append(
                    WorkItem(
                        bullet.nid,
                        f"rewrite_text {bullet.nid} -- describe this achievement "
                        "in the target role's terms",
                    )
                )

    if "BULLETS" in wanted:
        for project in doc.projects:
            for bullet in project.bullets:
                items.append(
                    WorkItem(
                        bullet.nid,
                        f"rewrite_text {bullet.nid} -- the project bullet, for the "
                        "target role",
                    )
                )

    # Skills last and as one item: a group is filled by repeated `add_skill`
    # calls, so enumerating each would spend a round apiece on the cheapest
    # edits in the document.
    if doc.skills and "SKILLS" in wanted:
        # Naming what is already listed, because rejecting a duplicate after
        # the fact does not work: told "Python is already in technical, add a
        # different skill", the model proposed Python three more times and the
        # turn stalled out with nothing added. It was not ignoring the message
        # so much as having no other candidate in mind. Given the existing list
        # up front it proposes something else first time.
        listed = ", ".join(
            item.text for group in doc.skills for item in group.items
        )
        items.append(
            WorkItem(
                "skills",
                f"add_skill -- between {SKILLS_WANTED} and {SKILLS_WANTED * 2} "
                "skills the target role expects, one call each, the most "
                "relevant first. Already listed, so do not repeat these: "
                f"{listed or '(nothing yet)'}",
            )
        )

    return Worklist(items=items[:MAX_ITEMS])
