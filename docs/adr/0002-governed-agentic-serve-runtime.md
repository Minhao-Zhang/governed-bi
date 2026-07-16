# 0002: Serve runtime as a governed agentic core

_[English](0002-governed-agentic-serve-runtime.md) · 简体中文 (pending)_

- **Status:** Accepted / Implemented. Grilled & refined in design review
  2026-07-13; cutover landed on `main` 2026-07-14 (commit `d2fdd6a`).
- **Deciders:** project owner + design session
- **Related:** [0001](0001-langgraph-server-chat-runtime.md),
  [pipeline-design.md](../pipeline-design.md) (§8 invariants; the serve §§5–7 it once had are removed),
  [design-decisions.md](../design-decisions.md) (D2, D5, D11, D15)
- **Supersedes:** the pipeline-design §8 invariant *"Serve stays a deterministic
  DAG; LLM appears only as bounded node operations, never as an autonomous loop"*
  and the §5 framing *"LLM = node classifier, never ReAct."*
- **Verified stack:** `langchain 1.3.12`, `langgraph 1.2.8`, `deepagents 0.6.12`
  — `create_agent` + `AgentMiddleware` (`wrap_tool_call`/`wrap_model_call`) and
  `FakeListChatModel` all import in the pinned environment.
- **Mechanism verified by spike (2026-07-13).** An end-to-end spike proved the
  load-bearing mechanism on the installed stack: `wrap_tool_call` reads
  `request.state` and writes custom state channels via `Command(update=...)`; a
  governed tool grows a per-turn `licensed` channel that a later `run_query`'s
  guardrail reads (Inv #4); and every `run_query` — pass *or* deny — writes a
  ledger entry from the same interception point (Inv #10). **Constraint found:**
  tool calls must be **sequential** (custom-state updates commit between calls);
  parallel tool calls in one model turn would let `run_query` race ahead of
  `inspect_schema`'s licensing. See the build guide.

## Context

The serve runtime under-uses LangGraph and its reasoning is deterministic-but-blind.

- **The deployed graph is one node.** `langgraph.json` → `graph_app.py:make_graph`
  → `build_chat_graph` is `START → answer → END`, where `answer` calls the
  250-line monolith `flow.py::answer_question`. LangGraph is used only for thread
  persistence + custom-event streaming — **no** node-level orchestration,
  per-node observability, per-node retry, or human-in-the-loop.
- **A real DAG exists but is unused and stale.** `analyst/graph.py::build_serve_graph`
  is a 9-node `StateGraph`, but nothing in the serve path imports it, and it has
  drifted from `flow.py` (no `graded_delivery`; `narrator` behind). We maintain
  two implementations and deploy the dumber packaging of the worse one.
- **The reasoning is deterministic-but-dumb.** Intent routing is keyword match;
  schema selection is retrieval score only; **SQL generation is blind
  single-shot** — the model never inspects real table structure, the direct cause
  of the unquoted-`RA` execution failure ("it does not even see the table
  structure"); repair is a hand-rolled `while attempts < 3` loop.
- **The standing invariant forbade the fix.** pipeline-design §8 declares serve
  must never run an autonomous loop — exactly what keeps the generator from
  *looking before it leaps*.

Decision taken this session: make the runtime genuinely intelligent (a **full
agentic core**), and reverse the "never an autonomous loop" invariant — **but
only if governance is preserved by construction**, not by convention.

## Decision

Adopt a **governed agentic core**: an outer **deterministic StateGraph** (thin
governance rails) wrapping an inner **`create_agent` reasoning loop** whose
governance is enforced by **`AgentMiddleware`** and whose every data touch is a
**read-only, guardrailed tool**.

The organizing principle:

> **Governance is a mandatory interception layer, not the agent's discretion.**
> The agent reasons freely, but every tool call passes through middleware that
> enforces the guardrail *and* records the audit, and the answer is stamped by
> deterministic code the agent cannot influence. Autonomy is granted for *how to
> find the answer* — never for *what may execute*, *what is trusted*, or *what
> goes unrecorded*.

### The invariant we reverse, and why it is now safe

The old invariant conflated **autonomy** with **ungoverned**. They are separable.
An agent that (a) can act *only* through governed tools and (b) whose output is
stamped by deterministic code outside its control is autonomous in **reasoning**
but not in **authority**. We replace

> ~~Serve stays a deterministic DAG; LLM only as bounded node ops, never an
> autonomous loop.~~

with

> **Serve's *authority* is deterministic; its *reasoning* may be agentic.**

### Decisions locked in the 2026-07-13 design review

| # | Question | Decision |
|---|---|---|
| Q1 | How is the core built in LangGraph? | **`create_agent` + `AgentMiddleware`**, wrapped by a thin outer `StateGraph`. Governance = `wrap_tool_call` (guardrail + audit) and `wrap_model_call` (identity tool-scoping) — *not* hand-wired nodes and *not* an opaque `create_react_agent`. |
| Q2 | Does exploration expand execution authority? | **Governance-bounded dynamic licensing.** Exploration tools honor `governance.excluded` (excluded assets never surface); a table surfaced via a governed tool is added to a per-turn `licensed` set that `run_query`'s guardrail reads as L4 `allowed_tables`; L3 still guards every column. Accepts the policy shift: the L4 floor moves from *"retrieval recall + FK topology"* to *"curator `excluded` flags + L3 per-column."* |
| Q3 | How durable must the audit be? | **(a) on-`Answer` provenance now**; design a durable-sink **(c)** seam fed from the same choke point; migrate to durable **(b)/(c)** later. |
| Q4 | Keep two generation paths? | **No — one agentic architecture. A key is required.** `TemplateSqlGenerator` is removed as a serve path; CI/offline determinism moves to a `FakeListChatModel` agent harness. |
| Q5 | What data may reach the LLM? | **Public data — send everything, no egress bound now.** Data-privacy/egress governance is a separate future branch; keep the tool boundary shaped so an egress knob can slot in. |
| Q6 | Agent bounds? | `recursion_limit ≈ 15` super-steps; **`run_query` attempt cap = 3** enforced in `wrap_tool_call`; exhaustion → §6 graded-delivery / refuse; one model tier (`settings.models.llm_model`). |

### Architecture: agent on rails

**Outer StateGraph (deterministic — the rails):**

```
START → ingest → refuse_gate ──(neg match)────────────────► REFUSE   (HARD)
                     │
                     ▼
              prepare → cache ──(hit, re-guardrailed)──────► narrate ─► END
                     │
                     ▼
               ┌───────────────────────────────────────┐
               │      agent_core = create_agent(...)     │  ← the intelligence
               │  governed by AgentMiddleware:           │
               │   • wrap_tool_call  → normalize+guardrail+audit each call
               │   • wrap_model_call → identity tool-scoping
               └───────────────────────────────────────┘
                     │ (sql, rows)          │ budget exhausted / decline
                     ▼                       ▼
                 finalize            graded_delivery | refuse  (deterministic, §6)
              (deterministic              │
               two-axis stamp             ▼
               + cache write)            END
                     │
                     ▼
                 narrate  (LLM re-phrases the delivered answer; no-op for
                     │      refusals / no narrator; a narrator failure keeps
                     │      the deterministic finalize text)
                     ▼
                    END
```

**Governed tools (read-only ONLY):**

| Tool | What it does | Governance |
|---|---|---|
| `search_corpus(query)` | retrieve tables / terms / joins / metrics / few-shots | read-only; honors `excluded`; each hit **expands the per-turn `licensed` set** |
| `inspect_schema(table_id)` | columns, types, sample values for a licensed table | read-only; honors `excluded` — **fixes "model never sees table structure"** |
| `sample_rows(table_id, n)` | row preview | read-only, runs **as identity** (RLS) |
| `run_query(sql)` | **the only path to data** | `wrap_tool_call`: normalize (`sqlglot identify=True`) → `check()` L1–L5 over the current `licensed` set → read-only connector; failure returns as a `ToolMessage`; attempt cap = 3; L2 hard-stops |

The agent never calls `gateway.execute` directly and never sets its own
reliability stamp. It reasons; the middleware and the rails govern.

### Governance invariants preserved by construction (the safety spine)

1. **Refuse-gate runs before the agent** (D5) — negative-example matches never
   reach it.
2. **Every data tool is read-only and scoped** — L3 column allowlist / L4
   licensing enforced in `wrap_tool_call`; `excluded` assets never surface.
3. **`run_query` is normalized, guardrailed, and capped in middleware** — the
   agent cannot execute ungoverned SQL; an L2 policy block is a hard stop, never
   coached (mirrors `_NON_REPAIRABLE_LAYERS`).
4. **Licensing derives from governed exploration, not agent claims** —
   `allowed_tables` = tables surfaced *through governed tools* this turn,
   FK-expanded. Recall becomes agentic (fixes RA under-retrieval) without letting
   a rogue agent self-authorize an `excluded` table. *Crossing outside the
   deterministic retrieval+FK base lowers `semantic_assurance`* (recommended
   default) so "the agent wandered" is visible in the stamp.
5. **The reliability stamp is deterministic** — `safety_clearance` /
   `semantic_assurance` are computed by `finalize` from what actually happened,
   **never self-reported**. The agent cannot claim `grounded`.
6. **`safety_clearance` stays binary-hard** — only `semantic_assurance` is graded
   (§6 deliver-and-grade unchanged).
7. **Bounded** — `recursion_limit ≈ 15` + `run_query` attempt cap = 3; exhaustion
   → graded delivery or refuse.
8. **Leakage boundary unchanged** — gold SQL/answers never reach serve.
9. **Production serves a pinned, reviewed corpus revision** — unchanged (§1).
10. **Enforcement and audit share the interception point** — the `wrap_tool_call`
    middleware that guardrails also writes the governance record. Each turn
    accumulates an **append-only governance ledger** (a state channel with an
    `operator.add` reducer): one record per governed action (refuse-gate result;
    tools offered; each exploration's surfaced/`excluded`-filtered assets and
    licensing deltas; each `run_query`'s normalized SQL + per-layer L1–L5 verdict
    + `allowed_tables` + result meta; the stamp derivation). You can never execute
    (or refuse) *without* a record — "governance tracking all the way down" by
    construction. Lives on `Answer` provenance now (Q3-a); a durable sink is a
    seam for later.

### One architecture, key required (Q4)

There is **one serve architecture** — the agentic core. Rationale: the rework
exists to kill the two-implementations drift (`flow.py` monolith vs. stale
`graph.py`); keeping a parallel deterministic path just relitigates that.

- **Governance cannot drift** because it is a single shared module
  (`check` / `column_allowlist` / `_licensed_table_ids` / refuse-gate /
  `_finalize_success`) called by the middleware and any node — not two paths that
  promise to agree.
- **CI/offline determinism** comes from a **`FakeListChatModel` agent harness**
  (already the pattern at `test_curator_deep_agent.py:111`) — more representative
  than the deleted template engine, since it exercises the real agent path.
- **Equivalence tests change contract**: from *"same `Answer`"* (impossible
  against a nondeterministic agent) to **"same governance invariants"** — both
  paths refuse the same negatives, block the same L2 SQL, stamp `safety_clearance`
  identically.
- **`TemplateSqlGenerator` is removed as a serve path**; `stack.py` fails loudly
  when no key is present (no silent offline downgrade); `flow.py`'s
  `or TemplateSqlGenerator` fallback is removed; narrator/embedder become
  always-present. The `eval/dataset.py` template-subset helper may remain a test
  util.

### What LangGraph / LangChain primitives replace

| Today (hand-rolled) | Governed agentic core |
|---|---|
| `while attempts < 3` repair loop (`flow.py:640`) | the agent's tool-reflection loop; `run_query` cap in `wrap_tool_call` |
| `_emit`/`on_event` callback shim (`flow.py:247`) | native `stream_mode` + per-tool LangSmith traces |
| single-shot `SqlGenerator` protocol | `create_agent` with governed tools |
| ad-hoc `try/except` per stage | `RetryPolicy` per node + LLM-recoverable tool errors as `ToolMessage` |
| governance scattered across `flow.py` | `AgentMiddleware` (`wrap_tool_call` guardrail+audit, `wrap_model_call` scoping) |
| no clarification (the model guesses) | `interrupt()` + checkpointer (deferred with durable persistence) |
| two divergent implementations + template path | one architecture; deterministic in CI via a fake model |

## Consequences

**Positive**
- Fixes the root causes: the model inspects structure before emitting (no more
  blind `RA`); agentic recall fixes under-retrieval; failure-type-aware
  self-correction replaces a text-diff retry.
- Full per-node **and per-tool** observability in Studio/LangSmith.
- Governance ledger (#10) makes "governed, auditable" literal for the agent path.
- One implementation; deletes the stale duplicate DAG *and* the template path.

**Negative / costs**
- **Cost + latency:** multiple LLM calls per question vs. 1–3. Mitigate with the
  cache, the `recursion_limit`, and (later) a cheaper tool-selection model.
- **A key is now mandatory** — no offline/no-LLM serve mode.
- **Nondeterministic serve** — CI determinism relies on the fake-model harness.
- **Larger governance surface** — defended by invariants 1–10 (interception +
  deterministic stamp) rather than by forbidding autonomy. The L4 floor now leans
  on curator `excluded` flags (Q2).
- **Heavier deployment** *when* durable audit/HITL land — Postgres checkpointer /
  audit sink (inherits 0001's deployment note); deferred for now.

## Alternatives considered

- **Keep the 1-node wrapper (status quo):** rejected — no observability/retry/HITL
  and blind generation persists.
- **Explicit hand-wired StateGraph tool loop (no `create_agent`/middleware):**
  considered as the Q1 default; rejected once `AgentMiddleware` was verified to
  enforce the guardrail at the tool boundary — middleware is framework-enforced,
  so decomposing the loop into nodes is unnecessary bespoke wiring.
- **Bounded tool-loop *generation only*:** the safer middle. Rejected — recall and
  repair are where the failures live. **Retained as the fallback** if cost/latency
  prove unacceptable.
- **Keep the deterministic template path in parallel (Q4):** rejected — it is the
  same drift trap; its only non-CI role (no-key demo) can't answer the target
  questions.

## Migration (phased; each phase independently shippable)

- **Phase 0 — governance core + CI harness (prep, no behavior change).**
  Single-source the shared governance module; stand up the `FakeListChatModel`
  agent test harness; add the governance-invariant equivalence tests.
- **Phase 1 — outer rails + `agent_core` behind a flag.** Thin outer StateGraph
  (`ingest`/`refuse_gate`/`prepare`/`cache` → `agent_core` → `finalize` /
  graded-delivery); `create_agent` + middleware + governed tools; governance
  ledger on `Answer`. A/B on BIRD.
- **Phase 2 — cutover.** Make the agent core the only serve path; require a key;
  delete `TemplateSqlGenerator` (serve), the `flow.py` monolith, and the stale
  `analyst/graph.py`.
- **Phase 3 — deferred branches.** HITL (`interrupt()` + durable checkpointer),
  durable audit sink (Q3-b/c), data-privacy/egress governance (Q5).

## Open questions (deferred to their own branches)

- HITL scope + the durable checkpointer (Postgres) backing.
- Durable audit sink shape (Q3-c) and retention.
- Data-privacy / egress governance (Q5).
- `recursion_limit` / model-tier tuning against the eval; the eval / eval-ladder
  interaction for a nondeterministic agent (seeds, temperature 0).
- Migration sequencing detail now that the deterministic DAG is retired, not
  deployed.

## Amendment 1 (2026-07-13): the agent must receive the semantic layer

**Status:** Proposed — blocks P2. **Trigger:** the first live serve-path A/B
(fixed corpus, `cs_semester`, N=15) showed the agent core **regressing** vs. the
deterministic flow — curated/curated_sme flow EX 0.667 vs. agent 0.267, and
curated==curated_sme (curation added nothing through the agent).

**Root cause.** The P1 tools exposed only *names*: `search_corpus` → asset
ids+scores, `inspect_schema` → columns. They surfaced **none** of the curated
semantic layer's high-value content — **few-shot exemplars (Q→gold-SQL), join
`ON` clauses, metric expressions, term mappings, rules/caveats** — which the flow
injects via `assemble_context` → `PromptContext.render()`. So the agent did
NL→SQL over bare schema (≈ the no-layer baseline), and everything curation
enriches (joins from gold SQL, few-shots, rules — the curated→curated_sme delta)
was invisible.
This gap originated in ADR's own tool table + the build guide sketching
`search_corpus` to return ids, not content.

**Decision — seed-then-refine (not tools-only).** A deterministic **`assemble`
node runs before `agent_core`**, reusing the flow's exact front half
(`route_intent` → `retrieve`/`route_schemas` → `detect_missing_join_path` →
`_licensed_table_ids` → `assemble_context`). Then:

1. **Seed the prompt.** Inject `PromptContext.render()` (tables, joins, terms,
   metrics, few-shots, caveats, skills — identical to what the flow feeds its
   generator) as a `## Governed context` block in the agent system prompt.
2. **Seed the scope.** Pre-populate the `licensed` state channel with the base
   (retrieved + FK-neighborhood + Steiner) table ids, so common-case questions go
   straight to `run_query` with no `inspect_schema` round-trips.
3. **Tools become refinement, not discovery.** `inspect_schema` is for tables
   *beyond* the seed (still licenses them, Inv #4); `search_corpus` returns
   **content** (matching few-shots' Q/SQL, metric expressions, term mappings),
   bounded top-k; `sample_rows`/`run_query` unchanged.
4. **Prompt change.** Replace "you cannot see a table until you inspect it" with
   "the governed context below is already licensed; use `inspect_schema` only to
   reach a table not listed."

**Rationale.**
- **Parity floor:** the agent starts from the flow's exact context (0.667), so it
  cannot regress below the flow for lack of the layer.
- **Fewer super-steps:** the seed removes the search/inspect round-trips that (with
  sequential tool calls, G1) blew the step budget — complementary to the 15→40
  bump.
- **Invariant #4 preserved:** the seed is the flow's *deterministic* L4 floor (not
  agent-claimed); expansion is still only via governed `inspect_schema`; guardrail
  L4 = seed ∪ inspected. Strictly ≥ the flow's scope, never self-authorized.
- **Governance unchanged:** every `run_query` still normalizes → `check()` → ledger
  (Inv #2/#3/#10); seeding adds context, not authority.

**Alternative rejected — tools-only (agent discovers the layer via `get_joins` /
`get_few_shots` / `get_metrics`).** Purely agentic and token-lean on huge schemas,
but it repeats the A/B failure mode (the model may never fetch joins/few-shots)
and multiplies step count. The expansion tools survive as the "refine" half; they
are not the primary path.

**Honest consequence to test next.** Seeding puts the agent *at* the flow's score
by construction. Its justification then rests entirely on the loop **on top** —
`run_query`-feedback repair, `sample_rows` on real data, and scope expansion when
retrieval under-recalls. The re-run A/B must show the seeded agent **beating** the
flow on hard cases; if it only ties, the agentic thesis is weak for this workload
and P2 should not proceed on EX grounds (governance/observability benefits would
have to justify it alone).

**Open (Amendment 1):** seed retrieval budget (reuse flow defaults); prompt-window
size on large enterprise schemas (BIRD is fine); whether `search_corpus` content
retrieval shares the flow's retriever or gets its own top-k.

## Amendment 2 (2026-07-14): the governance ledger streams live

**Status:** Implemented (P1) — landed with the agent path behind `agent_serve`.

**What.** The append-only governance ledger (Inv #10) is now emitted as a **live
event stream**, not only attached to the finished `Answer`. Per turn, the agent
path pushes three event kinds through the existing `on_event` callback (consumed
by the frontend on `stream_mode="custom"`):

- `rail` — each deterministic outer step (`route`, `refuse_gate`, `cache`, `assemble`);
- `tool` — each governed action inside the agent loop (`search_corpus` /
  `inspect_schema` / `sample_rows` / `run_query`), as a `start` then an
  `ok` / `blocked` / `error` / `cap` / `miss` resolve, paired by tool-call id;
- `final` — the terminal answer's two-axis stamp.

Each event carries `{seq, kind, step, status, id?, detail, serve_path?}`; the
first event of a turn tags `serve_path:"agent"` so the UI picks the timeline
renderer over the flow's fixed stepper. Governed-tool `detail` is built **from the
ledger entry**, so the live stream and the final `governance_ledger` on
`Answer.provenance` cannot drift — the live step view *is* the ledger, streamed
early. This turns Inv #10 from a post-hoc audit dump into a per-attempt live audit
of the repair loop, which is the observability half of this ADR's thesis made into
a product surface.

**How.** `GovEventStream` (`analyst/governance.py`) is a per-turn emitter over the
raw `on_event` callback (monotonic `seq`, `serve_path` tag, best-effort). `agent_core_node`
switched `agent.invoke` → `agent.stream(stream_mode=["updates","values"])`:
model-node tool calls become `start` events, tools-node results become resolves,
and the final accumulated state is the last `values` chunk. Events are re-emitted
from the outer node through the captured callback (**not** `get_stream_writer()`
inside the agent), so emission is in one place and thread-safe past the ToolNode
worker thread. The shared finalize helpers run with `on_event=None` on this path
so only the rich contract is emitted. The deterministic flow path is unchanged —
it keeps emitting the legacy `{stage}` events.

Frontend spec + full event contract: [`docs/plans/agent-step-visualization.md`](../plans/agent-step-visualization.md).
Tests: `tests/test_agent_step_events.py`.

## Implementation note (2026-07-14): P2 cutover landed on `main`

**Status:** Implemented, commit `d2fdd6a` on `main`.

The Phase 2 cutover described above shipped: the agentic core is now the
**only** serve path. `analyst/flow.py` (`answer_question`) and the stale unused
`analyst/graph.py` DAG are both deleted; the chat graph and `/chat` always run
`answer_question_agent`; and the now-vestigial `agent_serve` flag is gone: there
is no toggle, and the agent path is unconditional. With no live model
configured, serve **fails closed at startup** (`make_graph` raises) rather than
falling back to a deterministic or template path, and `/chat` returns `503`.
Governance (guardrails L1–L5, the refuse-gate, L4 licensing, the two-axis
stamp, the ledger) is unchanged and shared. Eval's `flow_solver`/`flow_refuser`
are replaced by `agent_solver`/`agent_refuser`, adding refuse-gate coverage on
the agent path; `run_experiment` is agent-only.

## Amendment 3: narration as a node + single-handler tracing

**Status:** Implemented.

**What.** Two fixes to the agent-path rails, orthogonal to governance:

1. **Narration is a dedicated `narrate` node**, appended after `agent_core`
   (and after a cache hit): `ingest → refuse_gate → prepare → cache → assemble
   → agent_core → narrate`. Previously the LLM narrator was invoked as a side
   call buried inside the finalizers (`_finalize_success` / `_try_cache_hit` /
   `_finish_unsuccessful`, via `_answer_text`); those finalizers now emit only
   the deterministic fallback text, and `narrate_answer` (`analyst/governance.py`)
   does the LLM phrasing from the `narrate` node. **Why:** the narrator's model
   call becomes a first-class, individually-traced graph step instead of a loose
   model call not attributable to any node. Both the cache-hit path (`cache →
   narrate`) and the agent path (`agent_core → narrate`) flow through it, so
   cached and freshly-generated answers finalize identically. It is a no-op for
   refusals (refuse-gate match, missing-edge: no result grid to phrase) and
   when no narrator is configured; a narrator failure keeps the deterministic
   text. A graded-delivery (unverified) answer keeps its "⚠️ Unverified" banner.
2. **One tracing (Langfuse) handler per turn, inherited downstream.** External
   tracing (`obs.tracing_callbacks()`) is now attached once, at the outer
   `graph.invoke` in `answer_question_agent`, and inherited by everything below
   it via the LangChain run context. This fixes two bugs: the inner
   `agent.stream(...)` in `agent_core` no longer attaches its own second
   handler (a second handler logged every model call twice — same LangChain
   `run_id` → two Langfuse generations under different parents → ~2x trace
   cost/tokens); and `LangChainChatClient.complete()` (`llm/langchain_client.py`)
   now inherits the ambient run's callbacks when called inside a graph node
   (the serve-path narrator and the multi-schema schema router), instead of
   opening a detached root Langfuse trace. It only attaches its own handler
   when invoked standalone (eval baseline solver, curator). **Net effect:** the
   entire question-answering turn is one Langfuse trace, and cost/token
   aggregation is no longer double-counted. LangSmith is unaffected; it
   self-instruments from the environment.

## Amendment 4 (2026-07-14): HITL clarification shipped server-side

**Status:** Implemented (server side); durable persistence still deferred.

The Q6 row (the "no clarification (the model guesses)" line above) and Phase 3
listed HITL (`interrupt()` + checkpointer) as deferred. The **interrupt mechanism
has since landed server-side**: `analyst/tools.py::ask_user` calls `interrupt()`,
`analyst/clarify.py` carries the clarification request/response shapes,
`api/graph_app.py` handles the `ClarificationPending` resume loop, and `stack.py`
wires `can_clarify` + a `clarify_checkpointer` (covered by
`tests/test_serve_clarify.py`). What remains deferred is only the **durable**
checkpointer (Postgres) — today's checkpointer is in-memory, so a clarification
does not survive a process restart. The frontend contract is in
[hitl-clarification-contract.md](../plans/hitl-clarification-contract.md); the
frontend's build status lives in [`governed-bi-ui`](https://github.com/Minhao-Zhang/governed-bi-ui),
not here. So
"Open questions → HITL" now scopes to *durable persistence*, not the mechanism.
