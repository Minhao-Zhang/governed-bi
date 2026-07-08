# Agentic BI System

> **What this is**
>
> Design for an agentic BI / Generative-BI system: natural-language questions →
> grounded, governed, auditable answers over enterprise relational data.
> Near-term = a **general, DB-agnostic showcase** (personal GitHub) that
> cold-starts from `{a DB connection + a few known-good queries}` and grows the
> semantic layer over time. Enterprise abstractions are seamed in but toggled
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
    - [Viz](viz.md): the interactive audit + edit cockpit (save → PR)
    - [Glossary](glossary.md): canonical terms
- Grounded in the [external design sources](references.md).

## Status

> **Decided (D1-D10)**
>
> target · governed unit · eval · grading · refusal · ownership · identity ·
> memory · corpus contract · curator gate. See [Design decisions](design-decisions.md).

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
