# 0004: Local-first conversation + run logging

_[English](0004-local-first-conversation-run-logging.md) · [简体中文](0004-local-first-conversation-run-logging.zh.md)_

- **Status:** Proposed (2026-07-21). Design agreed with the project owner; no
  code yet.
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
file) in dev, `PostgresSaver` in prod. This is a config flip, mirroring the
dev→prod pattern `memory/store.py:3-4` already states for durable memory
("Dev backing = in-memory / SQLite / files; prod = Postgres + pgvector").
Attach a durable saver where the chat graph is compiled standalone; on
LangGraph Server the runtime injects the durable backend, so `build_chat_graph`
stays checkpointer-less for the server entry (`graph_app.py:103-104`).
Conversation history is then the persisted `messages` on `ChatState`
(`graph_app.py:38-46`), referenceable later through the standard LangGraph
thread API (`get_state` / `get_state_history` / list threads) that every client
on the streaming / `useStream` path shares. This is the ADR 0001 thread model,
made durable and frontend-agnostic for that path. **It covers the
LangGraph-Server / `useStream` path only, not the plain REST `/chat` route**,
which calls `answer_question_agent` directly and is stateless by design
(`api/app.py:414-459`, "the caller persists the transcript," with a fresh
`InMemoryWorkingMemory` per request). Making REST `/chat` durable is a separate
migration step: route it through the checkpointed graph, or add persistence
inside `answer_question_agent`.

### 2. Metadata captured at the existing seams, persisted alongside the turn

- **Tokens.** Read `usage_metadata` (input/output/total tokens; Anthropic and
  OpenAI populate it natively via LangChain) off the model response and roll it
  up in `_finalize_success` (below). The capture seam is
  `GovernanceMiddleware.wrap_model_call` (`middleware.py:159`, already present to
  force sequential tool calls), which receives the model response. Confirm the
  exact state-write path against the installed middleware API (an `after_model`
  hook, or reading usage off the returned AIMessages) rather than assuming it can
  mirror the `ledger`'s `wrap_tool_call` `Command(update=...)` write
  (`middleware.py:43-47`): `wrap_model_call` returns a `ModelResponse`, so its
  channel-write mechanism differs. This is the one genuinely new capture; today
  tokens are dropped (`run_experiment.py:213`).
- **Ledger + duration + ts.** `wrap_tool_call` (`middleware.py:219`) already
  writes a ledger entry per governed action (`middleware.py:234-361`, e.g. the
  `pass` entry at `middleware.py:347-354`); add `duration_ms` and a timestamp
  to each entry (R5 item 1, `design-decisions.md:509-510`).
- **Roll-up.** `_finalize_success` (`analyst/governance.py:561`, invoked from
  `agent_core_node` in `analyst/agent.py:837`) already merges `base_provenance`
  with `governance_ledger` and turn facts into `Answer.provenance`
  (`governance.py:587-599`); extend that merge to also write model + tier,
  token sums plus a per-call breakdown, an estimated cost (from a price
  table), latency, outcome, the two-axis stamp (`safety_clearance` /
  `semantic_assurance`), `tables_used`, routed schema(s), the ledger,
  `corpus_release_hash` / `corpus_pin`, session/identity, and `serve_path`.
  `base_provenance` is threaded from `ServeRailsState` (`agent.py:141`;
  populated at `agent.py:445`, consumed at `agent.py:746`), so this is additive
  to an existing seam, not a new one.

### 3. One thin decoupled portable append (the only addition beyond pure-native)

`_finalize_success` also appends **one portable record per turn** (a SQLite
row or JSONL line) OUTSIDE LangGraph's internal checkpoint schema. Rationale:
the checkpoint tables are LangGraph-version-coupled and shaped for resume, not
for reading back a year later or reusing in eval. This decoupled record is the
durable, portable, human-readable "reference-it-in-the-future" log, keyed by
turn + `corpus_release_hash`, exactly R3's key
(`design-decisions.md:450-451`, "a dedicated, queryable, vendor-independent
interaction log ... keyed by turn + `corpus_release_hash`"). It also closes
the `run_experiment.py:213` `"usage": None` gap, because eval reads
tokens/cost from the same append instead of hard-coding `None`.

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
- **Full content now; masking later.** The log stores questions, SQL, and row
  previews verbatim, because it is a historical record and is not consumed
  during a run. `obs.py`'s `GOVERNED_BI_TRACE_MAX_CHARS` masking
  (`obs.py:61-91`, applied via `_trace_mask` in `_langfuse_handler` at
  `obs.py:115`) applies to the cloud tracer path only. Masking and retention
  are a deferred future toggle, explicitly not built now.
- **Local-first, on by default.** Unlike the cloud tracers, which are "both
  no-ops when unset" (`obs.py:1-4`), the local log is on by default and needs no keys.

## Consequences

**Positive**
- Durable, frontend-agnostic conversation history plus metadata on the
  LangGraph-Server / `useStream` path (REST `/chat` durability is a separate
  migration step, since `/chat` is stateless by design today).
- A concrete build of R3 / R5 and the ADR 0002 Inv #10 durable audit sink,
  rather than another deferred seam.
- Fixes D8 ephemerality for chat history and the governance ledger on that path.
  (HITL resume uses a separate inner `clarify_checkpointer`, `stack.py:172-178`,
  and needs its own durability step, not covered by §1.)
- Closes the eval `usage: None` gap (`run_experiment.py:213`); token/cost/
  latency finally measurable locally, without a vendor dashboard.
- Deep-agent (curator/SME) runs get the same durable record as serve turns: one
  mechanism, not three bespoke ones.

**Negative / costs**
- The durable checkpointer needs a real database in prod (Postgres), the
  same deployment note ADR 0001 already carries.
- A full-content local log is a sensitive artifact: verbatim questions, SQL,
  and row previews. Masking + retention are deferred, accepted for now because
  it is an operator-side historical log, not something consumed at runtime.
- The portable append is a second write per turn, on top of the checkpointer
  write. Cheap, but not free, and it is a second place that can drift from the
  checkpoint state if the two writes are not kept in lockstep.

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

1. Attach a durable saver on standalone/local compilation of the chat graph so
   the LangGraph-Server / `useStream` path persists conversation history (native,
   no new schema; `build_chat_graph` stays checkpointer-less for the server
   entry, `graph_app.py:103-104`, to avoid colliding with the platform's injected
   persistence). Making the REST `/chat` route durable (route it through the
   checkpointed graph, or persist inside `answer_question_agent`,
   `api/app.py:414-459`) is a distinct follow-on step.
2. Capture tokens in `wrap_model_call` (`middleware.py:159`) into a new
   `token_usage` channel; stamp each ledger entry with `duration_ms` + ts
   (`middleware.py:219`); extend `_finalize_success`
   (`governance.py:561`) to roll up the per-turn metadata onto
   `Answer.provenance`.
3. Add the thin portable per-turn append, written from `_finalize_success`,
   keyed by turn + `corpus_release_hash`; wire `run_experiment.py` to read
   tokens/cost from it instead of hard-coding `"usage": None`
   (`run_experiment.py:213`).
4. Extend to DeepAgents: checkpointer + run id + portable record for the
   curator (`pipeline.py:263-268`) and SME (`sme.py:219-221`) invokes.
5. (Deferred) masking toggle + retention/rotation; optional relational upgrade
   of the portable store for dashboards/metrics, per R5 items 4-5
   (`design-decisions.md:513-514`: OpenTelemetry/Prometheus surface,
   fail-loud tracing).

## Open questions

- Portable record format: SQLite row (queryable, still trivially exportable,
  recommended) vs. JSONL (dead-simple append/grep).
- Cost price-table location and when to compute it (config, at
  `_finalize_success`).
- Prod checkpointer: reuse the serving Postgres or a separate logging
  database.
