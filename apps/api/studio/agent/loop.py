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

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from studio.agent.assembler import (
    AssembledCall,
    StreamFinished,
    TextEvent,
    ThinkingEvent,
    ToolCallAssembler,
)
from studio.agent.budget import BudgetExceeded, TurnBudget
from studio.agent.context import find, full_section, outline
from studio.agent.grounding import Grounder
from studio.agent.prompts import build_messages, repair_message
from studio.agent.salvage import (
    closest_tool,
    coerce_arguments,
    extract_embedded_calls,
    repair_json,
)
from studio.agent.tools import REGISTRY, ToolError, ToolRegistry, ToolSpec, tiers_for_message
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

        tiers = tiers_for_message(request.message)
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

        messages = build_messages(
            user_message=request.message,
            outline_text=outline(base_doc),
            history=request.history,
            job_description=request.job_description,
        )

        applied_total = 0
        rejected_total = 0
        status = "ok"
        repairs_per_call: dict[str, int] = {}

        try:
            while True:
                self._budget.start_iteration()
                channel.raise_if_cancelled()

                assembler = ToolCallAssembler()
                tool_messages: list[dict[str, Any]] = []
                executed_any = False

                async for chunk in self._backend.stream(
                    messages, tools=schemas or None
                ):
                    channel.raise_if_cancelled()

                    for event in assembler.feed(chunk):
                        if isinstance(event, TextEvent):
                            channel.emit(ev.AssistantDelta(text=event.text))
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
                            rejected_total += rejected
                            tool_messages.append(result_message)
                        elif isinstance(event, StreamFinished):
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
                        rejected_total += rejected
                        tool_messages.append(result_message)

                if not executed_any:
                    break

                messages.append(assembler.assistant_message())
                messages.extend(tool_messages)

        except Cancelled:
            status = "cancelled"
        except BudgetExceeded as limit:
            logger.info("Turn %s hit the %s budget", channel.turn_id, limit.limit)
            channel.emit(
                ev.Warning(source="budget", message=str(limit))
            )
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

        if spec.tier == "B" and name == "add_skill":
            verdict = grounder.check(args.skill, args.evidence)  # type: ignore[attr-defined]
            if not verdict.ok:
                return self._reject(
                    call, channel, "not_grounded", verdict.detail, spec=spec
                )

        if spec.tier == "C" and not self._consented(name, args, request):
            consent_id = uuid.uuid4().hex
            channel.emit(
                ev.ConfirmRequired(
                    call_id=call.call_id,
                    tool=name,
                    args=args.model_dump(mode="json"),
                    risk=_risk_of(name),
                    consent_id=consent_id,
                )
            )
            return (
                0,
                0,
                _tool_message(
                    call.call_id,
                    "Waiting for the user to confirm this change. Tell them what you "
                    "are about to do and why, then stop.",
                ),
            )

        try:
            ops: list[DocOp] = spec.compile(args, doc)
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
            self._budget.check_touch_ratio(len(NodeIndex(new_state.doc)))

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

        return (
            len(applied),
            len(rejected),
            _tool_message(
                call.call_id,
                f"Applied {len(applied)} change(s)."
                + (f" {len(rejected)} rejected." if rejected else ""),
            ),
        )

    def _read(
        self, name: str, args: BaseModel, doc: StudioDoc, call_id: str
    ) -> dict[str, Any]:
        if name == "find_text":
            matches = find(doc, args.query, args.limit)  # type: ignore[attr-defined]
            body = (
                "\n".join(
                    f"[{match['nid']}] ({match['kind']}) {match['text']}"
                    for match in matches
                )
                or "No matches."
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

    def _consented(
        self, name: str, args: BaseModel, request: TurnRequest
    ) -> bool:
        """Whether a Tier C call may proceed without an explicit prompt.

        Auto-approved only when the value being written literally appears in the
        user's own message this turn. That is the difference between "change my
        email to x@y.com" (they typed it, no prompt needed) and the model
        deciding on its own what the address should be.
        """
        if f"{name}:{_consent_ref(name, args)}" in request.consent_tokens:
            return True

        value = getattr(args, "value", None) or getattr(args, "company", None)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold() in request.message.casefold()
        return False

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


def _risk_of(name: str) -> str:
    return {
        "remove_entry": "This permanently deletes an entry.",
        "set_personal_info": "This changes your contact details.",
        "set_entry_identity": "This changes a factual claim about your history.",
        "add_experience": "This adds a job to your history.",
    }.get(name, "This is a significant change.")


def _consent_ref(name: str, args: BaseModel) -> str:
    for attribute in ("nid", "field"):
        value = getattr(args, attribute, None)
        if isinstance(value, str):
            return value
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
