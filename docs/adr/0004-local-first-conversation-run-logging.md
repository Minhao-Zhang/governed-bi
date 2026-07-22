# 0004: Local-first conversation + run logging

_[English](0004-local-first-conversation-run-logging.md) · [简体中文](0004-local-first-conversation-run-logging.zh.md)_

- **Status:** Accepted (2026-07-22). M2 metadata track + durable conversation
  checkpointer shipped; M5 gated full-content + deep-agent logging in progress.
- **Deciders:** project owner + design session
- **Related:** [0001](0001-langgraph-server-chat-runtime.md) (LangGraph
  Server threads = persistence); [0002](0002-governed-agentic-serve-runtime.md)
  (governance ledger, Inv #10; this ADR builds its deferred durable audit
  sink); [0003](0003-governed-notes-tri-modal-retrieval.md);
  [design-decisions.md](../design-decisions.md) (D8 serve-time memory; audit
  dispositions R3 + R5)
- **Refines:** **D8** (working memory is ephemeral today,
  [design-decisions.md:137-147](../design-decisions.md)) and is the concrete
  build of audit findings **R3** (vendor-independent interaction log,
  [design-decisions.md:428-455](../design-decisions.md)) and **R5** (persist
  ledger + add token/cost/duration/ts,
  [design-decisions.md:488-517](../design-decisions.md)).

## Context

- **The need (from the owner).** Keep durable **conversation history to
  reference later**, with **metadata alongside**. The storage backend is not
  prescribed ("I don't care how it's stored, as long as it is stored"); it
  must live in the **DeepAgents/LangGraph backend**, so it is
  **frontend-agnostic**: the Next.js UI, CLI, and eval all inherit it rather
  than each building their own.
- **This maps directly onto two already-deferred audit findings.** R3
  (`design-decisions.md:428-455`) called for "a dedicated, queryable,
  vendor-independent interaction log" keyed by turn + `corpus_release_hash`,
  "capture-first, interpret-later," and "feedback is a validated hypothesis,
  never a direct edit." R5 (`design-decisions.md:488-517`) found the ledger
  "records a `verdict` per data-touching tool ... but carries no timing, no
  token/cost, and no timestamp, and it is not durably persisted," and with
  tracing off there is "no vendor-independent record" of latency or cost.
- **Current gaps, cited:**
  - **Tokens are captured nowhere.** `eval/run_experiment.py:213` hard-codes
    `"usage": None` in every eval row; nothing in the repo reads
    `usage_metadata` off a model response (a repo-wide search for the string
    returns zero hits).
  - **Observability is cloud-only and a silent no-op without keys.**
    `obs.py:1-4` documents "two tracers, both opt-in by environment and both
    no-ops when unset"; LangSmith gates on env vars
    (`obs.py:45-52`, `langsmith_enabled`), and `tracing_callbacks()` returns
    `[]` when the Langfuse keys are unset (`obs.py:125-133`). There is no
    local, vendor-independent fallback.
  - **Conversation history is ephemeral, or lives in a checkpointer nobody
    attaches.** `InMemoryWorkingMemory` (`memory/store.py:36-70`, D8) is
    explicitly "ephemeral by design (lost on restart)." Separately,
    `build_chat_graph` (`api/graph_app.py:99-107`) is "Compiled **without** a
    checkpointer by default: on LangGraph Server the runtime injects
    persistence" (`graph_app.py:103-104`), and the actual `compile(...)` call
    only attaches one if the caller passes it in
    (`graph_app.py:181`). The plain REST `/chat` path never passes one. The
    only checkpointer instantiated anywhere in serve is `stack.py`'s
    per-process `InMemorySaver`, and it exists solely for the **inner** agent's
    `ask_user` HITL interrupt/resume, not for conversation durability
    (`api/stack.py:53-54` field + comment, `stack.py:172-178` construction,
    `stack.py:222` wiring into `ServeStack`).
  - **The governance ledger is in-state, not durable.** `ledger:
    Annotated[list, operator.add]` (`analyst/middleware.py:47`, on `GovState`)
    accumulates one entry per governed tool call for the turn, but it lives
    only in agent state; ADR 0002 Inv #10 explicitly left the durable sink as
    a "seam for later" (`docs/adr/0002-governed-agentic-serve-runtime.md`,
    Inv #10 / Q3).

## Decision

Make **LangGraph's native persistence the store**, capture metadata at the
interception points ADR 0002 already owns, and add **one thin, decoupled
portable append** for longevity and eval reuse.

### 1. A durable checkpointer is the conversation store

Swap the ephemeral setup (no checkpointer at all on `build_chat_graph`,
`graph_app.py:181`, and an in-memory saver scoped only to inner-agent HITL,
`stack.py:172-178`) for a durable checkpointer: `SqliteSaver` (a local
file) in dev, `PostgresSaver` in prod. This is NOT a pure config flip.
`SqliteSaver` / `PostgresSaver` ship in the separate `langgraph-checkpoint-sqlite`
/ `langgraph-checkpoint-postgres` packages, neither of which is a current
dependency (`pyproject.toml` lists only the base `langgraph` package), and
there is no checkpointer path or DSN config field on `Settings` or
`DataSourceConfig` today (`config.py`). Phase 1 has to add the dependency and
a checkpointer/DSN config field as explicit work. The dev to prod pattern
`memory/store.py:3-4` states for durable memory ("Dev backing = in-memory /
SQLite / files; prod = Postgres + pgvector (a config flip)") describes an
unimplemented aspiration for those durable memory stores, not a precedent
already wired up in this codebase. Once added, attach a durable saver where
the chat graph is compiled standalone; on LangGraph Server the runtime injects
the durable backend, so `build_chat_graph` stays checkpointer-less for the
server entry (`graph_app.py:103-104`). Conversation history is then the
persisted `messages` on `ChatState` (`graph_app.py:38-46`), referenceable
later through the standard LangGraph thread API (`get_state` /
`get_state_history` / list threads) that every client on the streaming /
`useStream` path shares. This is the ADR 0001 thread model, made durable and
frontend-agnostic for that path. **It covers the LangGraph-Server /
`useStream` path only, not the plain REST `/chat` route**, which calls
`answer_question_agent` directly and is stateless by design
(`api/app.py:414-459`, "the caller persists the transcript," with a fresh
`InMemoryWorkingMemory` per request). Making REST `/chat` durable is a separate
migration step: route it through the checkpointed graph, or add persistence
inside `answer_question_agent`.

**Naming the roles.** The checkpointer is the thread resume / UX store: it is
not a cache, it is not the audit record, and it carries no retention guarantee
across LangGraph upgrades. Once the write-consistency contract and the
full terminal-outcome coverage in Decision §3 hold, the portable append is the
authoritative historical / audit record, the artifact to query for "history
to reference later."

### 2. Metadata captured at the existing seams, persisted alongside the turn

- **Tokens.** Read `usage_metadata` (input/output/total tokens; Anthropic and
  OpenAI populate it natively via LangChain) off the model response and roll it
  up in the shared finalize-and-log helper (below, Decision §3). The capture
  seam is `GovernanceMiddleware.wrap_model_call` (`middleware.py:159`, already
  present to force sequential tool calls), which receives the model response.
  Capture `usage_metadata` from the PRE-coercion response, before it reaches
  `_coerce_single_tool_call` (`middleware.py:175`, rebuild logic at
  `middleware.py:189-216`): that helper rebuilds the `AIMessage` from only
  `content`, `tool_calls[:1]`, `id`, and `additional_kwargs`, so it drops
  `usage_metadata` on any turn where the model emitted parallel tool calls.
  Confirm the exact state-write path against the installed middleware API (an
  `after_model` hook, or reading usage off the returned AIMessages) rather than
  assuming it can mirror the `ledger`'s `wrap_tool_call` `Command(update=...)`
  write (`middleware.py:43-47`): `wrap_model_call` returns a `ModelResponse`, so
  its channel-write mechanism differs. This is the one genuinely new capture;
  today tokens are dropped (`run_experiment.py:213`). `wrap_model_call` only
  wraps the inner serve agent, so it does not see every model call in the
  system: the schema router's `select_schema` / `router_chat` (`agent.py:394`),
  the narrator (`narrate_node`, `agent.py:855`), and the curator/SME graphs
  (`curator/deep_agent.py:285`, `curator/sme.py:164`) each make model calls
  outside this seam and need their own capture points. Add a fallback that
  records a failed-call outcome when a model call raises before returning a
  response, so an error mid-call does not silently vanish from the
  token/cost record.
- **Ledger + duration + ts.** `wrap_tool_call` (`middleware.py:219`) already
  writes a ledger entry per governed action (`middleware.py:234-361`, e.g. the
  `pass` entry at `middleware.py:347-354`); add `duration_ms` and a timestamp
  to each entry (R5 item 1, `design-decisions.md:509-510`).
- **Roll-up.** `_finalize_success` (`analyst/governance.py:561`, invoked from
  `agent_core_node` in `analyst/agent.py:837`) is the SUCCESS path's merge of
  `base_provenance` with `governance_ledger` and turn facts into
  `Answer.provenance` (`governance.py:587-599`); extend that merge to also
  write model + tier, token sums plus a per-call breakdown, an estimated cost
  (from a price table), latency, outcome, the two-axis stamp
  (`safety_clearance` / `semantic_assurance`), `tables_used`, routed
  schema(s), the ledger, `corpus_release_hash` / `corpus_pin`,
  `serve_config_hash` (a hash of the governance/routing config: thresholds,
  `top_k`, RRF weights, flags, so an identical corpus with a different config
  is distinguishable), `producer` / `data_split` / `export_allow`, stable
  immutable `turn` / `run` / `thread` ids, session/identity, and `serve_path`.
  Recommended future work, not built now: note-lifecycle events, content and
  context digests, and curator/SME to note lineage; the real corpus-release
  identity stays deferred to D11. `base_provenance` is threaded from
  `ServeRailsState` (`agent.py:141`; populated at `agent.py:445`, consumed at
  `agent.py:746`), so this is additive to an existing seam, not a new one.
  `_finalize_success` is the ONLY success finalizer, called from this one
  site; every other terminal outcome returns through a different function: a
  cache hit through `_try_cache_hit`'s `assemble(...)` (`governance.py:401`,
  `governance.py:457`); a refusal, a safety block, or a graded/unverified
  delivery through `_finish_unsuccessful`'s `refusal(...)` /
  `graded_delivery(...)` (`governance.py:460`, `governance.py:497,518,542,550`);
  a `GovernanceHardStop` caught directly in `agent.py`, e.g. `agent.py:691`;
  and the `ask_user` clarify / declined paths (`agent.py:671,675`). The
  roll-up above cannot live inside `_finalize_success` alone: it has to move
  into a shared finalize-and-log helper that every one of those
  terminal-outcome functions calls, so a refusal or a safety block carries the
  same metadata as a success (see Decision §3).

### 3. One thin decoupled portable append (the only addition beyond pure-native)

The roll-up in §2 and this portable append must both run from a single shared
finalize-and-log helper, invoked by EVERY terminal-outcome function, not from
`_finalize_success` alone: success (`_finalize_success`, `governance.py:561`),
a cache hit (`_try_cache_hit`, `governance.py:401`, which returns via
`assemble(...)` at `governance.py:457`), a refusal, a safety block, or a
graded/unverified delivery (`_finish_unsuccessful`, `governance.py:460`, via
`refusal(...)` / `graded_delivery(...)` at `governance.py:497,518,542,550`), a
`GovernanceHardStop` (caught directly in `agent.py`, e.g. `agent.py:691`), and
the `ask_user` clarify / declined paths (`agent.py:671,675`). Routing the
roll-up and the append solely through `_finalize_success` would silently
exclude refusals and blocks, the turns an auditor most wants, from the log;
the shared helper is what closes that gap.

That shared helper appends **one portable record per turn** (a SQLite row or
JSONL line) OUTSIDE LangGraph's internal checkpoint schema, one record for
every terminal outcome above, not only on success. Rationale: the checkpoint
tables are LangGraph-version-coupled and shaped for resume, not for reading
back a year later or reusing in eval. This decoupled record is the durable,
portable, human-readable "reference-it-in-the-future" log, keyed by turn +
`corpus_release_hash`, exactly R3's key (`design-decisions.md:450-451`, "a
dedicated, queryable, vendor-independent interaction log ... keyed by turn +
`corpus_release_hash`"). `corpus_release_hash` itself is not implemented
today (zero occurrences of the term in `src/`) and depends on the
`CorpusRelease` decision (D11, `design-decisions.md:453`), which is still
pending; until D11 lands, a git-SHA-per-checkpoint stand-in is the interim
key, per R3's own caveat. It also closes the `run_experiment.py:213`
`"usage": None` gap, because eval reads tokens/cost from the same append
instead of hard-coding `None`.

### 4. Scope: serve conversations AND DeepAgents runs

The serve agent (`create_agent` + `GovernanceMiddleware`, assembled in
`build_agent_core`, `agent.py:163-211`) and the curator/SME deep agents
(`create_deep_agent`: `curator/deep_agent.py:285`, `curator/sme.py:164`) are
all LangGraph graphs. Attach the same durable checkpointer plus a thread/run
id to their invoke configs (today `pipeline.py:263-268` and `sme.py:219-221`
each pass only `recursion_limit` and `callbacks`), and emit the same portable
per-run record. One mechanism, three producers (serve, curator, SME).

### Owner invariants + local-first posture

- **The metadata log is write-only during a run: a historical sink, never a
  live-path source.** Nothing reads the *token/cost/ledger metadata or the
  portable append* back to influence the current turn. (The conversation
  `messages` in the checkpointer *are* read each turn to build follow-up context,
  `graph_app.py:119-120`; that is the intended "history to reference" and a
  legitimate live-path read, so the invariant scopes to the metadata + portable
  record, not the conversation store.) This preserves R3's capture-first /
  "feedback is a validated hypothesis, never a direct edit" stance
  (`design-decisions.md:437-446`) and avoids the degenerate feedback loop R2/R3
  warns against. Contrast: `SqlCache` (`analyst/cache.py:56-89`) *is* a live-path
  input by design: `_try_cache_hit` (`governance.py:401,417`) is called from the
  `cache_lookup` node (`agent.py:451-454`) and can short-circuit the current turn
  on a hit. The metadata log deliberately has no equivalent read path.
- **Metadata-only default; full content opt-in (H11 — resolved).** Three tiers:
  **Tier A** metadata always (turn id, tokens, cost, duration, outcome, ledger
  verdicts — no verbatim question / SQL / answer / rows); **Tier B** verbatim
  question / SQL / answer text under `log_full_content`; **Tier C** row previews
  under `log_row_previews` AND `log_full_content`. Default is B/C off. Retention:
  `log_full_content_ttl_days` (default 30) — `prune_full_content` nulls Tier B/C
  while keeping Tier A. Store posture: POSIX file `0600` in parent dir `0700`;
  on win32, `os.chmod` cannot restrict group/other — document the single-operator
  caveat, do not pretend. Prod gate: `environment=prod` + `log_full_content`
  without `log_full_content_ack` fails loud at `build_stack`. Cloud-tracer
  masking (`GOVERNED_BI_TRACE_MAX_CHARS` in `obs.py`) is independent.
- **Local-first, on by default.** Unlike the cloud tracers, which are "both
  no-ops when unset" (`obs.py:1-4`), the local **metadata** log is on by default
  and needs no keys. Full-content tiers stay opt-in.

## Consequences

**Positive**
- Durable, frontend-agnostic conversation history plus metadata on the
  LangGraph-Server / `useStream` path (REST `/chat` durability is a separate
  migration step, since `/chat` is stateless by design today).
- A concrete build of R3 / R5 and the ADR 0002 Inv #10 durable audit sink,
  rather than another deferred seam.
- Fixes D8 ephemerality for chat history and the governance ledger on that path.
  (HITL resume uses a separate inner `clarify_checkpointer`; H10 / M5-F7 makes
  it durable via the same factory with a distinct path, or `InMemorySaver`
  when kind is `memory`.)
- Closes the eval `usage: None` gap (`run_experiment.py:213`); token/cost/
  latency finally measurable locally, without a vendor dashboard.
- Deep-agent (curator/SME) runs get the same durable record as serve turns: one
  mechanism, not three bespoke ones.

**Negative / costs**
- The durable checkpointer needs a real database in prod (Postgres), the
  same deployment note ADR 0001 already carries.
- A full-content local log is a sensitive artifact: verbatim questions, SQL,
  and row previews. Per H11 it is opt-in (Tier B/C), TTL-pruned, POSIX-permissioned
  with an explicit win32 caveat, and prod-ack gated — not on by default.
- The portable append is a second write per turn, on top of the checkpointer
  write. Cheap, but not free, and it is a second place that can drift from the
  checkpoint state if the two writes are not kept in lockstep. The two writes
  need one concrete write-consistency contract: at-least-once delivery with an
  idempotent upsert keyed by a stable turn/run id, or a single-writer outbox
  with reconciliation, and the append must be replay-idempotent on a
  LangGraph resume. Writing both from one shared helper is a starting point,
  not itself a durability guarantee.

## Alternatives considered

- **Cloud tracers only (Langfuse/LangSmith).** Rejected: vendor-locked, a
  silent no-op without keys (`obs.py:1-4,125-133`), not a backend-owned
  frontend-agnostic record, and no local source of truth, exactly the R5 gap
  ("with tracing off ... there is no vendor-independent record",
  `design-decisions.md:501-503`).
- **A dedicated normalized analytics SQLite (the earlier two-store
  proposal).** Deferred: over-built for "keep history to reference." The
  portable append covers the same need and can be upgraded to relational
  tables later without touching the capture seams (`wrap_model_call` /
  `wrap_tool_call` / `_finalize_success`).
- **Overload the checkpointer for analytics.** Rejected: the checkpoint
  schema is version-coupled and shaped for resume, not for ad hoc reads a year
  later or eval reuse, which is exactly why the decoupled portable append exists
  instead.
- **Make the log a live-path input (read past turns to steer the run).**
  Rejected by the owner: the log is write-only; live reuse is `SqlCache`'s job
  (`analyst/cache.py`), and auto-learning from the log is the degenerate loop
  R3 guards against (`design-decisions.md:437-446`).

## Migration (phased; each phase independently shippable)

1. Add the `langgraph-checkpoint-sqlite` / `langgraph-checkpoint-postgres`
   dependency (neither ships with the base `langgraph` dependency in
   `pyproject.toml` today) and a checkpointer/DSN config field on `Settings` /
   `DataSourceConfig` (`config.py`, which has no such field today). Then
   attach a durable saver on standalone/local compilation of the chat graph so
   the LangGraph-Server / `useStream` path persists conversation history (native,
   no new schema; `build_chat_graph` stays checkpointer-less for the server
   entry, `graph_app.py:103-104`, to avoid colliding with the platform's injected
   persistence). Making the REST `/chat` route durable (route it through the
   checkpointed graph, or persist inside `answer_question_agent`,
   `api/app.py:414-459`) is a distinct follow-on step.
2. Capture tokens in `wrap_model_call` (`middleware.py:159`) into a new
   `token_usage` channel, reading `usage_metadata` off the PRE-coercion
   response before `_coerce_single_tool_call` (`middleware.py:175`, rebuild
   logic at `middleware.py:189-216`) can drop it; add separate capture points
   for the schema router (`select_schema` / `router_chat`, `agent.py:394`),
   the narrator (`narrate_node`, `agent.py:855`), and the curator/SME graphs
   (`curator/deep_agent.py:285`, `curator/sme.py:164`), all of which call
   models outside the `wrap_model_call` seam; stamp each ledger entry with
   `duration_ms` + ts (`middleware.py:219`); and add a fallback that records a
   failed-call outcome when a model call raises before returning a response.
3. Enumerate every terminal-outcome function (success, cache hit, refusal,
   safety block, graded/unverified delivery, hard stop, and clarify/declined;
   see Decision §3) and route each one through a single shared
   finalize-and-log helper, so the roll-up onto `Answer.provenance` and the
   portable append below cover every outcome, not only success.
4. Add the thin portable per-turn append as METADATA-ONLY first (turn id,
   tokens, cost, duration, outcome; no verbatim content), written from the
   shared helper, keyed by turn + `corpus_release_hash` (interim: a
   git-SHA-per-checkpoint stand-in until the `CorpusRelease` decision, D11,
   lands); wire `run_experiment.py` to read tokens/cost from it instead of
   hard-coding `"usage": None` (`run_experiment.py:213`).
5. Add FULL-CONTENT logging (verbatim questions, SQL, row previews) under H11
   tiers + TTL + POSIX perms + prod-ack (M5). Metadata-only remains the default.
6. Extend to DeepAgents: `make_durable_checkpointer` + `emit_run_record` for the
   curator and SME invokes (one mechanism, three producers); usage via
   `UsageMetadataCallbackHandler`; failed-invoke fallback record.
7. (Deferred) optional relational upgrade of the portable store for
   dashboards/metrics, per R5 items 4-5 (`design-decisions.md:513-514`:
   OpenTelemetry/Prometheus surface, fail-loud tracing). Retention/rotation for
   Tier B/C is **not** deferred — see H11 / `prune_full_content`.

## Resolved decisions (2026-07-22)

Canonical record: [D18](../design-decisions.md#d18-local-first-conversation--run-logging).

1. **[H11] Log privacy / retention.** Metadata-only (Tier A) default-on;
   Tier B under `log_full_content`; Tier C row previews under `log_row_previews`
   AND `log_full_content`; 30-day TTL on B/C via `prune_full_content`; POSIX
   `0600`/`0700` with documented win32 single-operator caveat; prod refuses
   `log_full_content` without `log_full_content_ack` (fail loud at `build_stack`).
2. **[H10] Durable `clarify_checkpointer`.** Same factory as the conversation
   saver (`make_durable_checkpointer`) with a distinct path/namespace; 
   `InMemorySaver` when kind is `memory` (offline tests / ephemeral).

## Open questions

- Portable record format: SQLite row (queryable, still trivially exportable,
  recommended) vs. JSONL (dead-simple append/grep) — both implemented; default
  SQLite.
- Cost price-table location and when to compute it (config, at finalize).
- Prod checkpointer: reuse the serving Postgres or a separate logging
  database.
