# ResumeResume

An agentic resume editor: a chat sidebar beside a live document, where the AI
calls typed tools that mutate a structured document and the page re-renders as
each edit lands. Local-AI-first (Ollama by default), cloud providers optional.

The model here is Pencil's, not ChatGPT's. The AI does not stream prose into a
text box; it makes discrete, validated edits to a structured document, and you
watch them happen one at a time.

## Status

**P0 complete.** You can create a resume, edit it live in the browser, and
export a PDF. No AI yet, by design: the assistant arrives in P1 on top of an
engine that is already proven.

| Piece | State |
|---|---|
| Node identity, document schema | done |
| The 8 primitive ops + `apply_ops` gates | done |
| Legacy import/export | done |
| Persistence with version/ETag concurrency | done |
| API, PDF export, live document, inline editing | done |
| Generated TypeScript contract | done |
| Agent loop, tools, streaming | P1-P3 |

## Running it

```bash
make install
make api     # :8000
make web     # :3000  (separate terminal)
```

Open <http://localhost:3000>, click **New from sample**, edit a bullet, and hit
**Export PDF**.

## Why the engine came first

The risky part of this product is not the chat interface, it is that a small
local model will confidently emit a malformed edit and silently corrupt
someone's employment history. So the safety boundary is the engine, not the
prompt, and it was built and adversarially tested before anything could call it.

Three properties carry that weight:

**One mutation path.** Every tool call and every direct user edit compiles to
the same eight primitives, so there is exactly one function that changes a
document, one place that gates it, and one place to test.

**Authorization is derived, not declared.** An op's risk tier comes from what it
touches, never from what the caller claims, so a tool cannot mislabel itself
into a lower tier. A content-editing turn structurally cannot rewrite who you
are or delete a job, whatever the model emits.

**Ids, not paths.** Nodes carry stable ids whose prefix encodes their kind
(`exp_7f3a2`, `blt_9c21x`). Index paths break under concurrent inserts and force
a weak model to synthesise a path DSL; ids do neither, and let the engine reject
"set a bullet style on a company name" from the prefix alone.

## Running the tests

```bash
cd apps/api
uv sync --extra dev
uv run pytest
```

The suite is deterministic and makes no network or LLM calls. Two parts matter
most: the adversarial cases in `tests/unit/test_apply.py`, each one a real
failure shape from a small local model, and the Hypothesis properties in
`tests/property/`, which assert that *no* op sequence can corrupt a document.
That property suite found a genuine bug on its first run.

## Layout

```
apps/api/studio/doc/    the document engine (schema, ids, ops, gates)
apps/api/tests/         unit + property suites
docs/adr/               decisions worth their own record
```
