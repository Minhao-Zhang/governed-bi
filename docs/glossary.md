# Agentic BI Glossary

_[English](glossary.md) · [简体中文](glossary.zh.md)_

Canonical terms for the [Agentic BI System](system-overview.md). When a term
below conflicts with how something is being described, the term below wins.

> **Retired vocabulary**
>
> UDH.ai terms are not used: `category` → **governed dataset**; `fabric object`
> → **governed dataset** (optionally materialized); `app_ci` → the gateway's
> execution target.

| Term | Definition |
|---|---|
| **Domain** | A business area the agent serves (e.g. Sales, Support, Inventory). |
| **Governed dataset** | The canonical, single-source-of-truth *logical* model for a domain's questions. Grain, entities, columns, joins, and hygiene filters are defined once. A materialized view is an optional physical optimization, not the definition. |
| **Metric** | A compiled measure/dimension over a governed dataset that yields the same number everywhere. The unit that is certified (SemVer, draft→certified). |
| **Semantic layer** | The compiled definitions: governed datasets + metrics + term/business-rule resolution. Human-owned; the source of truth. |
| **Skill / reference doc** | Markdown procedural + descriptive knowledge per domain (routing rules, gotchas, query patterns). |
| **Corpus** | Umbrella for the shared human-owned substrate: semantic layer + skills + metadata/lineage + durable memory content. |
| **Gateway** | The read-only, policy-enforcing data-access boundary: credential isolation, RLS-as-user, forced LIMIT/timeout, audit/replay. The only path to data. |
| **Curator** (build agent) | Offline exploratory agent that *produces* the corpus (bootstrap + drift-repair). Writes are human-gated in prod. |
| **Server** (serve agent) | Online governed agent that *consumes* the corpus to answer. Fail-closed, auditable. |
| **Tool** | A coded function the model may decide to call. |
| **Hook** (middleware) | Deterministic code firing on loop events to inject context and/or veto actions. |
| **Memory** | Four stores: Working / Profile / Episodic / Correction. |
| **Working memory** | Verbatim per-session context (checkpointer). Ephemeral; identity-scoped. |
| **Governed path** | Answering from the semantic layer (the default). |
| **Discovery path** | Fenced raw exploration for questions the semantic layer does not cover. |
| **Promotion loop** | Distilling a discovered pattern into a certified governed dataset/metric after human review. |
| **Semantic plane / data plane** | Offline meaning (published via PR/CI) vs online execution (guardrail-gated). |
| **Negative example** | A curated pattern marking a question class as unanswerable-from-this-data; fires the canned escalation. |
| **Reliability stamp** | The provenance footer's source-tier + confidence marking on a best-effort answer. |
| **Reliability caveat** | An AI-inferred free-text warning on a *column* that it may be unreliable (`UNRELIABLE. DO NOT USE` plus a reason). Corpus-side and curator-authored, distinct from the answer-side **Reliability stamp**. It replaces a typed decoy flag so the mechanism transfers to an enterprise deployment. |
| **Governance exclusion** | A human-set `governance.excluded` boolean on a column/table meaning "never surface": the asset is removed from everything the server sees, all environments, permanently. Human-authored (D6); distinct from the curator's AI-inferred **Reliability caveat**. |
| **Clarification question** | A curator-emitted, ID-tracked open question about a corpus asset (e.g. "what does renamed column `kunde_id` mean?"), awaiting a **Responder**'s answer. Distinct from a **Reliability caveat** (the curator's own judgment): a Clarification question is addressed *to a human* and expects an answer back. |
| **Responder** | The pluggable role that answers **Clarification questions** in *free text* plus optional resources, never structured edits. Two implementations, both outside engine core: a human **SME** (product) and a **Simulated SME** (eval). |
| **SME** (subject-matter expert) | The human **Responder** in production: a non-technical domain expert who answers **Clarification questions** in free text. Never edits the corpus or opens a PR directly. |
| **Clarification answer** | A **Responder**'s free-text reply (plus optional resources) to a **Clarification question**. A *parse step* (the **Curator**/LLM or a data engineer) translates it into a structured corpus edit before it enters git. Resources land as `source_refs`. |
| **Simulated SME** | An eval-harness **Responder**: an LLM briefed with a dataset's *domain meaning* (what tables/columns represent), phrased as SME knowledge rather than a de-obfuscation map, and answering **Clarification questions** one at a time. Never handed a held-out **test** question's gold SQL (the one hard leakage invariant). May, in the limit, approach the **Gold semantic layer**, an accepted and documented limitation, since gold is a reference line, not a ceiling. |
| **Execution accuracy (EX)** | The agent's result matches gold, verified by re-executing gold SQL. |
| **Governed-path adherence** | Share of questions resolved via the semantic layer rather than raw tables. |
| **Decoy-touch rate** | Share of questions where the agent used a manifest-flagged fake column/table. |
| **No-layer arm** (baseline) | The eval floor: the **Server** answers with *no corpus at all*, given the raw (obfuscated) schema and question only. "Baseline" refers to this row specifically. _Avoid_: using "baseline" for the facts-only start. |
| **Facts-only corpus** | The auto-profiled starting corpus: physical types, sample values, and FK candidates (`curator/profile.py`), with **no Inference tier**. The start-of-growth row, before any **SME** interaction. |
| **Gold semantic layer** | The Arm-3 eval reference: a deterministic de-obfuscation oracle (rename map → real names, decoy manifest → exclusions, original schema → FK graph). No AI, no owner; a reference line, not a strict ceiling. BIRD-only. |
| **Schema** (namespace) | The single-level namespace inside the one database a run connects to (D15): one YAML subtree (`corpus/<schema>/`) and the per-asset `schema` field. Renamed from the field historically (mis)named `db`, which always denoted a schema. A run's database itself is connection config, not a corpus level. |
| **Cross-schema relationship** | A `join` asset whose two endpoints live in *different* schemas. **Curated only** — declared by an **SME**, distilled from example SQL, or mined from usage; never probed from database foreign keys or guessed from names. With no such asset the engine **refuses** the cross-schema question rather than inventing a join (D15). |
| **Schema router** | The retrieval pre-stage (D15) that shortlists the schemas relevant to a question before table retrieval, so thousands of tables across many schemas stay tractable. **Join-aware**: it expands along curated cross-schema joins so a bridge table in an un-mentioned schema is not dropped. |
| **Qualified identifier** | A fully-qualified `schema.table` (or `schema.table.column`) reference. Used end-to-end in **multi-schema mode** — retrieval, the guardrail allow-set, generated SQL, and execution. The single-schema path stays **bare/unqualified** (D15's mode-conditional rule protects the SQLite/BIRD graded path). |
| **Multi-schema mode** | The run mode where the connector spans every schema in the one database and cross-schema joins are executable (Postgres/Redshift only, v0). Distinct from *single-schema* mode (SQLite, or a pinned single Postgres schema), which is unchanged and emits bare SQL. Selected by an explicit signal, never by `schema` being unset. |
