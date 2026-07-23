# Agentic BI Glossary

_[English](glossary.md) · [简体中文](glossary.zh.md)_

Canonical terms for the [Agentic BI System](system-overview.md). When a term
below conflicts with how something is being described, the term below wins.

> **Retired vocabulary**
>
> UDH.ai terms are not used: `category` → **governed dataset**; `fabric object`
> → **governed dataset** (optionally materialized); `app_ci` → the gateway's
> execution target.
>
> Also retired by the [terminology refactor](plans/terminology-refactor.md):
> `A1` / `A2` / `A3` (→ `baseline` / `curated` / `curated_sme`); the `gold` arm /
> `build_gold_corpus` (→ `ceiling`, designed not built); `no_layer` and
> `facts_only` as standalone arms (folded into `baseline`); `certified` *as a
> reliability-stamp value* (→ `grounded` — `ProvenanceStatus.certified` and the
> metric `draft→certified` lifecycle are unaffected); the legacy single-axis
> tier `governed` / `lineage` / `fenced_raw` / `refused` (kept, if surfaced at
> all, only as a display-only projection of the two-axis stamp); `Server` *as
> the serve agent* (→ **Analyst**; "server" / "LangGraph Server" still mean
> infra only); `flow` / `flow_solver`; `DataSourceConfig.db` (→ `corpus_pin`).
>
> Also retired by [ADR 0003](adr/0003-governed-notes-tri-modal-retrieval.md) /
> **D17** (2026-07-22): `skill` (→ **Note**; `SkillFrontmatter` / `SkillKind`
> deleted, `RuleAsset` generalized into `NoteAsset`).
>
> Also retired 2026-07-17 ([D15](design-decisions.md#d15-multi-schema-serving-one-database-many-schemas)):
> `multi_schema` mode / single-schema mode as a toggle — the engine is now
> uniformly schema-qualified; only the number of schemas present differs.

| Term | Definition |
|---|---|
| **Domain** | A business area the agent serves (e.g. Sales, Support, Inventory). |
| **Governed dataset** | The canonical, single-source-of-truth *logical* model for a domain's questions. Grain, entities, columns, joins, and hygiene filters are defined once. A materialized view is an optional physical optimization, not the definition. |
| **Metric** | A compiled measure/dimension over a governed dataset that yields the same number everywhere. The unit that is certified (SemVer, draft→certified). |
| **Semantic layer** | The compiled definitions: governed datasets + metrics + term/business-rule resolution. Human-owned; the source of truth. |
| **Note** (`NoteAsset`) | Governed annotation — routing rules, gotchas, query patterns, business rules, context — attachable to any asset or namespace (`schema:` / `db:` scope sentinels, or an asset id). Carries the full three-tier + `Governance` structure and provenance-aware, tri-modal retrieval (semantic / trigger-PIN / agent-fetch). Formerly the ungoverned Markdown **Skill / reference doc** (ADR 0003, D17). |
| **Corpus** | Umbrella for the shared human-owned substrate: semantic layer + notes + metadata/lineage + durable memory content. |
| **Gateway** | The read-only, policy-enforcing data-access boundary: credential isolation, RLS-as-user, forced LIMIT/timeout, audit/replay. The only path to data. |
| **Curator** (build agent) | Offline exploratory agent that *produces* the corpus (bootstrap + drift-repair). Writes are human-gated in prod. |
| **Analyst** (serve agent) | Online governed agent that *consumes* the corpus to answer. Fail-closed, auditable. Formerly "Server"; "server" / "LangGraph Server" now mean infra only. |
| **Tool** | A coded function the model may decide to call. |
| **Hook** (middleware) | Deterministic code firing on loop events to inject context and/or veto actions. |
| **Memory** | Four designed stores (Architecture §7): **Working** (built, session-scoped) plus three durable ones, off-by-default and adopted only when eval earns it — **Profile**, **Episodic**, **Correction**. Only Working is implemented; Episodic/Correction are unimplemented protocol seams; Profile is config-only (a route budget + `profile_ttl_days`, no store seam yet — the lowest-priority durable store). |
| **Working memory** | Verbatim per-session context (checkpointer). Ephemeral; identity-scoped. |
| **Governed path** | Answering from the semantic layer (the default). |
| **Discovery path** | Fenced raw exploration for questions the semantic layer does not cover. |
| **Promotion loop** | Distilling a discovered pattern into a certified governed dataset/metric after human review. |
| **Semantic plane / data plane** | Offline meaning (published via PR/CI) vs online execution (guardrail-gated). |
| **Negative example** | A curated pattern marking a question class as unanswerable-from-this-data; fires the canned escalation. |
| **Reliability stamp** | The two-axis marking on a delivered answer (D5): `safety_clearance` (bool hard gate) and `semantic_assurance` (`grounded` / `heuristic` / `unverified` — how well-grounded). `grounded` means safe + in-scope, **not** verified-correct; thresholds uncalibrated (Audit R2). |
| **Reliability caveat** | An AI-inferred free-text warning on a *column* that it may be unreliable (`UNRELIABLE. DO NOT USE` plus a reason). Corpus-side and curator-authored, distinct from the answer-side **Reliability stamp**. It replaces a typed decoy flag so the mechanism transfers to an enterprise deployment. |
| **Governance exclusion** | A human-set `governance.excluded` boolean on a column/table meaning "never surface": the asset is removed from everything the **Analyst** sees, all environments, permanently. Human-authored (D6); distinct from the curator's AI-inferred **Reliability caveat**. |
| **Interaction signal** | A recorded observation of a user action on a served answer — a **Correction signal**, a rephrased re-ask, a regenerate, an abandonment, or an explicit rating — captured for *evaluation* (production quality, run against metrics) and *development* (passive semantic-layer improvement). Captured **raw** (capture-first); trust-tiering/interpretation is deferred until real usage shows what correlates with a wrong answer. v0 rides Langfuse/LangSmith trace feedback; a dedicated, queryable interaction log (keyed by turn + corpus-release hash) is future work. |
| **Correction signal** | The high-trust subtype of **Interaction signal**: a *user-initiated* observation that an answer was wrong in a specific, nameable way (e.g. "revenue should exclude refunds"). Distinct from a **Clarification question** (curator-initiated, addressed *to* a human) and from **Correction memory** (a store). A Correction signal is a *hypothesis*: it must be validated against the query and pass the human PR gate before it can change the corpus — never an auto-edit. |
| **Clarification question** | A curator-emitted, ID-tracked open question about a corpus asset (e.g. "what does renamed column `kunde_id` mean?"), awaiting a **Responder**'s answer. Distinct from a **Reliability caveat** (the curator's own judgment): a Clarification question is addressed *to a human* and expects an answer back. |
| **Responder** | The pluggable role that answers **Clarification questions** in *free text* plus optional resources, never structured edits. Two implementations, both outside engine core: a human **SME** (product) and a **Simulated SME** (eval). |
| **SME** (subject-matter expert) | The human **Responder** in production: a non-technical domain expert who answers **Clarification questions** in free text. Never edits the corpus or opens a PR directly. |
| **Clarification answer** | A **Responder**'s free-text reply (plus optional resources) to a **Clarification question**. A *parse step* (the **Curator**/LLM or a data engineer) translates it into a structured corpus edit before it enters git. Resources land as `source_refs`. |
| **Simulated SME** | An eval-harness **Responder**: an LLM briefed with a dataset's *domain meaning*, answering **Clarification questions** one at a time, never handed a held-out **test** question's gold SQL. Pull-based (answers only what the curator asks). Powers the `curated_sme` arm and the `ceiling`. |
| **Execution accuracy (EX)** | The agent's result matches gold, verified by re-executing gold SQL. |
| **Governed-path adherence** | Share of questions resolved via the semantic layer rather than raw tables. |
| **Decoy-touch rate** | Share of questions where the agent used a manifest-flagged fake column/table. |
| **Baseline** (eval floor) | The deterministic, script-built corpus — table/column names, types, **sample values**, FK candidates — with **no curator LLM** and **no train-SQL-derived** assets. Served through the same **Analyst** path as every arm. Isolates "what a script knows about the database." Replaces the old raw-dump no-layer arm **and** the facts-only row. |
| **Curated arm** | `baseline` + the curator's LLM-authored **Inference tier** (descriptions, reliability caveats, terms, metrics) **and** train-SQL-derived assets (seed joins, few-shots). `baseline → curated` isolates what the semantic layer adds. |
| **Curated+SME arm** (`curated_sme`) | `curated` + one or more Simulated-SME clarification rounds. The growth axis. |
| **Recoverable ceiling** (`ceiling`) | The dashed upper-bound line: a test-aware Simulated SME holding the held-out test questions + evidence (never test gold SQL) in its retrieval index. Deliberately-leaky oracle, walled off from the fair arms. Replaces the retired de-obfuscation "gold" arm. Designed, not yet built. |
| **Schema** (namespace) | The single-level namespace inside the one database a run connects to (D15): one YAML subtree (`corpus/<schema>/`) + the per-asset `schema` field. The run's database is connection config (`corpus_pin`), not a corpus level. |
| **Cross-schema relationship** | A `join` asset whose two endpoints live in *different* schemas. **Curated only** — declared by an **SME**, distilled from example SQL, or mined from usage; never probed from database foreign keys or guessed from names. With no such asset the engine **refuses** the cross-schema question rather than inventing a join (D15). |
| **Schema router** | The retrieval pre-stage (D15) that shortlists the schemas relevant to a question before table retrieval, so thousands of tables across many schemas stay tractable. **Join-aware**: it expands along curated cross-schema joins so a bridge table in an un-mentioned schema is not dropped. |
| **Qualified identifier** | A fully-qualified `schema.table` (or `schema.table.column`) reference. Used end-to-end, **always** — retrieval, the guardrail allow-set, generated SQL, and execution (D15, superseded 2026-07-17: uniformly schema-qualified). A *bare* reference resolves to the serving schema (`DataSourceConfig.serving_schema()`), or fails closed when the source spans all schemas with no default. |
