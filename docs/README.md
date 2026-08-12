# governed-bi design

Design for an agentic BI system: natural-language questions → grounded,
governed, auditable answers over relational data. Postgres is the live path;
SQLite is the offline test/CI substrate.

## Read in this order

1. [Usage](usage.md) — install, env, serve.
2. [Architecture](architecture.md) — serve spine and package map.
3. [Measurement](measurement.md) — how to run an evaluation arm, what a
   measurement row carries, and what makes a number quotable.
4. [Corpus format](corpus-format.md) — where the corpus is, its layout and field
   tiers.
5. [ADRs](adr/) — binding decisions (start with 0005 and 0006).
6. [Design decisions](design-decisions.md) — short index into the ADRs.
7. [Glossary](glossary.md) — canonical terms.
8. [Open work](open-work.md) — what is unfinished, and the evidence for each.

## Decision records (ADRs)

Point-in-time decisions. An ADR is never edited to match later reality — a
superseding ADR or a code change wins. Living how-to docs may drift; ADRs do not.

| ADR | Title |
|---|---|
| [0001](adr/0001-langgraph-server-chat-runtime.md) | Chat via LangGraph Server + `useStream` |
| [0002](adr/0002-governed-agentic-serve-runtime.md) | Serve runtime as a governed agentic core |
| [0003](adr/0003-governed-notes-tri-modal-retrieval.md) | Governed notes and tri-modal retrieval |
| [0004](adr/0004-local-first-conversation-run-logging.md) | Local-first conversation + run logging |
| [0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) | Memory layer and faceted retrieval |
| [0006](adr/0006-execution-time-governance.md) | Execution-time governance |
| [0007](adr/0007-http-surface-and-the-ui-contract.md) | HTTP surface and the UI contract |
| [0008](adr/0008-identifiers-end-to-end.md) | Identifiers end to end |
| [0009](adr/0009-browsing-and-filtering-api.md) | Browsing, filtering, and relationship API |
| [0010](adr/0010-live-stage-events.md) | Live stage events |
| [0011](adr/0011-two-model-split-and-facet-query-rewriting.md) | Two models and a query per facet |

## Measurement findings

A measurement is only true of the tree and the corpus it was taken on, so every
finding below names both. **One document per question, replaced rather than
appended to**:
an analysis that has been superseded is deleted, because a page carrying both the
old reading and the correction is a page a reader has to date-check line by line.
Git history is the record of what changed.

| Doc | What it covers |
|---|---|
| [Measurement](measurement.md) | how to produce a finding: the eval driver's flags, the prompt registry, the measurement row schema, and the quotability gates |
| [Failure modes](failure-modes.md) | how the engine answers wrongly, per failure class, with the causal repair experiments — arm `v4`, engine `3c0079a`, corpus `30872d3` |
| [Open work](open-work.md) | the unfinished items those findings imply, re-verified against the current tree |
| [Strategy checkpoint 2026-08-11](analysis/strategy-checkpoint-2026-08-11.md) | temporary checkpoint for the repository as a portfolio artifact: what is true, what it declines to be, and the work queue — not an ADR; replace when superseded |

## Architecture reviews

Not measurements — readings of the tree, looking for modules whose interface is nearly as wide as
their implementation. Line numbers are anchored to the commit named in each document and drift.

| Doc | What it covers |
|---|---|
| [Architecture review 2026-08-11](analysis/architecture-review-2026-08-11.md) | ten deepening candidates across `serve/`, `eval/`, `api/` and the `ui/` seam, at tree `506ad9b`, each marked verified-here or reported |

## Frontend, and external data

- UI: [`ui/`](../ui/) in this repository — running it and its checks are in [usage](usage.md#ui)
- Data: [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) (`../BIRD-Data-Obfuscation`)
