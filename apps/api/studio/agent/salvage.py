"""Recovering usable tool calls from imperfect model output.

The best local models sit around 75% on tool-call correctness, so malformed
calls are the normal case, not an exception. Treating them as errors produces a
product that fails a quarter of the time; treating them as *repairable* produces
one that mostly works.

The ladder, cheapest first. Every rung is a real failure observed from small
models, not a hypothetical:

1. Tool calls emitted as prose. Several Ollama chat templates put
   ``<tool_call>`` blocks in message content instead of the structured field.
   This is the highest-frequency local failure by a wide margin.
2. Near-JSON. Code fences, trailing commas, Python ``True``/``None``, single
   quotes, double-encoded argument strings.
3. Argument coercion. A scalar where a list belongs; an index where an id
   belongs; the old dotted path format from a model that learned on it.
4. Tool-name typos.

Anything still broken becomes a rejection carrying a hint, which the loop feeds
back so the model can correct itself.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Tool calls that arrived as text. Ordered most to least specific.
_EMBEDDED_PATTERNS = [
    re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL),
    re.compile(r"<\|tool_call\|>\s*(\{.*?\})", re.DOTALL),
    re.compile(r"```(?:json|tool_call)?\s*(\{.*?\})\s*```", re.DOTALL),
]

# A bare object mentioning a tool by name, with no wrapper at all.
_BARE_CALL = re.compile(
    r'\{\s*"(?:name|tool|function)"\s*:\s*"([\w.-]+)"\s*,\s*'
    r'"(?:arguments|args|parameters)"\s*:\s*(\{.*?\})\s*\}',
    re.DOTALL,
)

# The dotted/bracketed path format the previous engine used. Models that saw it
# in training reach for it, so translating beats rejecting.
_LEGACY_PATH = re.compile(r"^([a-zA-Z_]+)(?:\[(\d+)\])?(?:\.([a-zA-Z_]+))?(?:\[(\d+)\])?$")


@dataclass(frozen=True)
class SalvagedCall:
    name: str
    arguments: dict[str, Any]
    # How it was recovered, for telemetry: a rising repair rate is an early
    # warning that a model or chat template has regressed.
    via: str


def repair_json(raw: str) -> dict[str, Any] | None:
    """Parse near-JSON produced by a small model."""
    if not raw or not raw.strip():
        return None

    text = raw.strip()

    # Strip a code fence.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            value = _try_python_literal(candidate)
        if isinstance(value, dict):
            return _unwrap_double_encoded(value)
    return None


def _json_candidates(text: str) -> list[str]:
    """Progressively more aggressive repairs of ``text``."""
    candidates = [text]

    # Trailing commas before a closing brace or bracket.
    candidates.append(re.sub(r",\s*([}\]])", r"\1", text))

    # The first balanced object, when the model wrapped JSON in prose.
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for position in range(start, len(text)):
            character = text[position]
            if in_string:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    in_string = False
                continue
            if character == '"':
                in_string = True
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : position + 1])
                    break

    return candidates


def _try_python_literal(text: str) -> Any:
    """Handle a Python dict repr: single quotes, True/False/None."""
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None


def _unwrap_double_encoded(value: dict[str, Any]) -> dict[str, Any]:
    """Undo ``"arguments": "{\\"nid\\": ...}"``.

    Some providers JSON-encode the arguments object into a string and then
    encode that again, so a naive parse yields a dict whose value is a string.
    """
    for key in ("arguments", "args", "parameters"):
        inner = value.get(key)
        if isinstance(inner, str):
            try:
                decoded = json.loads(inner)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return decoded
    return value


def extract_embedded_calls(text: str, known_tools: set[str]) -> list[SalvagedCall]:
    """Find tool calls a model wrote into its prose."""
    if not text or "{" not in text:
        return []

    found: list[SalvagedCall] = []
    seen: set[str] = set()

    for pattern in _EMBEDDED_PATTERNS:
        for match in pattern.finditer(text):
            parsed = repair_json(match.group(1))
            if not parsed:
                continue
            name = str(parsed.get("name") or parsed.get("tool") or "")
            arguments = parsed.get("arguments") or parsed.get("args") or {}
            if isinstance(arguments, str):
                arguments = repair_json(arguments) or {}
            if name in known_tools and isinstance(arguments, dict):
                key = f"{name}:{json.dumps(arguments, sort_keys=True)}"
                if key not in seen:
                    seen.add(key)
                    found.append(SalvagedCall(name, arguments, via="embedded"))

    for match in _BARE_CALL.finditer(text):
        name = match.group(1)
        arguments = repair_json(match.group(2))
        if name in known_tools and isinstance(arguments, dict):
            key = f"{name}:{json.dumps(arguments, sort_keys=True)}"
            if key not in seen:
                seen.add(key)
                found.append(SalvagedCall(name, arguments, via="bare"))

    if found:
        logger.info("Recovered %s tool call(s) from message content", len(found))
    return found


def closest_tool(name: str, known: set[str], *, max_distance: int = 2) -> str | None:
    """Nearest tool name within an edit distance, or None."""
    if not name:
        return None
    if name in known:
        return name

    lowered = name.lower()
    for candidate in known:
        if candidate.lower() == lowered:
            return candidate

    best: tuple[int, str] | None = None
    for candidate in known:
        distance = _levenshtein(lowered, candidate.lower(), max_distance)
        if distance <= max_distance and (best is None or distance < best[0]):
            best = (distance, candidate)
    return best[1] if best else None


def _levenshtein(left: str, right: str, cap: int) -> int:
    """Edit distance, abandoning early once it exceeds ``cap``."""
    if abs(len(left) - len(right)) > cap:
        return cap + 1

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def resolve_legacy_path(path: str, doc: Any) -> str | None:
    """Translate a dotted/bracketed path into a node id.

    ``workExperience[0].description[1]`` is the previous engine's addressing
    scheme. Models reach for it from habit, and translating is strictly better
    than rejecting: the intent is unambiguous and the alternative is a wasted
    round trip.
    """
    match = _LEGACY_PATH.match(path.strip())
    if not match:
        return None

    section, section_index, field, field_index = match.groups()

    mapping = {
        "workexperience": doc.experience,
        "experience": doc.experience,
        "education": doc.education,
        "personalprojects": doc.projects,
        "projects": doc.projects,
    }
    entries = mapping.get(section.lower())

    if section.lower() == "summary":
        return doc.summary.nid if doc.summary else None

    if entries is None:
        return None

    position = int(section_index) if section_index is not None else 0
    if position >= len(entries):
        return None
    entry = entries[position]

    if field is None:
        return entry.nid

    if field.lower() in {"description", "bullets"}:
        bullets = getattr(entry, "bullets", None)
        if bullets is None:
            detail = getattr(entry, "detail", None)
            return detail.nid if detail else None
        if field_index is None:
            return bullets[0].nid if bullets else None
        index = int(field_index)
        return bullets[index].nid if index < len(bullets) else None

    return entry.nid


def coerce_arguments(
    name: str, arguments: dict[str, Any], doc: Any
) -> tuple[dict[str, Any], list[str]]:
    """Nudge plausible-but-wrong arguments into the expected shape.

    Returns the arguments plus a list of notes describing what was changed, so
    a rising coercion rate is visible rather than silently masking a prompt that
    has stopped working.
    """
    fixed = dict(arguments)
    notes: list[str] = []

    # A path where an id belongs.
    if "nid" not in fixed:
        for key in ("path", "target", "node", "id", "node_id"):
            value = fixed.get(key)
            if isinstance(value, str) and value:
                resolved = resolve_legacy_path(value, doc)
                if resolved:
                    fixed["nid"] = resolved
                    fixed.pop(key, None)
                    notes.append(f"resolved {key}={value!r} to {resolved}")
                    break

    # Common synonyms for the new value.
    if "value" not in fixed:
        for key in ("text", "new_value", "content", "new_text"):
            if key in fixed:
                fixed["value"] = fixed.pop(key)
                notes.append(f"renamed {key} to value")
                break

    # Synonyms for the optimistic-concurrency check.
    if "expect" not in fixed:
        for key in ("original", "old_value", "current", "old_text"):
            if key in fixed:
                fixed["expect"] = fixed.pop(key)
                notes.append(f"renamed {key} to expect")
                break

    # A scalar where a list belongs.
    if name in {"reorder_bullets", "reorder_skills"} and isinstance(
        fixed.get("order"), str
    ):
        fixed["order"] = [fixed["order"]]
        notes.append("wrapped order in a list")

    # A list where a scalar belongs.
    if isinstance(fixed.get("value"), list) and name in {"rewrite_text", "add_bullet"}:
        fixed["value"] = " ".join(str(item) for item in fixed["value"])
        notes.append("joined a list value into text")

    return fixed, notes
