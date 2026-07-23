# Eval concurrency: a configurable `workers` knob (design)

_Status: **Proposed** (design only, no code). From the 2026-07-22 experiment audit
(companion to [`eval-audit-backlog-2026-07-22.md`](eval-audit-backlog-2026-07-22.md),
its Q2 item). Implementation is deferred until the in-flight ~2,000-question
`run_datalake` run lands; this doc is the spec the implementer follows after._

Goal: make eval throughput configurable (high on a dedicated box with no
rate limits, low against a rate-limited provider) without changing any
grade. Default (`workers=1`) must reproduce today's exact serial behavior
byte-for-byte.

## Current state: fully sequential

No async/batch/threads anywhere in the eval drivers today. The dominant cost is
one LLM-and-DB-bound agentic turn per question, run one at a time:

- Per-question serve loop: [`run_experiment.py:167-171`](../../src/governed_bi/eval/run_experiment.py#L167-L171)
  (`for item in items:` → `solver.solve(item.question)`) and
  [`run_datalake.py:286-290`](../../src/governed_bi/eval/run_datalake.py#L286-L290)
  (`for item, db in pairs:`). Each `solve` is a full agentic turn:
  [`arms.py:189`](../../src/governed_bi/eval/arms.py#L189) `graph.invoke(...)`,
  up to 40 super-steps
  ([`middleware.py:40`](../../src/governed_bi/analyst/middleware.py#L40)
  `AGENT_RECURSION_LIMIT`), plus router + narrator LLM calls: tens of seconds
  of network latency per question.
- The three arms run sequentially:
  [`run_experiment.py:522`](../../src/governed_bi/eval/run_experiment.py#L522),
  [`run_datalake.py:508`](../../src/governed_bi/eval/run_datalake.py#L508).
- The per-DB build loop runs sequentially:
  [`run_datalake.py:456`](../../src/governed_bi/eval/run_datalake.py#L456)
  (`for db in wanted:`).

At today's per-question latency, a 2,000-question × 3-arm run is wall-clock
bound almost entirely by this loop nesting, not by DB or CPU work.

## The three isolation fixes required for safe parallelism

Running the loops above concurrently is unsafe today for three independent
reasons. All three must be fixed before any worker count above 1 is trustworthy.

### 1. Per-worker DB connection (Critical)

One shared `PostgresConnector` / `Gateway` serves the whole run today:
[`run_experiment.py:299,305`](../../src/governed_bi/eval/run_experiment.py#L299)
and [`run_datalake.py:488-489`](../../src/governed_bi/eval/run_datalake.py#L488-L489).
`PostgresConnector.__init__` holds exactly one psycopg connection
([`postgres.py:70`](../../src/governed_bi/gateway/connectors/postgres.py#L70)
`self._conn = psycopg.connect(...)`), and **psycopg connections are not
thread-safe**: interleaved cursors on one connection corrupt the wire protocol.
`Gateway` adds a second piece of shared mutable state on top
([`gateway.py:49`](../../src/governed_bi/gateway/gateway.py#L49) `self._audit`,
appended on every `execute`).

The connection is hit on the hot path (governance middleware's `run_query`
tool call, [`middleware.py:388`](../../src/governed_bi/analyst/middleware.py#L388)
`self._gateway.execute(sql, self._identity)`) and by grading itself:
[`hash_grade.py:225`](../../src/governed_bi/eval/hash_grade.py#L225)
(`score_sql_hashes`) and
[`hash_grade.py:288`](../../src/governed_bi/eval/hash_grade.py#L288)
(`validate_gold_hashes_live`).

**Fix:** either one `PostgresConnector` + `Gateway` pair constructed per
worker thread, or a `psycopg_pool.ConnectionPool` sized to the worker count
and shared, with each worker checking out its own connection for the duration
of a question. Cap pool size at the Postgres server's `max_connections`
(default ~100) minus headroom for other clients. Grading (`score_sql_hashes`,
`crosscheck_execution_match`) must run against the *same worker's* gateway,
not a separately-shared one (see the return-value change in Fix 3).

Precedent already in the codebase for "short-lived, per-scope connector":
[`run_datalake.py:244-251`](../../src/governed_bi/eval/run_datalake.py#L244-L251)
(`_datalake_gold_selfcheck`) already opens and closes one `PostgresConnector`
per db for its self-check (same shape, smaller scope). Folding that self-check
onto the per-worker pool ([E5 in the audit backlog](eval-audit-backlog-2026-07-22.md))
is a nice-to-have that falls out of this fix; not required for it.

### 2. Per-worker solver/graph (High)

`agent_solver` builds the rails graph once and returns a closure over it:
[`arms.py:172`](../../src/governed_bi/eval/arms.py#L172)
(`graph = build_serve_rails(...)`). `build_serve_rails` captures mutable
per-turn state in that closure:

- `_finalize_ctx` (a `FinalizeCtx`), replaced in place every `ingest` call:
  [`agent.py:319-328`](../../src/governed_bi/analyst/agent.py#L319-L328) build,
  [`agent.py:352-360`](../../src/governed_bi/analyst/agent.py#L352-L360) mutate.
- `_turn_n`, a one-element list incremented every turn:
  [`agent.py:331`](../../src/governed_bi/analyst/agent.py#L331) build,
  [`agent.py:350`](../../src/governed_bi/analyst/agent.py#L350) mutate.
- `events._seq`, the `GovEventStream` sequence counter reset per turn:
  [`governance.py:340,347,369`](../../src/governed_bi/analyst/governance.py#L340).

Invoking one compiled graph concurrently from multiple threads interleaves
this state across questions (turn IDs, ledger sequence numbers, finalize
timing all cross-contaminate). This also rules out a naive `graph.batch()` /
`graph.abatch()` call on a single graph instance: same shared closure, same
problem, just hidden behind LangGraph's own scheduler instead of a
`ThreadPoolExecutor`.

**Fix:** build one `agent_solver(...)` (and therefore one graph) per worker,
each with its own `session_id` (already a parameter,
[`arms.py:139`](../../src/governed_bi/eval/arms.py#L139)) so worker graphs
don't collide on it either. Building N graphs instead of 1 is cheap: the
per-corpus embedding work is already hoisted to build time
([`agent.py:308-312`](../../src/governed_bi/analyst/agent.py#L308-L312)
`embed_schema_documents`, computed once per graph build), so N graph builds
cost N schema-embedding passes, not N× the per-question LLM cost.

Note this graph is also checkpointer-less in eval
([`agent.py:964`](../../src/governed_bi/analyst/agent.py#L964)
`builder.compile()`, no `checkpointer=` passed), so there is no persisted
state to worry about splitting across workers; each per-worker graph starts
clean.

### 3. Return meta, don't stash on `self` (High)

`_AgentSolver.last_solve_meta` is an instance attribute overwritten every call
([`arms.py:184`](../../src/governed_bi/eval/arms.py#L184) init,
written at the end of `solve()`), and read by the driver immediately after
the call returns:
[`run_experiment.py:205`](../../src/governed_bi/eval/run_experiment.py#L205),
[`run_datalake.py:301`](../../src/governed_bi/eval/run_datalake.py#L301).
That read-after-call pattern is inherently racy once multiple questions are
in flight against the same solver instance. Even with Fix 2 (one solver per
worker), it is fragile the moment a worker handles more than one question,
because nothing pairs the meta back to *which* question produced it except
program order.

**Fix:** change `solve()` to return `(sql, meta)` (or a small result
object) instead of mutating `self`, and thread `question_id` through the call
so the caller pairs a result back to its question explicitly rather than by
timing. This removes the shared-mutable-attribute hazard entirely, independent
of thread count.

This is also a correctness fix today, not just a concurrency
precondition. It is exactly [C5 in the audit backlog](eval-audit-backlog-2026-07-22.md#correctness-backlog-nice-to-have):
a solver crash on question N leaves `last_solve_meta` holding question
N-1's `tier` / `routed_schemas` / `schema_pick`, which then gets attributed to
question N's row. Worth doing regardless of whether concurrency ships.

## Config surface

- One integer knob: **`workers`**, default **`1`**, which reproduces today's
  serial behavior byte-for-byte (same loop order, same single connector, same single graph).
- Add a `[eval]` table to
  [`governed_bi.toml`](../../governed_bi.toml), parsed the same way the
  existing `[logging]` table is in
  [`config.py:446-452`](../../src/governed_bi/config.py#L446-L452):

  ```toml
  [eval]
  workers = 1
  # build_workers = 1   # optional override; see below
  # serve_workers = 1
  ```

- Add a `--workers INT` flag to both driver CLIs
  ([`run_experiment.py:main`](../../src/governed_bi/eval/run_experiment.py#L630),
  [`run_datalake.py:main`](../../src/governed_bi/eval/run_datalake.py#L601)),
  alongside the existing `--max-agent-steps` / `--limit` flags. CLI value
  overrides the config file; config file overrides the code default of `1`.
- **Worker-count sanity check**: the implementation opens one connection per
  worker (Fix 1's per-worker-connector option, not a shared `psycopg_pool`), so
  there is no pool object to clamp against and no `max_connections` value known
  without a live probe. `resolve_workers` therefore warns loudly above a sane
  threshold (`MAX_SANE_WORKERS = 32`) and proceeds, leaving the operator
  responsible for keeping `workers` within the server's connection budget. If a
  worker cannot open its connection, the run fails loudly at build time with the
  underlying connection error (the operator lowers `--workers` or raises
  `max_connections`); it never deadlocks or silently drops questions. A hard cap
  that probes `max_connections` is possible later hardening, deliberately not
  built now.
- **Optional split**: `build_workers` vs `serve_workers`. Build (curator LLM
  calls + DB profiling, [`run_datalake.py:456`](../../src/governed_bi/eval/run_datalake.py#L456))
  and serve (per-question agentic turns) have different cost profiles and
  different safety preconditions: build parallelism additionally needs the
  sidecar-relocation fix below; serve parallelism needs Fixes 1-3 only.
  Recommended, but not required for a first cut: a single `workers` value
  applied to both loops is a legitimate, simpler v1; split only if build and
  serve turn out to want different counts in practice.
- **Explicitly out of scope for this knob**: rate-limit/backoff and a
  max-inflight-requests control for a genuinely rate-limited provider.
  `workers` answers "how much parallelism can this box sustain"; retry/backoff
  answers "how do I not get 429'd by the provider", a separate, later
  control. Note it as a future seam here; do not build it now.

## Mechanism: `ThreadPoolExecutor`

Use a `concurrent.futures.ThreadPoolExecutor` over the per-question stream
(and, in the build loop, over the per-db stream). Reasoning:

- The eval drivers are entirely sync, blocking-IO code (psycopg calls,
  `requests`-style HTTP under the LangChain clients). Threads fit blocking-IO
  workloads natively; the GIL releases during IO waits, which is most of the
  wall-clock time here (network round-trips to the LLM and the DB).
- An async rewrite would not remove that IO-bound-sync-work problem; it would
  just offload the same sync calls into a thread pool under the hood (e.g.
  `asyncio.to_thread` around psycopg, or LangChain's own
  `ainvoke`-wraps-sync-executor path for non-async-native pieces). Doing the
  threadpool explicitly, at the eval-driver level, is simpler and matches
  where the isolation fixes above already have to live (per-worker connector,
  per-worker graph).
- `graph.batch()` / `graph.abatch()` on a single compiled graph is unsafe
  regardless of sync/async (see Fix 2); batching only becomes safe once each
  batch item has its own graph instance, at which point it is no different
  from N threads each calling `graph.invoke()` on its own graph.
- Nodes and tools in the serve rails graph are themselves synchronous
  (`build_serve_rails`, `middleware.py`), so there is no async-native
  advantage on the graph side either.

Folding the three arms into the same pool as additional independent tasks
(rather than a separate parallelization axis) keeps the worker budget single
and simple: "N questions/arms in flight at once," not "N questions times M
arms in flight."

## Results-invariance argument

Parallelizing changes wall-clock only, never a grade, because:

- Eval is single-round: each `solve()` call is one question, independent of
  every other. `agent_solver`'s own docstring states this:
  [`arms.py:147-148`](../../src/governed_bi/eval/arms.py#L147-L148)
  ("each `solve` is independent (no working memory / cache)").
- No checkpointer on the serve rails graph in eval: confirmed at
  [`agent.py:964`](../../src/governed_bi/analyst/agent.py#L964)
  (`builder.compile()` with no `checkpointer=`); the inner agent core is also
  built with `clarify_checkpointer=None` on this path
  (eval never passes one). There is no persisted cross-question state a race
  could corrupt.
- No shared cache across questions in the eval path.
- Therefore the set of (question, grade, meta) triples produced by a run is
  independent of the order questions are dispatched in and independent of how
  many run concurrently; only *when* each result becomes available changes.

LLM nondeterminism (temperature, provider-side variance) is a real and
orthogonal concern, but it already exists at `workers=1`; it is unaffected by
adding workers. A `workers=N` run and a `workers=1` run against a
deterministic model (see Testing, below) must produce identical grades; two
`workers=1` runs against a live nondeterministic model may already differ
from each other, for reasons that have nothing to do with concurrency.

## Ranked plan

1. **Parallelize per-question serve across workers** (the biggest win: this
   is the loop that dominates wall-clock). Fold the three arms in as
   additional independent tasks in the same pool rather than a second
   parallel axis. Requires Fixes 1-3 above.
2. **Parallelize the per-DB build loop**
   ([`run_datalake.py:456`](../../src/governed_bi/eval/run_datalake.py#L456)),
   but only after fixing a sidecar-clobber race:
   [`run_datalake.py:69-94`](../../src/governed_bi/eval/run_datalake.py#L69-L94)
   (`_relocate_sidecars`) moves root-level curator sidecar files
   (`run_manifest.json`, `validate_findings.jsonl`, etc.) from the shared
   corpus root into a per-db `_build/` directory *after* each db's build
   finishes. Two db builds running concurrently both write those sidecars to
   the same root-level filenames before either relocates; the second build's
   write can clobber or interleave with the first's before it is moved. Fix
   by writing sidecars directly to a per-db temp location (or a per-db
   subdirectory) during the build itself, or by taking a per-root write lock
   around the write-then-relocate sequence. Either removes the shared
   filename collision; per-db write location is the cleaner fix since it
   removes the race instead of serializing around it.

**Speedup ceiling**: near-linear in `workers`, up to the binding constraint,
the Postgres connection pool / DB CPU (the LLM endpoint is assumed unlimited
per the scenario this doc targets: a dedicated box, no rate limits), not the
model endpoint. Past that point additional workers queue on the DB pool and
stop helping.

## Testing

- **Invariance test**: drive both drivers against the offline
  `FakeListChatModel` harness (same pattern as
  [`test_langchain_client.py`](../../tests/test_langchain_client.py) /
  `governed_bi.llm.fake`) with a fixed script, run once with `workers=1` and
  once with `workers=N` (N ≥ 2; small `N` is enough because this is proving a
  property, not measuring a speedup), and assert the two runs produce
  identical per-question grades and meta (`generated_sql`, `correct`,
  `correct_strict`, `tier`, `semantic_assurance`, `refused_by`, `usage`) once
  sorted back into a stable order by `question_id`. This is the executable
  form of the results-invariance argument above; it must pass before the
  knob ships above `workers=1` as a default anywhere.
- **Worker-count guard**: a test that `resolve_workers` emits a loud warning
  above `MAX_SANE_WORKERS` and returns the requested count unchanged (the
  operator-sized contract above). There is no `psycopg_pool`, so the failure
  mode for an over-budget count is a plain connection error raised at worker
  build time, not a silent deadlock; the guard is the warning plus that
  fail-loud behavior, not an automatic clamp.

## Non-goals

- Rate-limit / backoff / max-inflight control for a rate-limited provider:
  a separate, later control (see Config surface, above).
- An async rewrite of the middleware, gateway, or connectors.
- Any change to the serve graph itself (`build_serve_rails`,
  `answer_question_agent`) beyond what Fix 2 already requires (build it once
  per worker; no new nodes, no new state).
- Touching the in-flight ~2,000-question run. This ships after it lands.
