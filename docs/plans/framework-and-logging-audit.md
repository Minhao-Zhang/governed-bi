# Framework best practices, and one logging spine

> **STATUS 2026-07-31 — LOAD-BEARING. Largest un-migrated block in `docs/plans/`.**
>
> Ten findings here were checked against the **vendors' own current docs**, not against this
> repo's assumptions. Re-deriving them means re-reading those docs, so this file is the cheapest
> copy. A grep of [rebuild-checklist.zh.md](rebuild-checklist.zh.md) returns **zero hits** for
> `RetryPolicy`, `get_stream_writer`, `on_event`, `durability`, and `Store` — all of them live
> only here.
>
> Still to migrate: §1 (the Langfuse `mask` finding, including the `mask_otel_spans` fix skeleton
> and "add a test that a long tool output is truncated") → 5.3.5, same root cause · §0 (dependency
> floors: `langfuse>=3.0` pin actually running 4.14; both checkpoint packages a major behind) →
> 1.5, which currently only bounds the `langgraph-*` trio · §G1 (`RetryPolicy` unused anywhere
> while `error_type` exists precisely to separate a provider 429 from our own bug) → new X item ·
> §G4 (`get_stream_writer()` called once at `graph_app.py:132` then threaded through five
> signatures, so REST `/chat` gets no timeline at all) → merge with build-sequence ARCH-3.3 into
> one item · §D3 (curator has no checkpointer; a budget-exhausted 57-schema build is discarded) ·
> §8.4's four record-worthy prints → 5.2 · §7's thirteen-sink table → 3.1, which currently only
> cites the number "thirteen" · §6 (`Store` usage is zero, so D8's memory answer is `Store` not a
> bespoke table), §G2 (serve-turn checkpointer scope), §D1, §G6, §8.5 → gate/non-goals ·
> §10's "six things not to change".
>
> Absorbed already: §L1 / §8.2 / §8.3 → checklist 3.1 verbatim · §D4 (`skills=`) and §8.7 (four
> observability channels) → non-goals.

Two things in one plan, because they turned out to be the same problem seen twice.

1. A **best-practice scan** of every place this repo touches LangGraph, LangChain
   middleware, DeepAgents, Langfuse, checkpointers, and memory — against the current
   skills and the current vendor docs, not against memory.
2. A design for **logging everything through one spine**, because the scan's largest
   finding is that we have thirteen sinks with no shared correlation key and 105
   `print()` calls, and the external tracers carry no run identity at all.

Nothing is in production and nothing can break, so this plan is free to propose
deletion and re-wiring rather than additive compatibility.

**Method.** Loaded and followed `ecosystem-primer`, `langgraph-fundamentals`,
`langgraph-persistence`, `deep-agents-core`, `deep-agents-memory`,
`langchain-middleware`, and `langfuse`. The Langfuse skill mandates docs-first
("NEVER implement based on memory"), so every Langfuse claim below is checked against
`langfuse.com/docs` as of 2026-07-30 and cited. Every claim about our code carries a
`file:line` verified at `2187ead`.

**Supersedes nothing.** This extends [ADR 0004](../adr/0004-local-first-conversation-run-logging.md)
(Accepted; M5 in progress), whose scope was the *local-first* sink. The external
tracers were explicitly out of that ADR's scope and are where most of §2 lands.

---

## 0. Version drift

| Package | Pinned in `pyproject.toml` | Resolved in `uv.lock` | Note |
|---|---|---|---|
| `langfuse` | `>=3.0` | **4.14.0** | **v4 is a platform migration.** `obs.py` comments describe v3 behaviour throughout (`obs.py:124`, `:174`) |
| `langgraph` | `>=1.0` | 1.2.8 | fine |
| `langgraph-checkpoint-sqlite` | `>=2.0` | **3.1.0** | pin is a major version behind what resolves |
| `langgraph-checkpoint-postgres` | `>=2.0` | **3.1.0** | same |
| `langchain` | `>=1.0` | 1.3.12 | fine |
| `deepagents` | `>=0.6` | 0.6.12 | fine |
| `langsmith` | (transitive) | 0.10.0 | not pinned; arrives via langchain |

**Action:** raise the floors to what we actually run and test against
(`langfuse>=4.14`, `langgraph-checkpoint-sqlite>=3.1`, `langgraph-checkpoint-postgres>=3.1`).
A `>=3.0` pin that resolves to 4.14 means a fresh lock could legally install v3 and
silently change tracer semantics.

---

## 1. Headline finding: the Langfuse content mask does not mask our content

**Severity: high. This is a privacy claim the code makes and does not keep.**

`obs.py:81–111` defines `_trace_mask` and `obs.py:132–137` installs it:

```python
from langfuse import Langfuse
Langfuse(mask=_trace_mask)
```

The docstring states the purpose plainly (`obs.py:84–88`):

> The Langfuse callback auto-captures full run inputs/outputs — including
> `run_query` / `sample_rows` tool messages that carry live DB row previews and the
> governed context. A governed BI product should not ship that verbatim to a third
> party, so long strings (where row/context dumps live) are truncated.

Per the current Langfuse masking docs, the Python SDK has **two** hooks with
different coverage:

| Hook | Status | Covers |
|---|---|---|
| `mask_otel_spans` | **Recommended** | raw OpenTelemetry span attributes from Langfuse SDK spans **and third-party instrumentations** exported by this client |
| `mask` | **Legacy** | only data set through Langfuse SDK APIs — `start_observation()`, `update()`, `set_trace_io()`. It "does not inspect final raw OpenTelemetry span attributes from third-party instrumentations" |

The Langfuse **LangChain `CallbackHandler` is third-party instrumentation**. We attach
it as a LangChain callback (`obs.py:139`, spliced in at `agent.py:1478`) and never call
a Langfuse SDK API ourselves. So the legacy hook we installed sees none of the data the
docstring says it truncates.

**Conclusion: `run_query` and `sample_rows` tool messages — live database row previews
and the full governed context block — are exported to Langfuse verbatim today.**
`GOVERNED_BI_TRACE_MAX_CHARS` appears to control this and does not.

There is a second, smaller problem in the same six lines. `Langfuse(mask=...)` is
called *as a side effect* to reconfigure a singleton, inside a `try/except` that
swallows failure to a `debug` log. If a client already exists in the process — which it
will under `langgraph dev`, where the server may initialise one — the constructor may
not re-apply the option, and we would not know.

**Fix.** Replace with `mask_otel_spans`, constructed explicitly and once:

```python
from langfuse import Langfuse
from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch

def _mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult | None:
    patches = {}
    for identifier, span in params.spans.items():
        # truncate/delete the attributes carrying tool IO and prompt content
        ...
    return MaskOtelSpansResult(span_patches=patches)

Langfuse(mask_otel_spans=_mask_otel_spans, ...)
```

Then **add a test that asserts a long tool output does not survive the mask** — the
current gap is precisely the kind that a docstring hides and no test catches. Until
that lands, `docs/` should stop claiming Langfuse is the masked option
(`obs.py:16`, "Prefer Langfuse where content masking matters").

---

## 2. Langfuse — the rest

### L1. No trace carries a session, a user, or a tag

`obs.py:139` constructs `CallbackHandler()` with no arguments, and no call site ever
passes Langfuse metadata. Per the current docs, the handler **no longer accepts
constructor arguments** like `session_id`; trace attributes are set per invocation via
`langfuse_`-prefixed metadata keys in the LangChain config, or via
`propagate_attributes`:

```python
chain.invoke(inputs, config={
    "callbacks": [handler],
    "metadata": {
        "langfuse_session_id": "...",
        "langfuse_user_id": "...",
        "langfuse_tags": ["..."],
    },
})
```

Consequences today: Langfuse traces cannot be grouped into a conversation, cannot be
filtered by identity, and **cannot be joined to our own `run_id` / `turn_id`**. We have
a serve path that already knows `session_id` (`agent.py:490`, `:1493`), `identity`,
`run_id`, `turn_id`, `corpus_pin`, and the arm — and passes none of it to the tracer.

This is the single change that makes §7's unified logging possible, and it is small.

### L2. Nothing is ever scored

We compute, per question, everything Langfuse scores exist to hold: `correct`,
`correct_strict`, `ex_gradeable`, `outcome`, `failed_stage`, `failed_layer`,
`semantic_assurance`, `safety_clearance`, `decoy_touch`. All of it lands in our JSONL
and none of it reaches the trace. The Langfuse skill has a whole reference for this
(`references/user-feedback.md`, `docs/scores/*`).

Pushing eval verdicts as scores keyed on the trace is what turns Langfuse from "a place
traces go" into the one surface where a run's *trace*, *verdict*, and *cost* meet.
It also gives the interaction-signal work (D5 / R3) its natural v0 home, which
`glossary.md` already says: "v0 rides Langfuse/LangSmith trace feedback".

### L3. `flush_tracing()` is right, and its comment is out of date

`obs.py:169–184` flushes explicitly because the SDK exports on a background thread with
an `atexit` hook that `SIGTERM` / `os._exit` bypasses. That is correct practice for
short-lived processes and it is called from the eval/curator/CLI paths. The docstring
says "the Langfuse v3 SDK"; we run v4. Reword, keep the behaviour.

### L4. Import fallback chain is now dead weight

`obs.py:124–131` tries `langfuse.langchain` then falls back to `langfuse.callback`
(v2). We require `>=3.0` and run 4.14. Delete the v2 branch; it can only mask a real
import error as a working no-op.

---

## 3. LangSmith

### S1. Two tracers, zero correlation

LangSmith instruments itself from the environment (`obs.py:55–72`) and Langfuse rides a
callback. Both can be on at once. Neither carries our `run_id`. So a single serve turn
can produce a LangSmith trace, a Langfuse trace, a `stage_events.jsonl` row, and a
durable run-log row, with **no key that joins any two of them**.

LangSmith reads standard LangChain `config` `metadata` and `tags`. Langfuse reads the
same `metadata` dict via its `langfuse_`-prefixed keys. So **one dict serves both** —
put plain `run_id` / `turn_id` / `corpus_pin` / `arm` keys in for LangSmith and the
`langfuse_*` aliases in for Langfuse, in the same `metadata`, at the same call site.

### S2. The unmasked-content warning is the right shape

`langsmith_enabled()` warns once per process that LangSmith has no mask hook and
requires `GOVERNED_BI_ALLOW_UNMASKED_LANGSMITH` to silence — "an acknowledgement, not a
control" (`obs.py:15`). That is honest and worth keeping. Note the irony §1 creates: the
warning says to prefer Langfuse for masking, and Langfuse is currently unmasked too. Fix
§1 before this sentence is true.

---

## 4. LangGraph

### G1. No `RetryPolicy` on any node — and we built the telemetry for the failure it handles

`grep` for `RetryPolicy` / `retry_policy` across `src/`: **zero hits.** The
`langgraph-fundamentals` error-handling table maps transient failures (network, rate
limits) to `add_node(..., retry_policy=RetryPolicy(max_attempts=3))`.

Every node in the serve rails and the chat graph is registered bare
(`agent.py:1410–1422`, `graph_app.py:199–203`). Meanwhile `eval/arms.py:~460` added an
`error_type` field with this justification:

> Those classify as crashes, so a wave of them already blocks quotability — but without
> the type an operator sees only "crash_rate 0.4" and cannot tell a provider rate limit
> from a bug in us.

So we instrumented the exact failure class `RetryPolicy` exists to absorb, and never
absorb it. This also pairs with the reference-book audit's U-6 (no circuit breaker /
model fallback chain) — same gap, two framings. On a 69-schema pooled run at
`--workers > 1`, a transient 429 currently costs a question and counts as a crash.

**Fix:** `RetryPolicy` on the model-calling nodes (`agent_core`, `narrate`) and on the
curator's invoke path. Cheap, and it directly improves eval trustworthiness.

### G2. The outer rails compile without a checkpointer, and clarification gets a second, hand-derived thread

`agent.py:1422` is `builder.compile()` — no checkpointer. Persistence for the
clarification interrupt lives on the **inner** agent instead
(`agent.py:1094`, `checkpointer=clarify_checkpointer`), driven by a separately derived
`clarify_thread` (`graph_app.py:148–150`, a per-turn hash).

Per `langgraph-persistence`, this is the subgraph-checkpointer-scoping decision, and
we are making it by hand: two persistence scopes and two thread ids for one logical
turn, with a comment at `graph_app.py:141–142` noting that "the clarify checkpointer is
process-global, so a caller sending a colliding thread_id could land on a victim's
paused clarification". A hash is the mitigation for a namespacing problem the framework
already solves by nesting.

Consequences beyond neatness:
- The rails have **no state history**, so no time travel and no replay of a serve turn.
- An interrupted turn resumes the inner agent but the rails re-run from `START`
  (`ingest` → `refuse_gate` → `assemble` all redo their work, including retrieval).
- ADR 0001 says the LangGraph Server injects persistence for the outer chat graph, which
  is true for `graph_app` — but the rails compiled inside `answer` are a separate,
  unpersisted graph.

**Fix (design decision, not a one-liner):** either compile the rails with the injected
checkpointer and let `ask_user`'s `interrupt` propagate up through one thread, or record
why the two-scope split is deliberate. Right now it reads as accretion.

### G3. `ServeRailsState` has no reducers — currently safe, and the code knows it

`ServeRailsState(TypedDict, total=False)` (`agent.py:162–174`) uses no `Annotated`
reducers. The graph is a linear chain with two conditional edges to `END` and no
parallel branches, so last-write-wins is correct today. `agent.py:1241` says so:

> (…will bite once a checkpointer or a parallel branch is added)

Contrast `middleware.py:59–61`, which does it properly:

```python
licensed: Annotated[list, operator.add]
ledger: Annotated[list, operator.add]
token_usage: Annotated[list, operator.add]
```

**No action** beyond keeping that comment adjacent to the state class rather than 1,000
lines away. If G2 adds a checkpointer, revisit.

### G4. `get_stream_writer` is called in exactly one place and threaded as a parameter

`graph_app.py:132` is the repo's only `get_stream_writer()` call. The writer is then
passed as `on_event=writer` (`:174`) into `GovEventStream`, and from there through five
signatures. The `langgraph-fundamentals` pattern is for the node that emits to call
`get_stream_writer()` itself.

This has a live symptom already recorded in the architecture review: `api/app.py`'s
`/chat` never passes `on_event`, so **the REST profile silently produces no timeline**,
while the LangGraph-Server profile does. Two entry points, two behaviours, one
undocumented difference — caused by making the writer a parameter instead of an ambient.

**Fix:** have the emitting code call `get_stream_writer()` (it returns a no-op outside a
streaming context), and delete `on_event` from the five signatures. Both profiles then
behave identically by construction. This also collapses part of candidate 2 in the
architecture review.

### G5. `Command` usage is correct

`Command(resume=...)` for interrupt resume (`agent.py:1144`) and `Command(update=...)`
returns from `wrap_tool_call` to short-circuit a blocked tool with a state update
(`middleware.py:309, 351, 403, 472, 490`; `tools.py:361, 371`). This matches both the
LangGraph and middleware skills. No static-edge/`goto` conflict exists because we never
use `goto`. **Keep.**

### G6. `durability` is never set

LangGraph 1.x exposes a `durability` mode on invoke/stream (`"exit"` / `"async"` /
`"sync"`). We never set it, so we take the default. For the eval drivers — where a
crash mid-run is a measurement event — `"sync"` on the checkpointed paths is worth
evaluating once G2 is settled. Low priority, noted so it isn't rediscovered.

---

## 5. DeepAgents (the Curator)

### D1. No `store`, so the curator cannot remember anything across schemas

`build_curator_agent` (`curator/deep_agent.py:293–333`) passes `model`, `tools`,
`system_prompt`, optionally `backend` and `checkpointer`. It never passes `store`.

Per `deep-agents-memory`, `store` is what makes anything persist across threads, and
the harness-native pattern for "some files ephemeral, some durable" is a
`CompositeBackend` routing a prefix to `StoreBackend`:

```python
backend = lambda rt: CompositeBackend(
    default=StateBackend(rt),
    routes={"/memories/": StoreBackend(rt)},
)
agent = create_deep_agent(backend=backend, store=store, ...)
```

This matters more than it looks. A pooled build walks **57–69 schemas in one run** and
learns the same lessons repeatedly (naming conventions, decoy shapes, which probes are
worth the step budget) with no way to carry one schema's finding to the next.

### D2. `FilesystemBackend` is configured correctly

`FilesystemBackend(root_dir=str(Path(run_dir)), virtual_mode=True)`
(`deep_agent.py:315`). `virtual_mode=True` is exactly the skill's guidance — it blocks
`../` and `~/` escapes. And the skill's security note ("Never use FilesystemBackend in
web servers") is respected: this is the offline curator, not the API. **Keep, and say so
in the docstring** so nobody "simplifies" it later.

### D3. The curator has no checkpointer, so an exhausted step budget discards the work

`checkpointer` is only forwarded when not None (`deep_agent.py:330–331`), and
`pipeline.py:1145–1155` documents at length why none is created. The consequence is
concrete and recent: `079d1fe` — *"the step budget that cost 30 of 57 schemas"*. With a
checkpointer, a budget-exhausted build is resumable; without one, the run is lost.

The counter-argument in `pipeline.py` is about eval hygiene (a resumed build is not a
clean build). That is a real concern, and it argues for **making resume explicit and
recorded in the manifest**, not for having no durability at all.

### D4. `interrupt_on`, `skills`, and `subagents` are all unused — two of the three defensibly

- **`interrupt_on`** — not used. Our human gate is the PR review of a corpus diff, which
  is a stronger gate than a tool-call approval and works offline. **Deliberate; record
  it.** (Both `deep-agents-core` and `langchain-middleware` note `interrupt_on` needs a
  checkpointer, which D3 says we don't have — so this is also blocked on D3.)
- **`skills`** — not used. Do **not** confuse this with the repo's retired `skill` asset
  (ADR 0003 / D17 replaced that with `NoteAsset`); DeepAgents skills are
  progressive-disclosure `SKILL.md` directories loaded on demand. This is a genuine
  option we have not evaluated: the curator's per-dialect and per-domain instructions
  are currently prompt text carried in `prompts/registry.py`, and a skill directory is
  the harness-native way to load them only when relevant. Worth a real look, because
  prompt size is a step-budget cost.
- **`subagents`** — not used. Phase A and Phase B are two separate `create_deep_agent`
  invocations with different prompts (`pipeline.py:1157–1166` and `:1558–1567`, which the
  architecture review found are near-duplicate factories). That is close to what
  `subagents` models. Worth evaluating; not obviously better.

### D5. Two agents are built by hand where the harness would compose them

`pipeline.py` builds the same `make_agent` closure twice with only the prompt swapped.
`deep-agents-orchestration`'s `SubAgentMiddleware` exists for exactly this shape. Low
urgency, but it is the same duplication the architecture review flagged from the other
direction.

---

## 6. Persistence and memory: `Store` is the missing primitive

**`grep` for `InMemoryStore`, `PostgresStore`, `BaseStore`, `runtime.store`, `store=`
across `src/`: zero hits.**

This is the most consequential framework finding after §1, because of what it means for
work already designed:

- **D8 / the four-layer memory.** `Profile`, `Episodic`, and `Correction` are design,
  not code, and their empty protocols were deleted 2026-07-28. The reference book
  implements them as rows in a `ttd_memory_records` table with hand-rolled TTLs. **The
  LangGraph-native answer is `Store`** — `put/get/search/delete` over namespaced keys,
  with `PostgresStore` for durability. Building a bespoke table would be
  re-implementing it.
- **Cross-thread anything.** Working memory is a checkpointer (correct — that is
  short-term, thread-scoped). Everything the glossary calls cross-session is long-term,
  which is `Store`'s job, accessed via `runtime.store` in a node or `ToolRuntime` in a
  tool.
- **The curator's cross-schema memory** (D1) is the same primitive.

**Recommendation:** when memory is next picked up, the first decision is already made —
it is `Store`, not a table. Write that down so the design conversation starts one step
further along.

### Checkpointer hygiene, which is good

`run_log.py:153–275` builds `SqliteSaver` / Postgres savers with a documented
close protocol (`close_checkpointer`, with a 19-line docstring about Windows file
handles and the eval curator) and `_secure_store_perms`. `thread_id` is always
provided where a checkpointer is used (`agent.py:490`, `:1120`, `:1493`;
`graph_app.py:127`). The `langgraph-persistence` skill's two big footguns — missing
`thread_id`, and `InMemorySaver` in production — we avoid. **Keep.** The one structural
complaint is cohesion, not correctness: four checkpointer factories live in a module
named `run_log` (architecture review, candidate 9).

---

## 7. What is logged today — thirteen sinks, no shared key

Inventory, from the code rather than from docs:

| # | Sink | Written by | Grain | Carries `run_id`? |
|---|---|---|---|---|
| 1 | `runs/index.jsonl` | `eval/index.py` | one per eval run | yes |
| 2 | `generations.<arm>.jsonl` | eval drivers | one per (question, arm) | yes (70-key row) |
| 3 | `stage_events.jsonl` | `run_datalake.py:4558` | one per (question, arm, stage) | yes |
| 4 | `summary.json` / `analysis.json` | eval drivers | one per run | yes |
| 5 | `run_manifest.json` | `curator/pipeline.py:670` | one per curator build | yes |
| 6 | `curator_trace.jsonl` / `curator_sme_trace.jsonl` | `pipeline.py:572` | one per agent step | yes |
| 7 | `validate_findings.jsonl` | `pipeline.py:697` | one per finding | yes |
| 8 | `clarifications.jsonl` / `sme_clarifications.jsonl` | `clarifications.py:216` | one per question | partial |
| 9 | `adversary_findings.jsonl` | `curator/adversary.py` | one per finding | yes |
| 10 | durable run log (sqlite \| jsonl \| off) | `analyst/run_log.py` | one per serve turn | yes (`turn_id` UPSERT key) |
| 11 | LangGraph checkpoints (sqlite/pg) | framework | one per super-step | `thread_id` only |
| 12 | **Langfuse traces** | callback | one per turn | **no** |
| 13 | **LangSmith traces** | env | one per turn | **no** |

Plus two non-sinks that carry real information:

- **`print()` — 105 calls** across 14 modules (`run_datalake.py` 57, `index.py` 12,
  `run_experiment.py` 9, `pipeline.py` 6, `loader.py` 3, `asset_bag.py` 1, …).
- **`logger.` — 32 calls**, and **`logging.basicConfig` appears nowhere in `src/`.**

That last pair is the root cause, and the codebase already diagnosed it. Two comments
say it outright:

> `agent.py:621` — Printed, not logged: nothing in `src/` calls `logging.basicConfig`, so a
> `logger.warning` here would be swallowed by default.

> `run_log.py:498` — …to `logger.exception` and there is no `logging.basicConfig` anywhere in…

A library correctly does not call `basicConfig`. But **no entry point does either** — not
the API app, not the eval drivers, not the CLIs. So every diagnostic that must be seen
was written as `print()`, and every diagnostic written as `logger` is invisible. The
result is that facts of identical importance land in different places by accident: the
architecture review found `seed_stats` and `ledger_repairs` in `run_manifest.json` while
the seed-collapse count and the dropped-caveat ids only reach stdout — and
`asset_bag.py:1115` argues at length that a dropped caveat must be "on the record"
before printing it.

---

## 8. The spine: one identity, five adapters

The ask is "everything logged, in a unified way (or more than one way if easier)". More
than one sink is genuinely easier and genuinely better — a JSONL ledger, a trace UI, and
a console are different tools. What is missing is not a single sink. **It is a single
identity, present in all of them.**

### 8.1 The identity

Three fields we already produce, promoted to a required context object:

| Field | Meaning | Exists today |
|---|---|---|
| `run_id` | one per process invocation (serve turn, eval run, curator build) | `new_run_id()` |
| `turn_id` | one per question within a run | `run_log`'s UPSERT key; in the row register |
| `corpus_pin` | corpus content identity | on the manifest and the row |

Plus, where applicable: `arm`, `schema`, `prompt_set_hash`, `identity`.

This is deliberately **not** a new concept. Every one of these already exists; the plan
is that a single `RunContext` record carries them and every sink adapter reads from it —
which is also the architecture review's candidate 1 (`GenerationRow`) and candidate 2
(`ServeDeployment`) seen from the logging side. Do that work once.

### 8.2 Adapter 1 — the external tracers (the missing 90%)

One helper, called at every run boundary that already splices `tracing_callbacks()`:

```python
def tracing_config(ctx: RunContext) -> dict:
    """LangChain config that attributes a run in BOTH tracers."""
    return {
        "callbacks": tracing_callbacks(),
        "run_name": f"{ctx.kind}:{ctx.arm or 'serve'}",
        "tags": [ctx.kind, ctx.arm, ctx.corpus_pin],          # LangSmith reads these
        "metadata": {
            "run_id": ctx.run_id,                              # LangSmith
            "turn_id": ctx.turn_id,
            "corpus_pin": ctx.corpus_pin,
            "prompt_set_hash": ctx.prompt_set_hash,
            "langfuse_session_id": ctx.run_id,                 # Langfuse
            "langfuse_user_id": ctx.identity,
            "langfuse_tags": [ctx.kind, ctx.arm, ctx.corpus_pin],
        },
    }
```

One dict, both tracers, verified against the current Langfuse docs (§L1) and standard
LangChain config semantics. After this, a Langfuse trace and a `stage_events.jsonl` row
are joinable by `run_id`, which is the whole point.

Call sites to convert: `agent.py:1478`, `graph_app.py:174`,
`arms.py:436`, `oracle.py:362`, `refuse_gate.py:71`, `pipeline.py` (curator invoke),
`sme.py`, `scripts/live_smoke.py`.

### 8.3 Adapter 2 — `configure_logging()` at every entry point

One function in `governed_bi/logging_setup.py`, called by the API app factory, every
eval driver `main()`, and every CLI. The library keeps calling `logger.*` and keeps not
configuring anything.

- JSON-lines formatter to a file, human formatter to stderr.
- A `logging.Filter` that injects `run_id` / `turn_id` from a `ContextVar`, so **every
  log record is correlated without threading the context through call signatures.**
- `-v` / `GOVERNED_BI_LOG_LEVEL` to set the level.

This alone makes the 32 existing `logger.` calls visible for the first time.

### 8.4 Adapter 3 — split `print()` into two intents

105 prints are not all wrong. An eval driver printing progress to a human watching a
long run is legitimate. What is wrong is that *diagnostics* and *operator output* use the
same mechanism, so diagnostics are unfilterable and unfileable.

- `report(...)` — operator-facing progress, stdout, deliberate, **and tee'd to the run's
  log file** so a finished run's console output is recoverable.
- `logger.*` — everything that is a fact about the run rather than a message to a
  watching human.

Triage, not a blanket rewrite: the ~57 in `run_datalake.py` are mostly `report`; the
ones the architecture review identified as facts-that-should-be-on-the-record
(`asset_bag.py:1121` dropped caveats, `pipeline.py:1100` seed collapse, `pipeline.py:868`
reference repairs, `loader.py:127` skipped corpus files) become both a `logger.warning`
**and** a manifest field.

### 8.5 Adapter 4 — Langfuse scores carry the verdict

After 8.2 gives every trace a `run_id`, push the verdicts we already compute as Langfuse
scores: `correct`, `ex_gradeable`, `outcome`, `failed_layer`, `semantic_assurance`,
`safety_clearance`. Per the Langfuse skill's `references/user-feedback.md` and the scores
docs. This is what makes the trace UI answer "show me the traces that got it wrong",
which is currently a JSONL grep followed by manual trace hunting.

It is also the v0 interaction-signal sink the glossary already promises.

### 8.6 Adapter 5 — one manifest field per fact, no exceptions

The rule that prevents §7's drift from recurring: **a fact about a produced artifact goes
in that artifact's manifest.** If it is worth printing, it is worth a field. The
architecture review's U-10 (row cap / timeout not in `Settings`, so not in any manifest)
is the same rule violated at the config layer.

### 8.7 What this does not require

No Kafka, no Prometheus, no `ObservabilityFacade`, no fourth channel. The reference-book
audit already concluded our two-tracer + ledger setup answers the same questions with
three fewer services. The spine is an identity and five small adapters, not new
infrastructure.

---

## 9. Worklist, ordered

| # | Item | Severity | Size | Why this order |
|---|---|---|---|---|
| 1 | Replace legacy `mask` with `mask_otel_spans`; add a test that a long tool output is truncated; correct `obs.py`'s claims | **High — privacy** | S | The code asserts a protection it does not provide (§1) |
| 2 | `tracing_config(ctx)` — attribute every trace in both tracers (§8.2) | High | S | Unlocks everything else; ~8 call sites |
| 3 | `configure_logging()` at every entry point + ContextVar correlation filter (§8.3) | High | S/M | Makes 32 existing log calls visible; precondition for 4 |
| 4 | Raise dependency floors to what we run (§0) | Medium | XS | A `>=3.0` pin resolving to 4.14 can silently regress |
| 5 | `RetryPolicy` on model-calling nodes (§G1) | Medium | S | We measure the failure and never absorb it; improves eval trust |
| 6 | `report()` / `logger` split + manifest fields for the four record-worthy prints (§8.4, §8.6) | Medium | M | Triage, not a rewrite |
| 7 | Langfuse scores from eval verdicts (§8.5) | Medium | M | Depends on 2 |
| 8 | Decide G2: one checkpointer scope for the serve turn, or record why two | Medium | M/L | Design decision, not a patch |
| 9 | `get_stream_writer()` in the emitting node; delete `on_event` threading (§G4) | Medium | M | Fixes the REST-vs-Server timeline asymmetry |
| 10 | Curator `store` + `CompositeBackend` for cross-schema memory (§D1) | Medium | M | Also the answer to D8 when memory returns |
| 11 | Curator checkpointer with explicit, manifest-recorded resume (§D3) | Medium | M | A budget-exhausted 57-schema build is currently discarded |
| 12 | Evaluate DeepAgents `skills=` for curator instructions (§D4) | Low | M | Prompt size is a step-budget cost; needs measurement |
| 13 | Delete the v2 Langfuse import fallback; reword the v3 comments (§L3, §L4) | Low | XS | Dead code that can mask a real import error |
| 14 | Evaluate `durability="sync"` on checkpointed eval paths (§G6) | Low | S | Note so it isn't rediscovered |

Items 1–3 are one sitting and change the most.

## 10. What not to change

- **`Command` usage** (§G5) and **`middleware.py`'s `Annotated` reducers** — both already
  correct.
- **`thread_id` discipline** and the **checkpointer close protocol** — the two classic
  persistence footguns, both avoided (§6).
- **`FilesystemBackend(virtual_mode=True)`** and the curator's *enforced-by-absence* write
  surface — both deliberate governance, both tested (§D2).
- **`flush_tracing()`** — correct for short-lived processes; only the comment is stale.
- **The LangSmith unmasked-content acknowledgement** — the right shape for a control we
  cannot implement (§S2).
- **Not adopting four observability channels.** Settled by the reference-book audit.

---

## Provenance

Skills followed: `ecosystem-primer` → `langgraph-fundamentals`, `langgraph-persistence`,
`deep-agents-core`, `deep-agents-memory`, `langchain-middleware`, `langfuse`.

Langfuse docs consulted 2026-07-30 (the skill forbids implementing from memory):
`docs/integrations/langchain/tracing`, `docs/observability/features/masking`,
`docs/observability/features/sessions`, and the `llms.txt` index. The masking coverage
distinction in §1 is quoted from the masking page's own comparison table.

Repo state `2187ead`. Version facts from `pyproject.toml` and `uv.lock`. Counts
(`105` prints, `32` logger calls, zero `Store` / `RetryPolicy` hits) are `grep` over
`src/` and reproducible.
