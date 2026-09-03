"""The turn loop.

One user message in, a stream of events out, with the document mutated
incrementally along the way.

Shape of a turn:

    checkpoint the document          (so the whole turn is one undo)
    repeat up to the iteration budget:
        stream from the model
        as each tool call's arguments balance, execute it immediately
        feed results back as tool messages
        stop when the model stops calling tools
    run the drift guards against the pre-turn document
    emit done

Executing mid-stream rather than after it is what produces the "watch it work"
behaviour. Everything else here exists to make that safe when the model is
unreliable: salvage before rejection, rejection before failure, and a guard pass
that catches anything the gates let through.

A turn is never all-or-nothing. Applied edits stay applied, each independently
validated, and the checkpoint gives the user a single undo if the overall result
is not what they wanted. Transactional rollback would throw away good work
because of one bad call.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from studio.agent.assembler import (
    AssembledCall,
    CallProgress,
    StreamFinished,
    TextEvent,
    ThinkingEvent,
    ToolCallAssembler,
)
from studio.agent.budget import BudgetExceeded, TurnBudget
from studio.agent.context import find, full_section, outline, uploads
from studio.agent.grounding import Grounder
from studio.agent.prompts import build_messages, repair_message
from studio.agent.prose import ProseStream
from studio.agent.salvage import (
    closest_tool,
    coerce_arguments,
    extract_embedded_calls,
    repair_json,
)
from studio.agent import typography
from studio.agent.tools import REGISTRY, ToolError, ToolRegistry, ToolSpec, tiers_for_message
from studio.agent.worklist import (
    WHOLE_DOCUMENT_PARTS,
    Worklist,
    classify_parts,
    plan_for,
)
from studio.config import settings
from studio.doc.apply import OpContext
from studio.doc.index import NodeIndex
from studio.doc.ops import DocOp
from studio.doc.schema import StudioDoc
from studio.guards.drift import run_guards
from studio.guards.grants import GrantScope, IntentGrant, IntentLedger
from studio.llm.backend import BackendError, ChatBackend
from studio.persistence.repo import DocumentRepo
from studio.streaming import events as ev
from studio.streaming.channel import Cancelled, TurnChannel

logger = logging.getLogger(__name__)

_READ_ONLY = {"read_document", "find_text"}


@dataclass
class TurnRequest:
    document_id: str
    message: str
    job_description: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    busy_nids: set[str] = field(default_factory=set)
    consent_tokens: set[str] = field(default_factory=set)


@dataclass
class TurnResult:
    status: str
    applied: int
    rejected: int
    checkpoint_id: str | None
    version: int


class TurnRunner:
    """Runs one turn, emitting events into a channel."""

    def __init__(
        self,
        *,
        repo: DocumentRepo,
        backend: ChatBackend,
        registry: ToolRegistry | None = None,
        budget: TurnBudget | None = None,
    ) -> None:
        self._repo = repo
        self._backend = backend
        self._registry = registry or REGISTRY
        self._budget = budget or TurnBudget()
        # Set per turn from the document. A runner handles one turn at a time,
        # which is what makes this safe to hold here rather than thread through
        # every execute path.
        self._scaffolding = False
        #: What used to stop a turn and now gets written down instead: skills
        #: with no support anywhere in the résumé, and edits to the person's own
        #: identity or history. Neither blocks anything. Both are reported once,
        #: over a finished document.
        self._unsupported: list[str] = []
        self._identity_changes: list[str] = []

    async def run(self, request: TurnRequest, channel: TurnChannel) -> TurnResult:
        started = time.monotonic()
        state = await self._repo.get(request.document_id)
        if state is None:
            channel.emit(
                ev.ErrorEvent(code="not_found", message="Document not found")
            )
            channel.close()
            return TurnResult("failed", 0, 0, None, 0)

        base_doc = state.doc
        ledger = IntentLedger(turn_id=channel.turn_id)
        grounder = Grounder.build(
            base_doc,
            user_message=request.message,
            jd_keywords=_keywords(request.job_description),
        )

        # Scaffolding is not somebody's résumé, so the guards that protect one
        # are suspended here and only here. Every tier is granted and grounding
        # is not enforced, because "Alex Morgan" is not a person and the bullets
        # are not claims -- refusing to invent is refusing to do the only thing
        # being asked. Everything written under this is marked; see
        # ``unverified``.
        scaffolding = base_doc.scaffold
        self._scaffolding = scaffolding
        self._unsupported = []
        self._identity_changes = []
        if scaffolding:
            # Lifts only the runaway-rewrite cap; every other limit stands.
            self._budget = self._budget.for_scaffold()
        tiers = {"R", "A", "B", "C"} if scaffolding else tiers_for_message(
            request.message
        )
        schemas = self._registry.schemas(tiers)

        checkpoint_id = await self._repo.checkpoint(
            request.document_id, label="before turn", turn_id=channel.turn_id
        )

        channel.emit(
            ev.TurnStarted(
                base_version=state.version,
                base_hash=state.content_hash,
                model=self._backend.spec.model,
                provider=self._backend.spec.provider,
                tools=[schema["function"]["name"] for schema in schemas],
            )
        )

        # Only when there are any: a résumé with no pictures pays nothing for
        # the capability, which is almost every résumé.
        outline_text = outline(base_doc)
        listing = uploads(await self._repo.list_assets(request.document_id))
        if listing:
            outline_text = f"{outline_text}\n\n{listing}"

        messages = build_messages(
            user_message=request.message,
            outline_text=outline_text,
            history=request.history,
            job_description=request.job_description,
            scaffold=scaffolding,
        )

        applied_total = 0
        rejected_total = 0
        status = "ok"
        repairs_per_call: dict[str, int] = {}
        # Which sections the request is about, asked of the model as an
        # enumeration rather than a label -- the difference between 12/12 and
        # 6/12 on a 12B model. Started here and awaited after the first round,
        # so it runs while that round streams and costs no wall-clock time.
        parts_task = asyncio.create_task(
            classify_parts(self._backend, request.message)
        )
        worklist: Worklist | None = None
        nudged_key: str | None = None
        # What the assistant says, with a blank line between the thought it had
        # before it started working and the one it had after.
        prose = ProseStream(channel.emit)

        try:
            while True:
                self._budget.start_iteration()
                channel.raise_if_cancelled()

                assembler = ToolCallAssembler()
                tool_messages: list[dict[str, Any]] = []
                executed_any = False
                applied_this_round = 0
                # A round is an utterance: the model spoke, did some work, and
                # is speaking again.
                prose.new_utterance()

                async for chunk in self._backend.stream(
                    messages, tools=schemas or None, max_tokens=settings.llm_max_tokens
                ):
                    channel.raise_if_cancelled()

                    for event in assembler.feed(chunk):
                        if isinstance(event, TextEvent):
                            prose.say(event.text)
                        elif isinstance(event, CallProgress):
                            # Shown, not applied. The edit lands when the call
                            # balances, a moment later.
                            channel.emit(
                                ev.Drafting(
                                    call_id=event.call_id,
                                    target=event.target,
                                    text=event.text,
                                )
                            )
                        elif isinstance(event, ThinkingEvent):
                            channel.emit(ev.ThinkingDelta(text=event.text))
                        elif isinstance(event, AssembledCall):
                            executed_any = True
                            applied, rejected, result_message = await self._execute(
                                event,
                                request,
                                channel,
                                ledger,
                                grounder,
                                repairs_per_call,
                            )
                            applied_total += applied
                            applied_this_round += applied
                            rejected_total += rejected
                            tool_messages.append(result_message)
                        elif isinstance(event, StreamFinished):
                            # Cut off mid-answer rather than finished.
                            #
                            # A reasoning model can spend its whole generation
                            # budget inside its thinking block and stop before
                            # it writes anything -- no reply and no tool call.
                            # The provider says so plainly, and until now that
                            # signal was read into the assembler and dropped,
                            # so the turn ended looking like the model had
                            # simply chosen to do nothing. It is the difference
                            # between "it decided not to" and "it never got to
                            # the answer", and only one of those is worth the
                            # user retrying.
                            if event.finish_reason == "length":
                                channel.emit(
                                    ev.Warning(
                                        source="truncated",
                                        message=(
                                            "The model was cut off before it "
                                            "finished: it used its whole "
                                            "response budget and stopped "
                                            "mid-answer."
                                        ),
                                    )
                                )
                            channel.emit(
                                ev.Usage(
                                    prompt_tokens=event.usage.get("prompt_tokens", 0),
                                    completion_tokens=event.usage.get(
                                        "completion_tokens", 0
                                    ),
                                    ms=int((time.monotonic() - started) * 1000),
                                    iterations=self._budget.iterations,
                                )
                            )

                # Calls whose arguments never balanced, plus calls the model
                # wrote into its prose instead of the structured field.
                for leftover in assembler.unfired():
                    executed_any = True
                    applied, rejected, result_message = await self._execute(
                        leftover, request, channel, ledger, grounder, repairs_per_call
                    )
                    applied_total += applied
                    applied_this_round += applied
                    rejected_total += rejected
                    tool_messages.append(result_message)

                if not assembler.had_tool_calls and assembler.text:
                    for salvaged in extract_embedded_calls(
                        assembler.text, self._registry.names
                    ):
                        executed_any = True
                        call = AssembledCall(
                            call_id=f"embedded_{uuid.uuid4().hex[:8]}",
                            name=salvaged.name,
                            raw_arguments=json.dumps(salvaged.arguments),
                            arguments=salvaged.arguments,
                        )
                        applied, rejected, result_message = await self._execute(
                            call, request, channel, ledger, grounder, repairs_per_call
                        )
                        applied_total += applied
                        applied_this_round += applied
                        rejected_total += rejected
                        tool_messages.append(result_message)

                # A round that changed something resets the stall counter, so a
                # turn working steadily through a resume runs as long as it
                # needs to. Only rounds that change nothing count against it.
                self._budget.end_iteration(applied_this_round)

                if parts_task is not None:
                    parts = await parts_task
                    parts_task = None
                    # Only the count. Asked which parts "tailor this resume for
                    # an AI engineer" touches, the model names four and omits
                    # BULLETS -- enough to know the request is broad, and not a
                    # description of the work. Building the list from those four
                    # produced a résumé with a new headline over the old job's
                    # bullets. A narrow request names one part, gets no list,
                    # and ends when the model stops, exactly as before.
                    if len(parts) >= WHOLE_DOCUMENT_PARTS:
                        worklist = plan_for(base_doc)

                if worklist is not None:
                    # The budget already accumulates every nid the turn has
                    # touched and marking is cumulative, so this needs no
                    # per-round bookkeeping. Done before anything is settled, so
                    # an item the model actually did is recorded as done rather
                    # than passed over.
                    worklist.mark(sorted(self._budget.touched))

                # Every item is offered once. Whatever the model did with the
                # offer -- the edit, a different edit, or a sentence saying why
                # not -- the turn moves on. Re-asking made a whole tailoring do
                # nothing: the identical nudge went out twice, and a model that
                # has already answered a question answers the repeat with
                # silence, three times, until the stall counter ended the turn.
                if worklist is not None and nudged_key:
                    if nudged_key not in worklist.done:
                        worklist.settle(nudged_key)
                    if not executed_any:
                        worklist.note_silence()
                    if worklist.abandoned:
                        # Silence three rounds running means the request was
                        # narrower than the list assumed. Stop offering; the turn
                        # then ends when the model stops, as it did before.
                        worklist = None

                outstanding = worklist.remaining() if worklist else []

                # A turn ends when the model stops calling tools *and* there is
                # nothing left on the list. Without that second half a model
                # that announces "here is your tailored resume" after one edit
                # ends the turn on its own say-so, which is the whole failure
                # this list exists to correct.
                if not executed_any and not outstanding:
                    break

                messages.append(assembler.assistant_message())
                messages.extend(tool_messages)

                if outstanding:
                    nudged_key = outstanding[0].key
                    messages.append(
                        {"role": "user", "content": worklist.nudge()}  # type: ignore[union-attr]
                    )
                else:
                    nudged_key = None

            # Said once, on a turn that ran to completion. The user is judging a
            # finished document with one undo behind it, rather than a
            # half-rewritten one with an apology attached.
        except Cancelled:
            status = "cancelled"

        except BudgetExceeded as limit:
            logger.info("Turn %s hit the %s budget", channel.turn_id, limit.limit)
            # "Some changes could not be applied" is the wrong thing to tell
            # someone whose turn attempted no change at all. A turn that spends
            # every round reading has a different problem and a different
            # remedy, and the two read identically without this.
            message = str(limit)
            if applied_total == 0 and rejected_total == 0:
                message += (
                    " Nothing was changed: the assistant spent the turn looking"
                    " things up rather than editing. Naming what to change --"
                    " a section, or words that appear in the document -- usually"
                    " settles it."
                )
            channel.emit(ev.Warning(source="budget", message=message))
            status = "partial"
        except BackendError as error:
            channel.emit(ev.ErrorEvent(code="provider_error", message=str(error)))
            status = "failed"
        except Exception as error:  # noqa: BLE001 - never leak a turn crash
            logger.exception("Turn %s crashed", channel.turn_id)
            channel.emit(
                ev.ErrorEvent(
                    code="internal_error",
                    message="The assistant hit an unexpected error.",
                )
            )
            status = "failed"
        finally:
            # A turn that ends in its first round -- cancelled, or the model
            # errored -- leaves the classification in flight. Nothing awaits it
            # after this point, and an orphaned task logs a warning at exit.
            if parts_task is not None:
                parts_task.cancel()

        # Outside the try, because a turn that ended at the wall clock or on a
        # provider error still changed the document, and what it changed is
        # exactly what somebody needs to be told about. Emitted once, over a
        # finished document with one undo behind it -- these three replaced a
        # cap and two refusals, and each names what to look at rather than what
        # was prevented.
        for notice in self._budget.review(len(NodeIndex(base_doc))):
            channel.emit(ev.Warning(source="scale", message=notice))

        if self._unsupported:
            skills = ", ".join(dict.fromkeys(self._unsupported))
            channel.emit(
                ev.Warning(
                    source="grounding",
                    message=(
                        f"Added without finding support in your resume: {skills}. "
                        "Keep them only if they are true -- you will be asked "
                        "about them."
                    ),
                )
            )

        if self._identity_changes:
            channel.emit(
                ev.Warning(
                    source="identity",
                    message=(
                        "Changed without being named in your instructions: "
                        + ", ".join(dict.fromkeys(self._identity_changes))
                        + ". These are claims about your history -- worth "
                        "checking before you send it."
                    ),
                )
            )

        final = await self._finalise(request, base_doc, ledger, channel)

        if status == "ok" and rejected_total and not applied_total:
            status = "failed"
        elif status == "ok" and rejected_total:
            status = "partial"

        channel.emit(
            ev.Done(
                doc_version=final.version if final else state.version,
                hash=final.content_hash if final else state.content_hash,
                checkpoint_id=checkpoint_id,
                applied=applied_total,
                rejected=rejected_total,
                status=status,  # type: ignore[arg-type]
            )
        )
        channel.close()

        return TurnResult(
            status=status,
            applied=applied_total,
            rejected=rejected_total,
            checkpoint_id=checkpoint_id,
            version=final.version if final else state.version,
        )

    # --- execution ------------------------------------------------------

    async def _execute(
        self,
        call: AssembledCall,
        request: TurnRequest,
        channel: TurnChannel,
        ledger: IntentLedger,
        grounder: Grounder,
        repairs_per_call: dict[str, int],
    ) -> tuple[int, int, dict[str, Any]]:
        """Run one tool call. Returns (applied, rejected, tool message)."""
        state = await self._repo.get(request.document_id)
        doc = state.doc if state else StudioDoc()

        name = call.name
        spec = self._registry.get(name)
        if spec is None:
            # A near-miss name is far more likely than a genuinely unknown tool.
            corrected = closest_tool(name, self._registry.names)
            if corrected:
                channel.emit(
                    ev.Warning(
                        source="salvage",
                        message=f"Read {name!r} as {corrected!r}",
                    )
                )
                name = corrected
                spec = self._registry.get(corrected)

        if spec is None:
            return self._reject(
                call, channel, "unknown_tool", f"There is no tool called {call.name!r}."
            )

        channel.emit(ev.ToolStart(call_id=call.call_id, name=name, tier=spec.tier))

        raw_arguments = call.arguments
        if raw_arguments is None:
            raw_arguments = repair_json(call.raw_arguments)
        if raw_arguments is None:
            return self._reject(
                call,
                channel,
                "invalid_json",
                "The arguments were not valid JSON.",
                spec=spec,
                repairs_per_call=repairs_per_call,
            )

        coerced, notes = coerce_arguments(name, raw_arguments, doc)
        for note in notes:
            channel.emit(ev.Warning(source="salvage", message=note))

        try:
            args = spec.Args.model_validate(coerced)
        except ValidationError as error:
            return self._reject(
                call,
                channel,
                "invalid_args",
                _describe_validation(error),
                spec=spec,
                repairs_per_call=repairs_per_call,
            )

        channel.emit(
            ev.ToolArgs(
                call_id=call.call_id, name=name, args=args.model_dump(mode="json")
            )
        )

        if name in _READ_ONLY:
            return 0, 0, self._read(name, args, doc, call.call_id)

        # Grounding used to reject an ungrounded skill outright. It now records
        # one. The rejection was the engine overruling a request the person had
        # already made in plain words -- and it overruled the model too, which
        # can see the whole résumé and knows perfectly well whether Kubernetes
        # is a fair claim for somebody who ran the cluster migration. What the
        # engine can do that neither of them can is remember exactly which lines
        # went in without support and show them for checking, which is the
        # review that was actually wanted.
        if spec.tier == "B" and name == "add_skill" and not self._scaffolding:
            verdict = grounder.check(args.skill, args.evidence)  # type: ignore[attr-defined]
            if not verdict.ok:
                self._unsupported.append(args.skill)  # type: ignore[attr-defined]

        # Consent used to stop here and ask. The dialog was the wrong shape for
        # the request it interrupted: somebody who says "retarget this for data
        # science" has already answered the question, and being asked it four
        # more times mid-turn is the assistant refusing to believe them.
        #
        # What made asking first look necessary was the fear of a change that
        # could not be taken back, and that fear is unfounded here -- the whole
        # turn is one checkpoint, so a single undo puts every one of these back.
        # So the change is made, recorded, and shown afterwards: the person
        # judges a finished résumé instead of authorising edits one at a time
        # against a document they cannot see yet.
        if (
            spec.tier == "C"
            and not self._scaffolding
            and not _was_asked_for(args, request)
        ):
            self._identity_changes.append(spec.label(args))

        try:
            # Cleaned here, at the one point every tool funnels through on its
            # way to the document -- so a tool added later is covered without
            # anyone remembering the rule exists. Only the assistant's words:
            # a dash somebody typed themselves is a choice, and this never sees
            # a direct edit.
            ops: list[DocOp] = [
                typography.clean_op(op) for op in spec.compile(args, doc)
            ]
        except ToolError as error:
            return self._reject(
                call, channel, error.code, str(error), spec=spec
            )

        ctx = OpContext(
            granted_tiers={"A", "B", "C"},
            busy_nids=request.busy_nids,
            actor="agent",
        )

        try:
            new_state, applied, rejected = await self._repo.apply(
                request.document_id, ops, ctx=ctx, turn_id=channel.turn_id
            )
        except Exception as error:  # noqa: BLE001
            logger.error("Applying %s failed: %s", name, error)
            return self._reject(
                call, channel, "apply_failed", "The edit could not be saved."
            )

        if applied:
            touched = sorted({nid for entry in applied for nid in entry.touched})
            ledger.grant_many(spec.grants(args, doc))
            ledger.grant_many(_grants_from_ops(applied))
            self._budget.record_ops(len(applied), touched)

            channel.emit(
                ev.PatchApplied(
                    call_id=call.call_id,
                    ops=[entry.op for entry in applied],
                    touched=touched,
                    doc_version=new_state.version,
                    hash=new_state.content_hash,
                    label=spec.label(args),
                )
            )

        for entry in rejected:
            channel.emit(
                ev.PatchRejected(
                    call_id=call.call_id,
                    code=entry.code,
                    message=entry.message,
                    hint=entry.hint,
                )
            )

        if rejected and not applied:
            detail = rejected[0].message
            if rejected[0].hint:
                detail += f" ({json.dumps(rejected[0].hint)[:200]})"
            return (
                0,
                len(rejected),
                _tool_message(call.call_id, f"Rejected: {rejected[0].code}. {detail}"),
            )

        note = (
            f"Applied {len(applied)} change(s)."
            + (f" {len(rejected)} rejected." if rejected else "")
        )

        # Adding a skill is the one edit whose next call depends on what the
        # last one did, and the outline the model was given is from before the
        # turn. Without this it proposes the skill it just added: TensorFlow
        # went in, then came back four times against a rejection saying it was
        # already there, and the turn stalled out.
        if name == "add_skill":
            listed = ", ".join(
                item.text for group in new_state.doc.skills for item in group.items
            )
            note += f" Skills now: {listed}."

        return (len(applied), len(rejected), _tool_message(call.call_id, note))

    def _read(
        self, name: str, args: BaseModel, doc: StudioDoc, call_id: str
    ) -> dict[str, Any]:
        if name == "find_text":
            query = args.query  # type: ignore[attr-defined]
            matches = find(doc, query, args.limit)  # type: ignore[attr-defined]
            if matches:
                body = "\n".join(
                    f"[{match['nid']}] ({match['kind']}) {match['text']}"
                    for match in matches
                )
            else:
                # A miss used to answer "No matches." and nothing else, which is
                # a dead end: the model knows no more than before it asked, so
                # it rephrases and asks again. Six of those exhaust the round
                # budget with no edit ever attempted -- an entire turn spent
                # searching, which is what "Stopped after 6 rounds without
                # settling" was reporting.
                #
                # A miss now answers the question actually being asked, which is
                # "then which node do I edit?". Handing back the outline makes a
                # second search pointless rather than tempting, and it is the
                # same text the turn opened with, so it costs nothing to trust.
                body = (
                    f"No node matches {query!r}. Do not search again -- this is "
                    "the whole document, and every id in it can be edited "
                    "directly:\n\n" + outline(doc)
                )
        else:
            section = args.section  # type: ignore[attr-defined]
            body = (
                full_section(doc, section)
                if args.detail == "full" and section  # type: ignore[attr-defined]
                else outline(doc, section=section)
            )
        return _tool_message(call_id, body)

    def _reject(
        self,
        call: AssembledCall,
        channel: TurnChannel,
        code: str,
        detail: str,
        *,
        spec: ToolSpec | None = None,
        repairs_per_call: dict[str, int] | None = None,
    ) -> tuple[int, int, dict[str, Any]]:
        channel.emit(
            ev.PatchRejected(call_id=call.call_id, code=code, message=detail)
        )

        # Hand back the schema for this one tool. Re-sending every schema would
        # push the document out of a small context window, which is often what
        # caused the bad call to begin with.
        if spec is not None and repairs_per_call is not None:
            seen = repairs_per_call.get(call.call_id, 0)
            repairs_per_call[call.call_id] = seen + 1
            if seen < self._budget.max_repairs_per_call:
                self._budget.record_repair()
                schema = json.dumps(
                    spec.json_schema()["function"]["parameters"], indent=2
                )
                return (
                    0,
                    1,
                    _tool_message(
                        call.call_id, repair_message(spec.name, code, detail, schema)
                    ),
                )

        return 0, 1, _tool_message(call.call_id, f"Rejected: {code}. {detail}")

    async def _finalise(
        self,
        request: TurnRequest,
        base_doc: StudioDoc,
        ledger: IntentLedger,
        channel: TurnChannel,
    ):
        """Run the drift guards against the pre-turn document."""
        state = await self._repo.get(request.document_id)
        if state is None:
            return None

        corrected, reports = run_guards(base_doc, state.doc, ledger)

        for report in reports:
            if report.reverted:
                channel.emit(
                    ev.DriftSuppressed(
                        guard=report.guard,
                        scope=report.scope,
                        ref=report.ref,
                        detail=report.detail,
                    )
                )
            else:
                channel.emit(
                    ev.Warning(source=report.guard, message=report.detail)
                )

        if any(report.reverted for report in reports):
            # A guard firing means something got past the gates. Persist the
            # corrected document rather than leaving the drift in place.
            await self._repo.replace(
                request.document_id, corrected, reason="drift_guard"
            )
            return await self._repo.get(request.document_id)

        return state


# --- helpers ----------------------------------------------------------------


def _tool_message(call_id: str, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _describe_validation(error: ValidationError) -> str:
    """A one-line, model-readable summary of what was wrong."""
    parts = []
    for item in error.errors()[:3]:
        location = ".".join(str(piece) for piece in item["loc"]) or "(root)"
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


def _consent_ref(name: str, args: BaseModel) -> str:
    """What a confirmation is *for*.

    The token is `tool:ref`, so this decides how narrow a "yes" is. `page` is
    included because without it every page-removal shares the empty ref -- and
    confirming "remove the blank page 3" would silently authorise removing
    page 1 later in the same turn.
    """
    for attribute in ("nid", "field", "page"):
        value = getattr(args, attribute, None)
        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
    return ""


def _grants_from_ops(applied: list[Any]) -> list[IntentGrant]:
    """Grants for nodes whose ids were minted during compilation.

    ``add_experience`` cannot name its own grant, because the id does not exist
    until the op is built. Reading it back off the applied op is the only place
    the real id is known.
    """
    grants: list[IntentGrant] = []
    for entry in applied:
        op = entry.op
        if op.get("op") == "insert_node":
            nid = (op.get("node") or {}).get("nid")
            if isinstance(nid, str) and nid.startswith(("exp_", "edu_", "prj_")):
                grants.append(IntentGrant(GrantScope.ENTRY_ADD, nid))
    return grants


#: Argument fields that carry a claim about the person: who they are, where
#: they worked, what they were called. Bullets and summaries are not here --
#: those are wording, and rewording is what the assistant is for.
_IDENTITY_FIELDS = (
    "value",
    "name",
    "company",
    "title",
    "institution",
    "degree",
)


def _was_asked_for(args: BaseModel, request: TurnRequest) -> bool:
    """Whether the person named these values themselves.

    The notice this feeds exists for identity edits nobody asked for -- a
    tailoring turn that quietly retitles a job, or changes an employer. Fired on
    every Tier C call it said the opposite of something useful: asked to add two
    named jobs, it reported back that the turn had "changed your identity or
    history", which is a warning about the instruction the person had just
    typed.

    The whole conversation counts, not just this message. Details arrive across
    turns -- the employer in one, the dates in the next -- and checking only the
    latest message would flag a job the person named two turns ago.

    A removal names no values, so it is always reported. That is the most
    consequential thing on this tier and the one worth reading twice.
    """
    values = [
        getattr(args, field, None)
        for field in _IDENTITY_FIELDS
    ]
    claimed = [
        value.strip() for value in values if isinstance(value, str) and value.strip()
    ]
    if not claimed:
        return False

    said = " ".join(
        [request.message, *(entry.get("content", "") for entry in request.history)]
    ).casefold()
    return all(value.casefold() in said for value in claimed)


def _keywords(job_description: str | None) -> list[str]:
    """Keywords from a job description, without a model call.

    Deliberately mechanical. Extracting these with the LLM would put an
    untrusted document's text through a generative step before it is used as a
    security check, which is precisely the wrong order.
    """
    if not job_description:
        return []
    words = [
        "".join(character for character in word if character.isalnum() or character in ".+#")
        for word in job_description.split()
    ]
    return [word for word in words if len(word) > 2]
