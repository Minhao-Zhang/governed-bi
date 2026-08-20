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
5. [ADRs](adr/) — binding decisions (start with 0005, 0006, then 0014).
6. [Design decisions](design-decisions.md) — short index into the ADRs.
7. [Glossary](glossary.md) — canonical terms.
8. [Open work](open-work.md) — what is unfinished, and the evidence for each.
9. [Enterprise fork](enterprise-fork.md) — what an enterprise deployment must implement for
   PII / RLS / RBAC, in what order, and what this repository deliberately does not do for it.

## Decision records (ADRs)

Point-in-time decisions. **The decision an ADR records is never edited to match later
reality** — a superseding ADR or a code change wins, and the reasoning is kept even when it
was reasoning toward the wrong answer.

What *is* edited is everything around the decision. A superseded ADR is rewritten as a
reversal record: what was decided, what is true instead, and what was learned. It does not
keep coordinates into files that no longer exist, and its Status line does not describe a
build in the present tense after that build was deleted.

| ADR | Title |
|---|---|
| [0001](adr/0001-langgraph-server-chat-runtime.md) | Chat via LangGraph Server + `useStream` — **superseded**; transport still holds |
| [0002](adr/0002-governed-agentic-serve-runtime.md) | Serve runtime as a governed agentic core — **superseded**; governance-as-topology still holds |
| [0003](adr/0003-governed-notes-tri-modal-retrieval.md) | Governed notes and tri-modal retrieval — **reversed in full by 0005** |
| [0004](adr/0004-local-first-conversation-run-logging.md) | Local-first conversation + run logging — **superseded in part by 0014**: the log is deleted and the checkpointer is built |
| [0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) | Memory layer and faceted retrieval |
| [0006](adr/0006-execution-time-governance.md) | Execution-time governance |
| [0007](adr/0007-http-surface-and-the-ui-contract.md) | HTTP surface and the UI contract |
| [0008](adr/0008-identifiers-end-to-end.md) | Identifiers end to end |
| [0009](adr/0009-browsing-and-filtering-api.md) | Browsing, filtering, and relationship API |
| [0010](adr/0010-live-stage-events.md) | Live stage events |
| [0011](adr/0011-two-model-split-and-facet-query-rewriting.md) | Two models and a query per facet |
| [0012](adr/0012-access-seam-principal-and-authorization.md) | The access seam: principal, authorization, and the Layer 6 split |
| [0013](adr/0013-the-declared-abstention-policy.md) | The declared abstention policy |
| [0014](adr/0014-one-conversation-store.md) | One conversation store, on a durable LangGraph checkpointer — **supersedes 0004 §5** |

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
| [Risk coverage on `v4`](analysis/risk-coverage-v4.md) | whether a better operating point exists at all: out-of-fold precision on delivered answers under a coverage constraint, with bootstrapped intervals — arm `v4`, corpus `30872d3`, replicated on `v3-fold` |
| [Selective delivery on `v4`](analysis/selective-delivery-v4.md) | whether any signal the artifact records beats the engine's own operating point: risk-coverage curves, AURC against an oracle and a no-ranking reference, and what each trade costs in right answers — arm `v4`, corpus `30872d3`, replicated across all seven artifacts in `runs/eval/` |
| [Declared machinery with no consumer](analysis/declared-not-consumed.md) | a sweep for the knobs, record fields, state channels and env vars that something declares and nothing reads — six artifacts, 1,351 rows each, corpus `30872d3` |
| [Which questions the published arms ran](analysis/dataset-identity-2026-08-20.md) | the question set behind every published *n*=1,351, and the pre-flight that now refuses a mismatch — closed for the three shipped arms |
| [Open work](open-work.md) | the unfinished items those findings imply, re-verified against the current tree |

## Audits and reviews

Not measurements — readings of the tree. Each names the commit it was taken at, and **line numbers
in all of them have drifted**: resolve a citation by symbol.

| Doc | What it covers |
|---|---|
| [Decisions taken working the 2026-08-10 audit](analysis/decisions-2026-08-10.md) | every call made without asking, with the reasoning and what would reverse it, so a reviewer can disagree with one without re-deriving it |
| [A false ambiguity, and the 25-minute turn](analysis/binding-scope-and-statement-timeout-2026-08-19.md) | CTE-scope false `r_ambiguous_reference` plus `run_query` having no statement timeout, at tree `031b955` |
| [Adopting from the downstream fork 2026-08-19](analysis/adopting-the-downstream-fork-2026-08-19.md) | what was taken from the `governed-bi-utkuai` fork, what was rebuilt because its designs predate ADR 0014, and what was declined — with the measurement showing why a clarification ledger must not live under `corpus_root` |

## Frontend, and external data

- UI: [`ui/`](../ui/) in this repository — running it and its checks are in [usage](usage.md#ui)
- Data: [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) (`../BIRD-Data-Obfuscation`)
