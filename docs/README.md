# governed-bi design

Design for an agentic BI / Generative-BI system: natural-language questions →
grounded, governed, auditable answers over enterprise relational data.

Near-term target is a **general, DB-agnostic showcase** that cold-starts from
`{a DB connection + a few known-good queries}` and grows a semantic layer over
time. Enterprise abstractions are seamed in but toggled off. Evaluated on the
self-built [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) dataset (execution accuracy; cost logged).

## Read in this order

1. [System overview](system-overview.md): what this is, the two harnesses, status.
2. [Architecture](architecture.md): the full design (spine, kernel, services, storage, flow, eval, environments).
3. [Diagrams](diagrams.md): Mermaid architecture, data-flow, and user sequence diagrams.
4. [Design decisions](design-decisions.md): D1-D10 as ADRs, with alternatives and trade-offs.
5. [Asset schemas](asset-schemas.md): the per-asset YAML field spec (Facts / Inference / Audit tiers).
6. [Curator](curator.md): the build-side proposer + adversary loop.
7. [Server](server.md): the serve-side LangGraph flow + guardrails.
8. [Viz](viz.md): the interactive audit + edit cockpit (save → PR).
9. [Glossary](glossary.md): canonical terms.

[External design sources](references.md) that ground the design.

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
