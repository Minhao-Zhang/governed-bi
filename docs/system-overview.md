# Agentic BI System

_[English](system-overview.md) · [简体中文](system-overview.zh.md)_

> **What this is**
>
> Design for an agentic BI / Generative-BI system: natural-language questions →
> grounded, governed, auditable answers over enterprise relational data.
> Near-term = a **SQLite-proven showcase** (personal GitHub; dialect-pluggable
> seams for other engines) that grows a reviewable semantic layer from a seed of
> known-good queries — *seed-assisted growth*, not a zero-prior cold start.
> Enterprise abstractions are seamed in but toggled
> off. Evaluated on the self-built [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) dataset (execution
> accuracy; cost logged). A private **enterprise fork** (phase 2) reuses this
> engine at enterprise scale, facing the same no-owner / no-manpower situation.

## Key points

- Two harnesses over one shared substrate: **curator** (builds the corpus) and **server** (answers). The semantic layer is the moat. Fail-closed.
- Design notes:
    - [Architecture](architecture.md): full design
    - [Design decisions](design-decisions.md): D1-D10 with alternatives and trade-offs
    - [Asset schemas](asset-schemas.md): the per-asset YAML field spec (Facts / Inference / Audit tiers)
    - [Curator](curator.md): the build-side proposer + adversary loop
    - [Server](server.md): the serve-side LangGraph flow + guardrails
    - [Viz](viz.md): the read-only audit surface — the presenter view models + the `governed_bi.api` HTTP API to browse the layer + chat with the server
    - [Glossary](glossary.md): canonical terms
- Grounded in the [external design sources](references.md).

## Status

> **Decided (D1-D10)**
>
> target · governed unit · eval · grading · refusal · ownership · identity ·
> memory · corpus contract · curator gate. See [Design decisions](design-decisions.md).

> **Built (code)**
>
> corpus (schemas / loader / validate / serialize) · graph projection + Steiner
> join planner (in-memory networkx) · gateway + five-layer guardrails · RVGD
> retrieval (BM25 + ground expansion, plus an embedder-gated vector channel fused
> with BM25 via RRF) · retrieval→context assembly · the serve flow (refuse-gate,
> template AND LLM SQL-gen, bounded self-repair, SQL semantic cache, reliability
> stamp) · working memory · the eval scaffold · the read-only viz presenter view models + the `governed_bi.api` HTTP API · model
> config (`governed_bi.toml`) and the `ChatClient` / `Embedder` seams (raw OpenAI +
> LangChain + deterministic offline defaults) · the LLM curator proposer
> (descriptions + `suspect` caveats) · the **LangGraph serve harness**
> (`server.graph`, Answer-equivalent to the plain flow) and the **deepagents
> curator harness** (`curator.deep_agent`, construction). The core slice runs
> end-to-end with no model or network; the harnesses run behind the `agents` extra
> on offline model doubles.

> **Pending (code)**
>
> LLM authoring of the remaining Inference assets (joins / terms / metrics / rules
> / skills) and the live per-asset adversary `refute` · the curator self-eval
> train-EX loop · the obfuscated BIRD eval data (a small vendored beer_factory set
> stands in until the jsonl lands) · a first run against a **live** OpenAI API
> (everything so far uses the offline doubles). Without the eval data the arms
> cannot yet show the moat.

> **Open (design-level)**
>
> - Reliability-inference signals: the exact evidence the curator uses (deepens Curator Phase 2)
> - Refuse-gate + negative-example curation + held-out unanswerable set
> - Server tool registry (few, sharp): the exact tool list (flow in [Server](server.md))
> - Curator exploration tactics: probe-query strategies (loop in [Curator](curator.md))
>
> *Parked (development, per "design-first"):* build ordering / critical path.
> *Resolved → notes/decisions:* storage layout (D9) · gold auto-derivation (D4)
> · train/test split (§8) · corpus schemas ([Asset schemas](asset-schemas.md))
> · curator loop ([Curator](curator.md)) · server flow ([Server](server.md))
> · viz/audit ([Viz](viz.md)).
