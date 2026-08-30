# 1. Address nodes by stable id, not by path

**Status:** accepted
**Date:** 2026-08-30

## Context

Resume-Matcher addresses edits with a dot-and-bracket path string:
`workExperience[0].description[1]`, gated by a regex allow-list of eight
permitted path shapes.

That design has three problems for conversational editing:

1. **Indices drift.** A queued edit misapplies if anything inserts ahead of it.
   With an agent making several edits per turn and a user typing at the same
   time, this is a routine race, not an edge case.
2. **It makes the model synthesise a DSL.** A local model in the ~75%
   tool-reliability band has to construct a path with correct bracket syntax and
   correct indices. Every character is a chance to be wrong.
3. **Authorization and addressing are the same mechanism.** The allow-list
   answers "may the AI rewrite this?" using the same regex that locates the
   value. Extending the editable surface therefore means weakening the
   protection, for every call, including ones the user never asked for.

## Decision

Every addressable node carries a stable id whose prefix encodes its kind:
`exp_7f3a2`, `blt_9c21x`, `skl_4d10p`. Ids are minted server-side, survive
reordering and template changes, and are never reused.

Authorization moves to a separate axis: an op's tier is derived from what it
touches, and is checked independently of how the node was located.

## Consequences

Good:

- Concurrent inserts cannot cause a misapply.
- Kind-checking is free: `apply_ops` rejects "set a bullet style on a company"
  from the prefix, with no document lookup, and a weak model can sanity-check
  its own reference before emitting it.
- Editable surface and safety are now independent. Adding the ability to delete
  a bullet no longer requires loosening the rule that protects employment dates.
- An unknown id can be answered with *nearby candidates*, which is what lets a
  weak model self-correct rather than give up.

Costs:

- Ids must be minted on import and carried through persistence.
- The old path format still appears in model output from habit, so the salvage
  ladder resolves a path string to an id rather than rejecting it outright.
- Documents are not interchangeable with the old shape without conversion; see
  `studio/doc/legacy.py`.
