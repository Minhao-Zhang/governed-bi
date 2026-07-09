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
| **Execution accuracy (EX)** | The agent's result matches gold, verified by re-executing gold SQL. |
| **Governed-path adherence** | Share of questions resolved via the semantic layer rather than raw tables. |
| **Decoy-touch rate** | Share of questions where the agent used a manifest-flagged fake column/table. |
| **Gold semantic layer** | The Arm-3 eval baseline: a deterministic de-obfuscation oracle (rename map → real names, decoy manifest → exclusions, original schema → FK graph). No AI, no owner; a reference line, not a strict ceiling. BIRD-only. |
