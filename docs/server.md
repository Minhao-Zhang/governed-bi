# Agentic BI Server

_[English](server.md) · [简体中文](server.zh.md)_

The serve-side agent for the [Agentic BI System](system-overview.md). It is the
online governed agent that *consumes* the corpus to answer, **fail-closed and
auditable** (two-harness split; `LangGraph` + middleware). Counterpart to the
[Curator](curator.md); consumes the assets in [Asset schemas](asset-schemas.md).

> Implementation: [`src/governed_bi/server/`](../src/governed_bi/server/),
> with guardrails/gateway in [`gateway/`](../src/governed_bi/gateway/), join
> planning in [`graph/`](../src/governed_bi/graph/), and RVGD in
> [`retrieval/`](../src/governed_bi/retrieval/).

## Shape

The serve runtime is **being reworked into a governed agentic core** ([ADR 0002](adr/0002-governed-agentic-serve-runtime.md), *Proposed*). The organizing principle is **authority is deterministic; reasoning may be agentic**. ADR 0002 reverses the old design-spine #2 invariant ("never an autonomous ReAct loop"). Autonomy is granted for *how to find the answer*, never for *what may execute* or *what is trusted*: the agent reasons freely, but every tool call passes through middleware that runs the guardrails and records the audit, and the answer is stamped by deterministic code the agent cannot influence.

Both paths described below **share one governance core** (`check` / column-allowlist / licensed-table / refuse-gate / stamp helpers), so the guardrails cannot drift between them.

> *Built (today's reality):* two serve paths behind the **`agent_serve`** config flag (default off; a local overlay may enable it).
> - **Deterministic flow (the code default).** `server.flow.answer_question` is the plain deterministic reference; `server.graph.build_serve_graph` / `answer_question_graph` is the equivalent **LangGraph `StateGraph`** harness (nodes: ingest → refuse_gate → prepare → cache → retrieve+context → generate → guardrail → execute → stamp; the self-repair loop is a graph cycle back to `generate`). The two are Answer-equivalent by construction (the graph nodes call the same helpers), asserted in the tests.
> - **Agentic path (P0/P1 landed, flagged).** When `agent_serve` is on *and* a live model is present, `server.agent` compiles an outer deterministic `StateGraph` (`ingest → refuse_gate → prepare → cache → assemble → agent_core`) that wraps an inner LangChain `create_agent` reasoning loop. Governance rides on `GovernanceMiddleware` (`server.middleware`); the four governed tools live in `server.tools`; `llm.fake` supplies a `FakeListChatModel` harness for CI determinism.
>
> P2 cutover (deleting the flow/template path and requiring a live key) is **not** done; the deterministic flow remains present and is what runs by default. See ADR 0002 and the A/B results in [`docs/plans/agentic-serve-ab-results.md`](plans/agentic-serve-ab-results.md).

## The flow

1. **Ingest**: question + identity (D7, as-user) + working memory (D8, session-scoped).
2. **Query understanding + term binding**: resolve business language via `term` assets. Synonyms and `term_relationship` map varied phrasings → the canonical asset (strong-routing, not an LLM guess).
3. **Intent routing**: hard-wired route (`nl2sql | kpi_lookup | knowledge_qa | deep_analysis`), each with its own retrieval and memory budget.
4. **SQL semantic-cache fast path**: question embedding → cosine ≥0.92 vs cached SQL → hit skips retrieval/plan/gen but **always re-executes** (SQL-text-only, as-user, D7). TTL 15 min; write back on success. *Built:* `server.cache.SqlCache` (off by default, injected). A hit is additionally **re-guardrailed** against the licensed tables it was stored with, then re-executed; a stale/now-blocked hit falls through to the full pipeline (fail-closed). Admission gates on the **semantic** axis, never on safety alone: only `certified` answers (clean run, no uncertainty flag) are written back.
5. **RVGD retrieval**: R exact / V semantic / G graph / D dictionary. Four-stage rerank, token-budgeted, Corrective-RAG fallback. **Facts + Inference tiers only** (loader contract); Audit and `excluded` assets never retrieved. *Built:* the pure-Python **BM25** lexical channel plus deterministic grounding (a bound term pulls in its target, a metric its base table, a table its columns), and the **V (vector) channel** (`retrieval.embedding`): an injected `Embedder` (OpenAI `text-embedding-3-small`, or the deterministic offline `HashingEmbedder`) ranks by cosine and is fused with BM25 via Reciprocal Rank Fusion. Off unless an embedder is passed, so the default is pure BM25. The graph channel (G) and Corrective-RAG rerank remain later slices.
   - **Context assembly** (`server.context.assemble_context`): retrieval returns ids; this resolves the L4-licensed table scope into a `PromptContext` (physical schema, join paths with confidence, terms, metrics, suspect-column caveats, gold exemplars, skills). The guardrail's `allowed_tables` is derived from it, so **what the generator can see is exactly what L4 permits**.
6. **Steiner-tree join planning** over the inferred FK graph.
7. **SQL generation** (a pluggable seam, `SqlGenerator`): the design-vision generator layers a system prompt (role → schema constraint → safety → output) over an LLM, emitting **physical (obfuscated) identifiers**. *Built:* both `TemplateSqlGenerator` (deterministic, single-table metric aggregates, no model) and `LlmSqlGenerator` (reads the `PromptContext`, calls an injected `ChatClient` = OpenAI `gpt-5.5` low, is feedback-aware for the repair loop, and declines with a `CANNOT_ANSWER` sentinel). Which one runs is an injection choice on the deterministic flow. **Slated for removal (ADR 0002, Q4/P2):** the template / no-model serve mode goes away: the agentic path requires a live model, and CI determinism moves to the `FakeListChatModel` harness. The `SqlGenerator` seam is replaced by the agent's governed `run_query` tool (below).
8. **Five guardrails** (`wrap_tool_call`, fail-closed on any, all five enforced): syntax → policy blacklist → AST column allowlist → term-semantics → cost. **L3 is scope-aware** (sqlglot `traverse_scope`): it resolves each column against its own query scope, checks every column node (including bare `HAVING` refs and `USING` / `NATURAL` join keys), and blocks star projections (`SELECT *` / `t.*`) the allowlist cannot vouch for. **L4 (term-semantics)** licenses the retrieved tables plus their FK join-neighborhood (one hop, tunable) and the Steiner points the join plan bridges through - not the exact retrieved set, so it is decoupled from lexical-retrieval recall - and, in **multi-schema mode**, spans schemas: a cross-schema table name is licensed only via a **curated** join (memory-sourced, never FK-discovered), and with none the engine **refuses** rather than guesses. Qualification is mode-conditional (**D15**): the single-schema / SQLite / BIRD path stays **bare/unqualified**, only the multi-schema Postgres / Redshift path emits schema-qualified names; L3 still guards every column, so widening the table scope never leaks an excluded or `suspect` column (a neighbor table exposes only its already-allowed columns). **L5** is a structural cross-join / cartesian guard; numeric EXPLAIN-based cost (Postgres / Redshift) is future per-dialect work. Refuse-gate runs **concurrently** (D5).
9. **Execute as-user**: gateway RLS, forced LIMIT/timeout, audit/replay.
10. **Answer + reliability stamp**: a **two-axis** stamp — `safety_clearance` (guardrails + authorization passed, a gate) and `semantic_assurance` (`certified` → `heuristic` → `unverified`, how well-grounded). The single-axis tier (governed → lineage → fenced-raw) is their compact projection. High-stakes → sign-off / SQL-only.

**Self-repair (steps 7-9 as a bounded loop).** Generation, guardrails, and execution run as a loop: a *repairable* guardrail rejection or an execution error is fed back to the generator for another attempt, each attempt re-guardrailed so un-vetted SQL never runs. It stops early when the generator cannot improve (repeats a query) and fails closed after a small cap. A repaired answer has `heuristic` semantic assurance (tier `lineage`), never `certified`/`governed`. **Not every failure is repairable:** a hard policy/DDL block (L2 `policy_blacklist`) fails closed immediately, because feeding it back is only pressure to evade the policy. Scope failures (L3/L4) stay repairable by decision (the FK-neighborhood + repair loop is deliberate false-refusal reduction; [D11](design-decisions.md#d11-external-review-2026-07-09)). This recovers malformed SQL without ever emitting an unchecked query; it cannot catch *plausible-but-wrong* SQL (valid, in-allowlist, but the wrong computation), which is exactly why the two-axis stamp and the refuse / SQL-only paths exist. The guardrails are a safety/governance gate, not a correctness oracle.

> *Agentic path:* this hand-rolled `while attempts < N` cycle becomes the **agent's own tool-reflection loop** (a failed `run_query` returns as a `ToolMessage` the agent can read and retry) with the same fail-closed discipline: a `run_query` **attempt cap** (3) is enforced in `wrap_tool_call`, an L2 policy block is a hard stop (never coached back), and every attempt is re-guardrailed before it can execute. The outer graph is bounded by a `recursion_limit`; exhaustion falls through to graded delivery or refuse.

## The agentic path (ADR 0002, flagged)

When `agent_serve` is on, the SQL-gen-and-execute middle (steps 6-9 above) is replaced by a bounded `create_agent` reasoning loop. The deterministic **rails** stay: `ingest → refuse_gate → prepare → cache → assemble → agent_core`, then a deterministic `finalize` (two-axis stamp + cache write) or graded-delivery / refuse. The refuse-gate still runs **before** the agent, and the stamp is still computed by deterministic code the agent cannot influence.

**Governed tools (read-only ONLY, `server.tools`).** The agent can act *only* through four tools:

- `search_corpus(query)`: retrieve tables / terms / joins / metrics / few-shots; each hit **expands the per-turn `licensed` set** (post-Amendment 1 it returns curated *content*, not just ids).
- `inspect_schema(table_id)`: columns, types, sample values for a licensed table (fixes "the model never sees table structure"); licenses tables beyond the seed.
- `sample_rows(table_id, n)`: row preview, runs **as identity** (RLS).
- `run_query(sql)`: **the only path to data**; the agent never calls `gateway.execute` directly.

**`GovernanceMiddleware` (`server.middleware`).** Governance is a mandatory interception layer, not the agent's discretion:

- `wrap_tool_call` normalizes each call (`sqlglot identify=True`), runs the **L1-L5 guardrail** over the current `licensed` set, enforces the `run_query` attempt cap, and writes a **governance ledger** entry, an append-only audit record of every governed action (refuse-gate result, tools offered, each exploration's surfaced / `excluded`-filtered assets and licensing deltas, each `run_query`'s normalized SQL + per-layer verdict + `allowed_tables` + result meta). You can never execute (or refuse) *without* a record.
- `wrap_model_call` scopes which tools the model is offered (identity tool-scoping).

**The invariant that survives:** the **guardrails still run in middleware BEFORE any execution**: the same five layers, fail-closed, now enforced at the *tool boundary* (`wrap_tool_call`) instead of a graph node. Licensing derives from **governed exploration, not agent claims**: `allowed_tables` is the set of tables surfaced through governed tools this turn (FK-expanded), so a rogue agent cannot self-authorize an `excluded` table; L3 still guards every column.

**Amendment 1: seed the semantic layer.** A first live A/B showed the tools-only agent *regressing* vs. the flow, because the P1 tools surfaced only names and none of the curated semantic layer (few-shots, join `ON` clauses, metric expressions, terms, rules). The fix: a deterministic **`assemble` node runs before `agent_core`** and seeds the agent with the *same* semantic-layer context the flow uses (`PromptContext.render()`) as a `## Governed context` block, and pre-populates the `licensed` channel with the base (retrieved + FK-neighborhood + Steiner) table scope. Tools become **refinement, not discovery**. This is the flow's *deterministic* L4 floor (not agent-claimed), so the seeded scope is strictly ≥ the flow's and never self-authorized. See ADR 0002 Amendment 1 and [`docs/plans/agentic-serve-ab-results.md`](plans/agentic-serve-ab-results.md).

## Three points where curator inference drives serve behavior

The Inference tier *steers*, it doesn't decorate. This is what separates the server from a generic text-to-SQL pipeline.

1. **Reliability caveats → decoy avoidance.** A `suspect` column's caveat is injected into SQL-gen ("DO NOT USE …") and checkable at guardrail L3 (AST). This is where **decoy-touch rate** is won or lost.
   - **Enforcement env-toggle:** dev/BIRD **hard-blocks** any SQL referencing a `suspect` column (decoys are never needed → drives decoy-touch → 0); prod/enterprise **soft-warns + drops the reliability tier** (a false-positive flag must never silently block a real answer).
2. **Join `confidence` → planning + uncertainty.** Low-confidence inferred joins get a **cost penalty** in the Steiner plan; a below-threshold join in the chosen path **propagates to the reliability stamp**.
3. **Skills → SQL-gen shaping** (routing / gotchas). This is the lever that lets **Arm 2 beat the Arm 3 gold ceiling**.

**Uncertainty aggregation → `semantic_assurance`:** low-confidence join used · fenced-raw fallback · Corrective-RAG triggered · suspect column in scope · SQL repaired → drops `certified` to `heuristic` (or `unverified`) → differential handling (D5, give the stamp teeth). This is the *semantic* axis only; `safety_clearance` is a separate pass/fail gate that says nothing about how right the number is. The levels are **uncalibrated governance/uncertainty heuristics**, to be tuned on the eval: `certified` means safe, in-scope, and no uncertainty flag fired, **not** verified-correct. Because fail-closed carries a false-refusal cost, the eval's `false_refusal_rate` ([Architecture](architecture.md) §8) is its counterweight.

## Governance exclusion (hard, human-set)

Distinct from the curator's AI-inferred `reliability.suspect`: a human owner sets `governance.excluded: true` on a column/table after review → the asset is **removed entirely** from everything the server sees (retrieval, presented schema, graph), in **all environments, no toggle, permanently**. It still appears in the viz/audit surface (marked, with reason) so the exclusion is auditable, and guardrail L3 hard-blocks it as defense-in-depth. Escalation path: curator flags `suspect` → human reviews (D6) → leaves it, or escalates to `excluded`. This stays **out of the autonomous eval arms** (so Arm 2 stays pure-curator); it is the human-in-the-loop governance capability for enterprise deployments. Spec in [Asset schemas](asset-schemas.md).

## Refuse / best-effort decision tree (fail-closed, D5)

Refusal is driven by a **curated signal (`negative_example` assets), not a coverage heuristic**: a semantic-similarity match run concurrently with the hard guardrails.

- refuse-gate match (negative example) **or** hard-guardrail veto → **refuse** (canned escalation)
- else governed coverage → **answer: governed** (high stamp)
- else lineage-derivable → **answer: lineage** (medium stamp)
- else fenced-raw possible → **answer: fenced-raw** (low stamp)
- else no path above the confidence floor → **refuse / clarify** (fail-closed)
- high-stakes (leadership / PII) → sign-off or SQL-only, regardless

Never a confident wrong number.

Links: [Design decisions](design-decisions.md) (D5 refusal · D6 ownership · D7 identity · D8 memory · D10 curator) · [Asset schemas](asset-schemas.md) · [Curator](curator.md) · [Architecture](architecture.md) §6.
