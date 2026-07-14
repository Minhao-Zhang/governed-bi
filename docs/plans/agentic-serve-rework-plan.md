# Engineering impact map: governed agentic serve runtime

_Implements [ADR 0002](../adr/0002-governed-agentic-serve-runtime.md). This is the
"what gets touched" inventory, grouped by package and mapped to the ADR's phases
(P0–P3). It is a checklist, not a spec — the design rationale lives in the ADR._

**Legend:** 🆕 new · ✏️ modify · 🗑️ delete · ♻️ reuse-as-is (no change, listed so
reviewers know it is load-bearing)

**Standout findings that shrink the work**
- `server/middleware.py` **already exists as a stub** (`before_model`,
  `wrap_tool_call` → `NotImplementedError`) whose docstring already describes this
  exact design ("guardrails + refuse-gate around a tool call; veto is final";
  "shrink the hooks as models improve, engine vs fuel"). The seam is intended,
  not new.
- `retrieval/schema_router.py::select_schema` is **already built** (unwired) — it
  becomes a tool / pre-agent node, not new code.
- The governance primitives (`gateway/guardrails.py`, `column_allowlist`,
  `_licensed_table_ids`, the two-axis stamp) are **reused unchanged** — that is
  the point: governance is single-sourced, only reasoning changes.

---

## 1. `server/` — the core rework

| File | Fate | What changes |
|---|---|---|
| `middleware.py` | ✏️ implement | Fill the stub as a LangChain `AgentMiddleware`. `wrap_tool_call`: for `run_query` → normalize (`sqlglot identify=True`) → `check()` L1–L5 over the per-turn `licensed` set → cap at 3 → append ledger record (Inv #10); L2 = hard stop. `wrap_model_call`/`before_model`: identity tool-scoping + context/working-memory injection. Signature moves from ad-hoc `(state, call)` to the `AgentMiddleware` API. |
| `agent.py` | 🆕 create | `build_agent_core(...)` = `create_agent(model, tools, middleware=[...], system_prompt=...)`; plus the **thin outer `StateGraph`** (`ingest → refuse_gate → prepare → cache → agent_core → finalize / graded / refuse`). Replaces `graph.py`. |
| `tools.py` | 🆕 create | The four governed tools (`search_corpus`, `inspect_schema`, `sample_rows`, `run_query`) closing over corpus/gateway/identity/graph + the mutable per-turn `licensed` set. Reuse `retrieve`, `filter_corpus_for_retrieval`, `select_schema`, `TableView`/`ColumnView`, `gateway.execute`. |
| `ledger.py` | 🆕 create (or fold into `answer.py`) | Governance-ledger record type + `operator.add` reducer channel; serialized onto `Answer` provenance (Q3-a). |
| `flow.py` | 🗑️ P2 / ✏️ P0 | P0: extract the reusable helpers (`_finalize_success`, `_licensed_table_ids`, `_finish_unsuccessful`, `_match_negative_example`, `_try_cache_hit`, `_answer_text`, `_result_table`, escalation blobs, `_HARD_REFUSE_LAYERS`) into a shared `governance`/`finalize` module. P2: delete the `answer_question` monolith + the `or TemplateSqlGenerator` fallback. |
| `graph.py` | 🗑️ P2 | Delete the stale unused DAG (`build_serve_graph`, `answer_question_graph`). Superseded by `agent.py`. |
| `sqlgen.py` | 🗑️/✏️ P2 | Delete `TemplateSqlGenerator` (serve path gone, Q4). `LlmSqlGenerator` prompt logic folds into the agent system prompt / a generate tool. Retire or repurpose the `SqlGenerator` protocol + `RepairFeedback` (replaced by `ToolMessage` reflection). |
| `context.py` | ♻️/✏️ | `assemble_context` / `PromptContext.render` reused to build the agent system prompt and `inspect_schema` output. Likely light refactor, no contract change. |
| `answer.py` | ✏️ additive | Reuse `Answer`, two-axis stamp, `graded_delivery`, `refusal`, `assemble`, `RESULT_PREVIEW_ROWS`. Add the governance-ledger to provenance; `finalize` node calls `_finalize_success`. Add "wandered outside deterministic base → lower `semantic_assurance`" (Inv #4). |
| `routing.py` | ♻️ | `route_intent`, `bind_terms` reused in the `ingest` node. |
| `cache.py` | ♻️ | `SqlCache` + `_try_cache_hit` reused as the `cache` node. |
| `narrate.py` | ✏️ minor | Always-present now (key required); optionally its own node for tracing. |
| `__init__.py` | ✏️ | Drop `TemplateSqlGenerator` export; add the agent-core/serve-graph builders. |

---

## 2. `api/` — wiring + deployment

| File | Fate | What changes |
|---|---|---|
| `stack.py` | ✏️ | `_build_model_stack`: **remove the offline fallback**; raise loudly with no key (Q4). `ServeStack.generator` → `agent_core` (or keep + add). |
| `graph_app.py` | ✏️ | The `answer` node calls the agent core; keep chat state thin (`{messages, answer}`) — **agent internal messages stay node-local, never checkpointed** (preserves ADR 0001). Or repoint `langgraph.json` at `agent.py`'s serve graph directly. |
| `app.py` | ✏️ | Non-streaming `/chat` (line ~401) calls the agent core instead of `answer_question`; the "offline REST profile" framing goes away (key required). |
| `schemas.py` | ✏️ additive | `AnswerResponse` surfaces the governance ledger / richer provenance. |
| `routes.py` | ♻️ | Custom routes (`/schema`, `/graph`, `/corpus`, `/health`) unaffected. |

`langgraph.json` — ✏️ `graphs.serve` may repoint from `graph_app.py:make_graph`
to the new serve-graph builder.

---

## 3. `gateway/` — governance primitives (mostly reused)

| File | Fate | What changes |
|---|---|---|
| `guardrails.py` | ♻️ | `check`, `column_allowlist`, `GuardrailLayer` reused **unchanged** inside `wrap_tool_call`. The anti-drift guarantee. |
| `gateway.py` | ♻️/✏️ later | `execute` reused inside `run_query`. `AuditEntry` stays ephemeral now; the durable audit sink (Q3-b/c) extends it in P3. |
| `connectors/*` | ♻️ | Postgres/SQLite/Redshift connectors unchanged. |

---

## 4. `llm/` , `retrieval/` , `config`

| File | Fate | What changes |
|---|---|---|
| `llm/fake.py` | 🆕 | Scripted/fake-model helper for CI + the no-key demo (wraps `FakeListChatModel`). |
| `llm/client.py`, `langchain_client.py` | ✏️ minor | Docstring mentions of `TemplateSqlGenerator` updated; `LangChainChatClient` reused as the agent model. |
| `retrieval/schema_router.py` | ♻️ | `select_schema` / `route_schemas` become the schema-pick tool / pre-agent node. |
| `retrieval/rvgd.py`, `embedding.py` | ♻️ | `retrieve`, `filter_corpus_for_retrieval` reused inside `search_corpus`. |
| `config.py` | ✏️ | Add agent bounds (`recursion_limit≈15`, `run_query` cap=3) to `Settings`; add an `egress_mode` seam (default = send-all, Q5); make the key required (validation). `grade_semantic_failures` stays. |
| `governed_bi.toml` / `.local.toml` | ✏️ | `[runtime]` agent bounds; drop offline-mode notes. |
| `.env.example` | ✏️ | Document the key as **required**, not optional. |

---

## 5. `eval/` — arms + experiment

| File | Fate | What changes |
|---|---|---|
| `arms.py` | ✏️ | The layered/no-layer arms call the agent core instead of `answer_question`. |
| `run_experiment.py` | ✏️ | Build the agent core (not `LlmSqlGenerator`); set temperature 0 + seeds; nondeterminism handling is a **deferred branch** but flagged here. |
| `refuse_gate.py` | ✏️ | Uses `answer_question` → agent core. |
| `dataset.py` | ✏️/♻️ | `TemplateSqlGenerator` subset helper: keep as a **test util** or drop; not a serve path. |

---

## 6. Tests — contract change

The equivalence contract flips from *"same `Answer`"* to *"same governance
invariants"* (Q4). New harness = `FakeListChatModel` agent (precedent
`test_curator_deep_agent.py:111`).

| Test | Fate |
|---|---|
| `test_serve_graph.py` | 🗑️/✏️ — tests the retired DAG; rewrite against the new serve graph. |
| `test_server.py` | ✏️ — drops `TemplateSqlGenerator`; runs the agent core under a fake model. |
| `test_chat_graph.py` | ✏️ — chat state + agent-messages-stay-local. |
| `test_stage_events.py` | ✏️ — `_emit` stage assertions → `stream_mode` assertions. |
| `test_missing_edge.py`, `test_cache.py`, `test_sqlgen_llm.py` | ✏️ |
| 🆕 `test_middleware_guardrail.py` | guardrail + normalize + cap + L2-hard-stop in `wrap_tool_call`. |
| 🆕 `test_tool_scoping.py` | `excluded` never surfaces; licensing grows only via governed tools; RLS-as-identity. |
| 🆕 `test_governance_ledger.py` | one record per governed action; no execute-without-record. |
| 🆕 `test_governance_invariants.py` | the new equivalence contract. |

---

## 7. Docs, CI, scripts

- **CI** (`.github/workflows/ci.yml`) — ✏️ confirm the suite runs **key-free** via
  the fake-model harness (the agent path must not require a live key in CI). The
  "require committed demo DB" step stays.
- **Docs to update** — ✏️ `server.md`/`.zh`, `architecture.md`/`.zh`,
  `system-overview.md`, `usage.md`/`.zh`, `walkthrough.md`/`.zh`, `README.md`/`.zh`,
  `diagrams/overview.md`/`.zh`, `ui-frontend-*` (response contract), `openapi.json`
  (if `AnswerResponse` changes). On **accept**: fold the supersession into
  `pipeline-design.md` §5/§8 and add the D-number in `design-decisions.md`; mark
  `langgraph-rework-plan.md` partially superseded.
- **`scripts/live_smoke.py`** — ✏️ calls `answer_question`/serve → agent core.

---

## 8. Net summary

**Create (🆕):** `server/agent.py`, `server/tools.py`, `server/ledger.py`,
`llm/fake.py`, 4 test modules.
**Implement stub (✏️):** `server/middleware.py`.
**Delete (🗑️, P2):** `TemplateSqlGenerator` (in `sqlgen.py`), `server/graph.py`,
the `answer_question` monolith in `flow.py`, offline/no-key paths.
**Reused unchanged (♻️, load-bearing):** `gateway/guardrails.py`, connectors,
`retrieve`, `select_schema`, two-axis stamp, `SqlCache`, `route_intent`.

**Phase mapping**
- **P0** (no behavior change): extract shared governance module from `flow.py`;
  `llm/fake.py` + fake-model CI harness; `test_governance_invariants.py`.
- **P1** (flagged): `agent.py` + `tools.py` + `middleware.py` + `ledger.py`;
  wire behind a flag in `stack.py`/`graph_app.py`; A/B on BIRD.
- **P2** (cutover): make agent core the only path; require key; delete template /
  monolith / stale `graph.py`; flip the eval arms; docs.
- **P3** (deferred branches, own ADRs): HITL (`interrupt()` + durable
  checkpointer/Postgres), durable audit sink (Q3-b/c), data-privacy/egress (Q5).

## 9. Risks / watch-items

- **Chat-state pollution** — the agent's tool-call messages must not leak into the
  user-facing transcript or the checkpoint. Keep them node-local (ADR 0001).
- **L2 hard-stop from middleware** — confirm the mechanism (raise-and-catch in the
  outer graph vs. a middleware jump) during P1.
- **CI without a key** — the entire suite must run on the fake model; any test that
  silently needs a live key will break the offline guarantee.
- **Eval nondeterminism** — point estimates get noisy (already seen on the
  curator); needs seeds + temperature 0; full treatment is a deferred branch.
- **Cost/latency** — multi-call loop; the `recursion_limit` + cache are the guards.
