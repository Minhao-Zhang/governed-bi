# Agentic BI Server

The serve-side agent for the [Agentic BI System](system-overview.md). It is the
online governed agent that *consumes* the corpus to answer, **fail-closed and
auditable** (two-harness split; `LangGraph` + middleware). Counterpart to the
[Curator](curator.md); consumes the assets in [Asset schemas](asset-schemas.md).

> Implementation: [`src/governed_bi/server/`](../src/governed_bi/server/),
> with guardrails/gateway in [`gateway/`](../src/governed_bi/gateway/), join
> planning in [`graph/`](../src/governed_bi/graph/), and RVGD in
> [`retrieval/`](../src/governed_bi/retrieval/).

## Shape

A **deterministic LangGraph DAG with conditional routing** (design-spine #2, never autonomous ReAct). Middleware: `before_model` injects context (working memory, RLS scope, semantic-layer router); `wrap_tool_call` runs the guardrails, and fail-closed lives here.

## The flow

1. **Ingest**: question + identity (D7, as-user) + working memory (D8, session-scoped).
2. **Query understanding + term binding**: resolve business language via `term` assets. Synonyms and `term_relationship` map varied phrasings → the canonical asset (strong-routing, not an LLM guess).
3. **Intent routing**: hard-wired route (`nl2sql | kpi_lookup | knowledge_qa | deep_analysis`), each with its own retrieval and memory budget.
4. **SQL semantic-cache fast path**: question embedding → cosine ≥0.92 vs cached SQL → hit skips retrieval/plan/gen but **always re-executes** (SQL-text-only, as-user, D7). TTL 15 min; write back on success.
5. **RVGD retrieval**: R exact / V semantic / G graph / D dictionary. Four-stage rerank, token-budgeted, Corrective-RAG fallback. **Facts + Inference tiers only** (loader contract); Audit and `excluded` assets never retrieved.
6. **Steiner-tree join planning** over the inferred FK graph.
7. **SQL generation**: layered system prompt (role → schema constraint → safety → output). Emits **physical (obfuscated) identifiers**.
8. **Five guardrails** (`wrap_tool_call`, fail-closed on any): syntax → policy blacklist → AST column allowlist → term-semantics → cost/EXPLAIN. Refuse-gate runs **concurrently** (D5).
9. **Execute as-user**: gateway RLS, forced LIMIT/timeout, audit/replay.
10. **Answer + reliability stamp**: best-effort tiering (governed → lineage → fenced-raw). High-stakes → sign-off / SQL-only.

**Self-repair (steps 7-9 as a bounded loop).** Generation, guardrails, and execution run as a loop: a guardrail rejection or an execution error is fed back to the generator for another attempt, each attempt re-guardrailed so un-vetted SQL never runs. It stops early when the generator cannot improve (repeats a query) and fails closed after a small cap. A repaired answer is stamped `lineage`, not `governed`. This recovers malformed or out-of-scope SQL without ever emitting an unchecked query; it cannot catch *plausible-but-wrong* SQL (valid, in-allowlist, but the wrong computation), which is exactly why the reliability stamp and the refuse / SQL-only paths exist. The guardrails are a safety/governance gate, not a correctness oracle.

## Three points where curator inference drives serve behavior

The Inference tier *steers*, it doesn't decorate. This is what separates the server from a generic text-to-SQL pipeline.

1. **Reliability caveats → decoy avoidance.** A `suspect` column's caveat is injected into SQL-gen ("DO NOT USE …") and checkable at guardrail L3 (AST). This is where **decoy-touch rate** is won or lost.
   - **Enforcement env-toggle:** dev/BIRD **hard-blocks** any SQL referencing a `suspect` column (decoys are never needed → drives decoy-touch → 0); prod/enterprise **soft-warns + drops the reliability tier** (a false-positive flag must never silently block a real answer).
2. **Join `confidence` → planning + uncertainty.** Low-confidence inferred joins get a **cost penalty** in the Steiner plan; a below-threshold join in the chosen path **propagates to the reliability stamp**.
3. **Skills → SQL-gen shaping** (routing / gotchas). This is the lever that lets **Arm 2 beat the Arm 3 gold ceiling**.

**Uncertainty aggregation → reliability stamp:** low-confidence join used · fenced-raw fallback · Corrective-RAG triggered · suspect column in scope → lower tier → differential handling (D5, give the stamp teeth).

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
