# ADR 0010 — Live stage events: the wire contract for a visible turn

Status: Accepted (2026-08-04). Supersedes ADR 0007 §5, which decided the shape but recorded
three facts about the transport that measurement has now falsified.

## Context

A turn takes 30–120 seconds and the interface showed nothing until it ended. `/capabilities`
reported `can_stream: false`, so the UI mounted `<RestChat/>` and `POST /chat`, a single
blocking `graph.invoke`. About 900 lines of timeline UI rendered nothing, `can_clarify` was
gated off the same flag so `ask_user` was unreachable, and the honest reason was that nothing
in v2 emitted a custom event.

ADR 0007 §5 decided how to fix it. Before building it, the transport was measured against
`langgraph` 1.2.8 / `langgraph-api` 0.11.0 / `@langchain/langgraph-sdk` 1.9.25. Three of §5's
premises were wrong, and each would have produced a plausible-looking empty timeline.

## The measurements

**M1 — the wire field is `stream_subgraphs`, not `subgraphs`.** `POST
/threads/{id}/runs/stream` reads `payload.get("stream_subgraphs", False)`
(`langgraph_api/models/run.py:341`) and forwards it to `astream(subgraphs=...)`. A request
carrying `subgraphs: true` is accepted with HTTP 200 and **silently ignored** — no error, no
warning. Two runs were measured that way before the field name was found. The JS SDK spells it
`streamSubgraphs`, which is the same name.

**M2 — `stream_subgraphs` is load-bearing for both tokens and tools, and this is the finding
that matters.** `agent_core` calls `agent.invoke(...)` on a nested `create_agent` graph, so
every model token and every tool call of the turn happens *inside a subgraph*. Measured on one
question, same graph, same model:

| `stream_subgraphs` | `messages/partial` | namespaced `updates` |
| --- | --- | --- |
| absent / false | **0** | **0** |
| `true` | **321** | **12** (`model` ×4, `tools` ×8) |

A local probe isolates the mechanism: a `get_stream_writer()` call inside a compiled graph
invoked within a node yields **nothing** to the parent's stream unless `subgraphs=True`. So a
correct emitter plus a request missing this flag is an empty timeline — the exact failure §5
set `can_stream: false` to avoid, arrived at from the other direction.

M2 has a second edge, found while building: the trap is not only on the HTTP flag. The first
draft of `tests/serve/test_stream_events_end_to_end.py` called
`compile_graph().stream(..., stream_mode="custom")` without `subgraphs=True`, and `check`,
`execute`, `cap` and every tool event silently vanished while `guard` through `stamp` arrived
intact. The test did not fail loudly; it asserted over a stream that was half a turn. Written down
because it caught the person who had just written this section: **any** consumer of this stream,
HTTP or in-process, has to opt into subgraphs, and forgetting it looks like the emitter being
broken rather than the reader being wrong.

**M3 — token streaming needs no model change.** The first hypothesis for zero
`messages/partial` was that `init_chat_model` needs `streaming=True`, since
`BaseChatModel._should_stream` requires either that field or an attached streaming handler.
Wrong: with M1 fixed, 321 token deltas arrived from an unmodified model. No provider config
changes.

**M4 — the `blockbuster` reason in `/capabilities` is retired, and the `.env` fix for it never
worked.** `routes.py` recorded a second reason to keep streaming off: a synchronous engine
would trip `blockbuster` inside the server's worker. Measured: `blockbuster` is armed only in
the in-mem run queue (`langgraph_runtime_inmem/queue.py:110`), LangGraph runs sync nodes in an
executor thread where it does not fire, and a full streamed run against live Postgres
completed. Separately, `.env` sets `LANGGRAPH_ALLOW_BLOCKING=true` and **the CLI ignores it**:
`langgraph_api/cli.py:283` patches the variable from the `--allow-blocking` flag and the loop
below skips any `.env` key already patched ("Don't overwrite"). The variable has never had an
effect. It is kept as documentation of the escape hatch, with a comment saying so.

**M5 — `updates` mode already exposes every rail, for free.** A streamed run emits one
`updates` event per node keyed by node name: `accept, guard, rewrite, negative_gate, fanout,
facet_×5, route, resolve, connect, assemble, agent_core, stamp`. What it does *not* expose is
anything inside `agent_core` beyond `model` / `tools` — and `tools` does not say *which* tool,
whether governance passed, or what it cost. **That is where the whole latency and the whole
governance story live**, so custom events are still required; they are required for the agent
loop specifically, not for the rails.

## The decisions

### 1. One emitter per boundary, and the boundary set is three, not one

ADR 0007 §5 said "emit from `serve/wrap.py`, not from the nodes" so one emitter covers every
stage and cannot drift. That holds for the rails and it is why `events.py` exists. But two
boundaries genuinely cannot be reached from there, and pretending otherwise is how a step
silently never appears:

* **`serve/wrap.py`** — every rail. One emitter, `Stage`-named, status observed from the
  returned update.
* **`serve/tools.py`** — the tools and the governance verdicts. They run inside the nested
  agent, which `wrap_node` never sees. This is a real exception to "one place", taken because
  these are the rows a user most wants and the only ones `updates` mode cannot supply (M5).
* **`serve/nodes/stamp.py`** — the `final` event. `stamp` is the one node deliberately left
  unwrapped (`graph.py`: wrapping the recorder turned "the recorder crashed" into a turn with
  no answer and no reason), so its event has to come from inside it.

### 2. The step vocabulary is `register/stages.py`, and `run_query` is therefore not a step

§5 said the UI adopts v2's vocabulary and `register/stages.py` is the authority. Taken
literally, that settles a question §5 did not notice: **`Stage` has no `run_query` member**, on
the stated grounds that "a passing query already emits the `check` + `execute` pair, and a
third record would double-count an action the ledger and every rate already agree on."

So a SQL call emits `check` and `execute`, never `run_query`. This is not a compromise — it is
the better timeline. "Checked against governance → blocked (`r_table_not_licensed`, table
layer)" followed by "Executed → 214 rows" says what the product does; "Ran query" does not. A
repair is a `check` row that did not pass, which is exactly what a repair is.

The UI's v1 names — `refuse_gate`, `cache`, `schema_route`, `finalize`, `search_corpus`,
`read_notes`, `grep_notes`, `run_query` — name concepts this engine does not have. They go.

### 3. Status is observed, never declared

`start` on entry, then the outcome the node actually produced, read from its returned update.
A status computed from configuration makes a broken run and a clean run look identical.

### 4. The stream carries what the record carries

The live stream and the durable ledger are two projections of one turn, and the UI's own
`steps.ts` claims "live == audit". So the stream obeys ADR 0006 §11's retention rule: closed
vocabulary and numbers kept, statement kept as the **executed** SQL plus its sha256, driver
error text and result rows dropped, exceptions as `type(exc).__name__` only — never
`str(exc)`. `execute` reports `sql` from `AttemptRecord.executed_sql`, the statement the ledger
hashes, not the model's raw argument; a live view showing one statement and an audit showing
another is the defect this rule exists to prevent.

The tool's `ToolMessage` still carries prose to the model, because the model needs it to
repair. The event does not, because the operator does not.

**On `execute.sql` being text, checked rather than assumed.** Review flagged it against
`Redaction.statement`, which declares that a statement is kept "as a digest plus a literal-elided
structural fingerprint, **never as text**". The flag is right about the declaration and wrong
about the consequence: `Redaction` is declared in `register/record.py` and **read by nothing** —
no sink applies it — so `generated_sql` already reaches the record as raw text, and from there to
the same client through `answer.record` and through `values`. The event adds no disclosure that
the response does not already make, and it pairs the text with the digest the ledger stores, which
is what lets a reader check that the live view and the audit agree.

That leaves a real finding this ADR is **not** the place to fix: a redaction policy with no
enforcement anywhere is the declared-but-unwired shape this repository keeps paying for. It is
recorded here so the next person does not read this section as evidence the policy works.

**Resolved 2026-08-06, and not in the direction this paragraph expected.** The policy is
deleted rather than enforced: `Redaction`, `redaction_of()`, `ledger_entry()` and `ports.Sink`
are gone, and ADR 0006 §11 now says the durable log is verbatim by design because this is a
local-first tool writing the user's own transcript to the user's own disk. So the reasoning in
this section still holds — the event discloses nothing the response does not — and it no longer
rests on a policy that was never applied. Note this paragraph was *right*, and being right in a
docstring for a month is exactly the failure mode: nothing acts on a finding recorded next to
the thing it excuses.

"Two projections of one turn" also constrains *how many* rows, not only what they carry, and that
caught a real defect. The `cap` event was emitted outside `AttemptBook.cap_recorded`'s guard, so a
cap of 1 against three calls produced **one** ledger row and **two** timeline rows — measured by
`test_the_attempt_cap_emits_one_cap_row`. `AttemptBook` already states the rule the ledger follows
— *"One row, not one per post-cap call: the cap is a terminal state, and a row per call would
inflate the attempt count with calls where nothing was attempted"* — and the stream now emits
inside the same guard. A stream that counts differently from the ledger is this section's rule
broken in the one direction that is easy to miss, because nothing about it looks like a leak.

### 5. The emitter must never change a turn's outcome

Emission is wrapped so a failure to send cannot fail a turn: a stream event that does not
arrive is not a governance event that did not happen. `get_stream_writer()` raises
`RuntimeError` outside a runnable context, which is the eval harness and the CLI, and those
callers must keep working. The cost of swallowing is that a broken emitter is invisible, so
`tests/test_stream_events.py` asserts the payload builder produces a valid event for every
stage and status instead of relying on production to notice.

### 6. `can_stream` is true, and `can_clarify` follows

`can_stream: true`, so the UI mounts `<StreamChat/>`. `can_clarify` was `can_stream and
has_live_model` and stays exactly that expression — flipping the first term is what unblocks
`ask_user`, whose server half was already built.

## The wire contract

Envelope — ADR 0007 §5 kept the UI's `GovEvent` and so does this. Validated on `typeof kind
=== "string" && typeof seq === "number"`; anything else is dropped silently.

```ts
{
  seq: number            // monotonic within a process; the client sorts, never indexes
  id: string             // stable per logical step; start and resolve share it
  kind: "rail" | "tool" | "final"
  step: string           // a register/stages.py Stage value
  status: "start" | "ok" | "blocked" | "error" | "refused" | "declined" | "hit" | "miss" | "cap"
  label?: string         // omitted; the client owns copy
  detail?: Record<string, unknown>
  serve_path?: "agent"   // on the first event of a turn only
}
```

`seq` disambiguates order **within one stream** and nothing more. It comes from a process-global
counter, so it is monotonic per process and means nothing across a restart — and a clarification
splits a turn into two streams, with a human's thinking time in between, during which
`langgraph dev` may well have reloaded. The client therefore owns cross-stream position: it assigns
a row's place from the rows it already holds on first sight, and uses `seq` only to order events
that arrived together. Trusting `seq` globally put `stamp` above `guard` after a mid-clarification
restart. The engine cannot supply a number meaningful across a restart, so nothing should rely on
one.

`id` is keyed on `turn_id` for rails and on `tool_call_id` for tools, both stable across a
resume replay. That is what makes an `ask_user` resume merge into the row it started rather
than appending a duplicate: the `tools` node re-executes on resume, so `start` is emitted
twice, and a seq-derived id would have shown the same step twice.

### Steps

| `step` | `kind` | statuses | `detail` |
| --- | --- | --- | --- |
| `accept` | rail | start, ok, error | `turn_index` |
| `guard` | rail | start, ok, blocked, error | `rule_id` when blocked, `gate` on error |
| `rewrite` | rail | start, ok, error | `rewritten` (bool) |
| `negative_gate` | rail | start, **ok**, hit, miss, error | `gate` on ok/error, `asset_id` on hit |
| `facet_schema` `facet_term` `facet_metric` `facet_entity` `facet_example` | rail | start, ok, error | `n_hits`, `failed_channels` on error |
| `route` | rail | start, ok, declined, error | `schemas`, `n_candidates`, `reason` when declined |
| `resolve` | rail | start, ok, error | `n_pulled_in`, `n_licensed` |
| `connect` | rail | start, ok, declined, error | `n_crossings`, `n_licensed`, `reason` when declined |
| `assemble` | rail | start, ok, error | `n_chars` |
| `agent_core` | rail | start, ok, error | `n_attempts` |
| `read_body` | tool | start, ok, **blocked**, error | `n_asset_ids`, `error_type` |
| `inspect_schema` | tool | start, ok, **blocked**, error | `table_id`, `error_type` |
| `sample_rows` | tool | start, ok, **blocked**, error | `column_id`, `limit`, `error_type` |
| `check` | tool | start, ok, blocked, error | `attempt`, `layer`, `reason_code` |
| `execute` | tool | ok, error | `sql`, `sql_sha256`, `row_count`, `truncated`, `n_columns` |
| `cap` | tool | cap | `cap` |
| `ask_user` | tool | start, ok, declined | `clarification_id` |
| `reflect` | rail | ok, error | `verdict` (`answered` / `wrong` / `unsure`, or why it is unmeasured). **One row, not a start/resolve pair, and only on turns where the observer actually judged something** — it ships disabled (`reflect_enabled`), and a disabled observer that still put a start row on every turn would have changed the timeline of every arm measured so far. |
| `narrate` | rail | start, ok, error | `source` (`narrated` / `none` / `skipped`), `n_chars` |
| `refuse` | rail | start, refused, error | `terminal_reason` |
| `decline` | rail | start, declined, error | `terminal_reason` |
| `stamp` | final | ok, refused, declined, error, cap | `outcome`, `failed_stage` |

`execute` has no `start`: it is emitted from the completed `AttemptRecord`, and a `start` for
it would be a status derived from intent rather than observation (§3). `cap` is terminal by
construction and has no `start` for the same reason.

Three rows in that table are easy to read as mistakes and are not:

**`negative_gate` resolves `ok` on every turn today, not `miss`.** The gate ships *disabled* —
`negative_tau` is UNSET until a negative corpus exists — and reporting a disabled gate as `miss`
would claim it looked and found nothing. It never looked. `ok` with `detail.gate = "disabled"`
says so, and `miss` is reserved for a gate that ran. This matters to the client because `ok` is
currently the *only* status this step produces, so it is the branch every user sees.

**`cap` is a `tool`, even though `Stage.cap` is in `TERMINAL_STAGES` beside `refuse` and
`decline`.** Two facts at two levels, kept apart on purpose: the `cap` *row* is "this tool call
was refused a slot", emitted at a tool-call boundary and keyed on its `tool_call_id`; the *turn*
being cap-terminated appears on `stamp` with `status: "cap"`. A capped turn produces both and
they are not duplicates.

**`stamp` can be `declined`, which `Outcome` cannot.** `Outcome` has no `declined` member — a
decline classifies as `refused`, which is right for measurement and wrong for a timeline, where
"no schema matched" and "the guard blocked this" are not the same event to a person reading it.
So `stamp`'s status reads `path_kind` first for that one distinction and `Outcome` for the rest.

### What adversarial review changed in this table

Five independent lenses were run over the built code against this ADR, each finding verified by
a second agent trying to refute it. Seven defects survived, and the table above is the corrected
one. They are recorded because six of the seven are the *same shape* — a status or a key that
described intent rather than observation, which is the failure §3 exists to prevent, appearing in
places §3 did not think to look:

1. **A bounds refusal read as `ok`.** The three read-only tools do not raise when bounds say no;
   they return `OUT_OF_SCOPE_MESSAGE`. Status derived from "did it throw" therefore reported a
   refused read as a successful one — and since a bounds refusal writes no ledger attempt and no
   `tool_delivered` entry, the event was the *only* record of it anywhere. Now `blocked`, told
   from a wiring failure by the shared constant rather than by the `delivered` flag, because
   `sample_rows` also returns `delivered=False` for "no connector configured" and presenting a
   wiring failure as a governance refusal is the inverse defect.
2. **`accept` silenced on every turn after a bad one.** `path_kind` is checkpointed and `accept`
   is the node that clears it, so on turn N+1 of a thread whose turn N declined, `accept` read
   its predecessor's terminal value on entry and the skip-check silenced it — losing the first
   row *and* the turn's only `serve_path` tag. Declines are not rare.
3. **`error_failed_open` reported as a clean pass**, on both gates. It means the gate ran, a rule
   threw, and the question went through anyway; the record counts it as a security event.
4. **A facet with no channel wired up reported `0 hits`.** This is `_channels_for`'s own defect
   one layer out — its predecessor reported the configuration instead of the observation. "The
   corpus has nothing to say" and "we never looked" are not the same sentence.
5. **A failed rewrite reported `rewritten: true`**, because the flag was `outcome != "unchanged"`.
6. **A decline's reason reached nobody.** The engine's channel is `terminal_reason`; this table
   said `reason`; the client read `reason`. So the most important row on a failed turn rendered
   with no explanation. Both keys are now emitted — same string, each name correct in its own
   place — and a declining node also keeps the counts it did compute.
7. **`n_assets` was declared here and never emitted.** Removed from the contract rather than
   produced: `assemble` returns only `delivery`, so emitting it would mean reading `licensed` off
   the state, making one reader consult something other than the update for one cosmetic number.

The seventh is the one worth generalising. When the contract and the code disagree, the fix is
whichever of the two is *observable* — and here that meant deleting a field, not adding one.

**Two of the seven were then confirmed by a live run rather than by a test**, and one of them was
worse than the review made it sound:

* A turn on the served corpus hit the attempt cap naturally — three `check`/`blocked` pairs at
  `attempt` 1, 2, 3 (`PARSE`, `r_ambiguous_fold`), then **one** `cap` row, then `stamp`/`cap`.
  That is defect 1 above in the wild: before the fix it would have been three cap rows against
  the ledger's one.
* Every facet on that turn resolved `error` with `failed_channels`, because this deployment
  configures no embedder, so the declared `semantic` channel is never consulted — and `extraction`
  likewise. `facet_metric` reported `n_hits: 17` **and** two failed channels. Under the old
  reader that turn rendered as five clean facets. So the whole deployment has been serving
  degraded retrieval while the timeline called it healthy, which is precisely what
  `facet_degraded` exists to make sayable.

The second has a consequence for the client rather than the engine: a status that fires on 100% of
turns is one users learn to ignore, and then the one that matters is ignored with it. The engine
keeps `error` because it agrees with the record's `facet_degraded`; the client names the cause
(`failed_channels` is a closed vocabulary) and aggregates the five facets into one row, so the
degradation is stated once rather than five times.

### Stage members this stream deliberately does not emit

`Stage` declares more than the stream produces, and `stages.py` says why declared-but-unemitted
members are kept: a name that appears only once somebody instruments it is better than a second
name invented at that moment. `read_body`, `inspect_schema`, `sample_rows`, `ask_user`, `check`,
`execute` and `cap` are now emitted. `graded_delivery`, `repair`, `table_select` and
`sql_generate` are **not** — the last two are attributed offline by the analyser, which has no
live stream to write to, and the first two are not built. A client must therefore treat an
unrecognised `step` as renderable-but-unlabelled rather than as an error.

### The request the client must send

```jsonc
{
  "assistant_id": "serve",
  "input": {"messages": [{"type": "human", "content": "…"}]},
  "stream_mode": ["values", "messages", "custom"],
  "stream_subgraphs": true          // M1/M2 — without this the timeline is empty
}
```

## Consequences

- `docs/openapi.json` remains the spec for route shapes; this ADR is the spec for the event.
- The frontend's `lib/steps.ts` is rewritten against this table. Its `GovEvent` envelope and its
  status union are unchanged — the envelope was already right, and every status this engine emits
  was already in it, which is the strongest evidence ADR 0007 §5 got the shape right even where it
  got the transport wrong. `reduceSteps` changed twice: a row's label is recomputed on each event
  rather than pinned at `start` (a blocked `check` was rendering "Checking against governance"
  forever), and a row's position now comes from arrival rather than from `seq`.
- `buildStepsFromLedger`'s `step === "finalize"` special case becomes `step === "stamp"`, but the
  function **cannot currently produce a row at all**: the v2 record carries `execution =
  {terminal, attempts, guardrail_errors}` and `AttemptRecord` has no stage name, so there is no
  array of steps to build from. So "live == audit", which the frontend module claims, is **live
  only** — a turn whose live trace was missed (a reload, another tab) has no step trace anywhere.
  Recording the stage events that are now streamed is the fix and it is not in this change.
- **The audit log is written from the graph, not from the route, and that was a regression this
  change caused and caught.** `/audit/turns` was populated only by `POST /chat`'s `_logged`; once
  the UI streams, that route serves almost nothing, so the audit page listed stale REST turns and
  none of the real ones — looking exactly like a page with nothing to show. Measured: three
  streamed turns, zero rows. `build_graph` gained a `record` seam after `stamp`, symmetric to
  `accept` before `guard`, and `api/graph_app` injects it; it cannot live in `stamp` because
  `tools/check_imports.py` orders `serve` before `api`. It swallows its own failures — a turn that
  answered is not a turn that failed, and the client already has the answer by then. Verified
  live: the audit count moved 11 → 12 on one streamed turn.
- The eval harness and `python -m governed_bi.serve` are unaffected: no writer is available
  outside a server run and emission is a no-op there (§5).
- Local serving requires no flags. `--allow-blocking` is not needed (M4) and
  `LANGGRAPH_ALLOW_BLOCKING` in `.env` never worked.
- **`POST /chat` becomes a degradation path, and it does not share a memory with the streamed
  one.** `routes.py` compiles its own `InMemorySaver`; `graph_app.make_graph` compiles with none
  so the server can supply its own, which is what makes `/threads` work. So one `session_id`
  names two unrelated checkpoints: a mid-conversation fallback serves its turn correctly and in
  isolation, with the conversation before it gone, and a clarification paused on the streamed
  thread is not answerable over REST. **Left unfixed deliberately.** The fix is one graph and one
  saver — this route becoming a client of the runtime, or the runtime's saver becoming reachable
  from it — and both are larger than a fallback deserves, and neither should be improvised in the
  change that turns streaming on. The route says so in its own docstring; the UI must not claim
  otherwise.
- The step vocabulary is now shared by three artefacts — this ADR's table, `register/stages.py`,
  and the frontend's `lib/steps.ts`. `tests/serve/test_stream_events.py` asserts the first two
  against each other, deliberately from a hand-written list rather than a derived one: a list
  derived from the code cannot catch the code being wrong.
