# governed-bi design

_[English](README.md) · [简体中文](README.zh.md)_

Design for an agentic BI / Generative-BI system: natural-language questions →
grounded, governed, auditable answers over enterprise relational data.

Near-term target is a **SQLite-proven showcase** (with dialect-pluggable seams
for other engines) that grows a reviewable semantic layer from a seed of
known-good queries — *seed-assisted growth*, not a zero-prior cold start.
Enterprise abstractions are seamed in but toggled off. Evaluated on the
self-built [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) dataset (execution accuracy; cost logged).

## Read in this order

1. [System overview](system-overview.md): what this is, the two harnesses, status.
2. [Architecture](architecture.md): the full design (spine, kernel, services, storage, flow, eval, environments).
3. [Diagrams](diagrams.md): Mermaid architecture, data-flow, and user sequence diagrams.
4. [Design decisions](design-decisions.md): D1-D15 as ADRs, with alternatives and trade-offs.
5. [Asset schemas](asset-schemas.md): the per-asset YAML field spec (Facts / Inference / Audit tiers).
6. [Curator](curator.md): the build-side proposer + adversary loop.
7. [Server](server.md): the serve-side LangGraph flow + guardrails.
8. [Viz](viz.md): the read-only audit surface — the presenter view models plus the `governed_bi.api` HTTP API to browse the layer and chat with the governed server (the interactive UI is a separate project).
9. [Glossary](glossary.md): canonical terms.

[External design sources](references.md) that ground the design.

## Using the repo

The design docs above describe the intended system. For what actually runs
today (the corpus layer and the dev workflow):

- [Walkthrough](walkthrough.md): clone → validate → ask your first question. **Start here.**
- [Usage](usage.md): install, the validate CLI, and the programmatic corpus API.
- [Corpus authoring](corpus-authoring.md): write and validate corpus assets step by step.

## The spine (non-negotiables)

- **Two planes.** A semantic/control plane (versioned config + markdown, published via PR/CI) stays separate from a data plane that executes only guardrail-passed SQL. Meaning is defined once and owned by humans.
- **Deterministic DAG + conditional routing, not autonomous ReAct.** The question can be wide; the SQL must be narrow.
- **Fail-closed.** Out-of-scope / missing-coverage / tripped-guardrail returns a refusal or a clarifying question, never a confident wrong number.

## How the docs map to the code

| Doc | Package area |
|---|---|
| [Asset schemas](asset-schemas.md), [Design decisions](design-decisions.md) D9 | `src/governed_bi/corpus/` |
| [Diagrams](diagrams.md) | End-to-end map across `src/governed_bi/` and `corpus/` |
| [Curator](curator.md) | `src/governed_bi/curator/` |
| [Server](server.md), [Architecture](architecture.md) §6 | `src/governed_bi/server/`, `gateway/`, `graph/`, `retrieval/`, `memory/` |
| [Architecture](architecture.md) §8 | `src/governed_bi/eval/` |
| [Viz](viz.md) | `src/governed_bi/viz/` |
| [Architecture](architecture.md) §9 (environment toggles) | `src/governed_bi/config.py` |
