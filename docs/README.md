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
9. [Enterprise fork](enterprise-fork.md) — what an enterprise deployment must implement for
   PII / RLS / RBAC, in what order, and what this repository deliberately does not do for it.
10. [UtkuAI fork handoff](utkuai-fork-handoff.md) — **start here for the fork.** Why it exists,
    its spec, which files are its own and which upstream ones it touched, the five seams where it
    attaches, the one architectural debt it carries, and the two decisions that are upstream's.
11. [Claim audit, 2026-08-18](handoff-claim-audit-2026-08-18.md) — the fork's own claims checked
    against a running engine. Read it beside the handoff: it is where the handoff's "honestly"
    section gets its evidence, and it names the one claim that is wired and empty.

## Decision records (ADRs)

Point-in-time decisions. **The decision an ADR records is never edited to match later
reality** — a superseding ADR or a code change wins, and the reasoning is kept even when it
was reasoning toward the wrong answer.

What *is* edited is everything around the decision. A superseded ADR is rewritten as a
reversal record: what was decided, what is true instead, and what was learned. It does not
keep coordinates into files that no longer exist, and its Status line does not describe a
build in the present tense after that build was deleted. ADR 0003 and ADR 0004 were rewritten
that way on 2026-08-12, having drifted into 121 citations of v1 modules between them.

| ADR | Title |
|---|---|
| [0001](adr/0001-langgraph-server-chat-runtime.md) | Chat via LangGraph Server + `useStream` |
| [0002](adr/0002-governed-agentic-serve-runtime.md) | Serve runtime as a governed agentic core |
| [0003](adr/0003-governed-notes-tri-modal-retrieval.md) | Governed notes and tri-modal retrieval — **reversed in full by 0005** |
| [0004](adr/0004-local-first-conversation-run-logging.md) | Local-first conversation + run logging — the turn log; the durable checkpointer half is withdrawn |
| [0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) | Memory layer and faceted retrieval |
| [0006](adr/0006-execution-time-governance.md) | Execution-time governance |
| [0007](adr/0007-http-surface-and-the-ui-contract.md) | HTTP surface and the UI contract |
| [0008](adr/0008-identifiers-end-to-end.md) | Identifiers end to end |
| [0009](adr/0009-browsing-and-filtering-api.md) | Browsing, filtering, and relationship API |
| [0010](adr/0010-live-stage-events.md) | Live stage events |
| [0011](adr/0011-two-model-split-and-facet-query-rewriting.md) | Two models and a query per facet |
| [0012](adr/0012-access-seam-principal-and-authorization.md) | The access seam: principal, authorization, and the Layer 6 split |
| [0013](adr/0013-the-declared-abstention-policy.md) | The declared abstention policy |

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
| [Open work](open-work.md) | the unfinished items those findings imply, re-verified against the current tree |
| [Strategy checkpoint 2026-08-11](analysis/strategy-checkpoint-2026-08-11.md) | temporary checkpoint for the repository as a portfolio artifact: what is true, what it declines to be, and the work queue — not an ADR; replace when superseded |

## Audits and reviews

Not measurements — readings of the tree. Each names the commit it was taken at, and **line numbers
in all of them have drifted**: resolve a citation by symbol.

| Doc | What it covers |
|---|---|
| [Implementation audit 2026-08-10](analysis/audit-2026-08-10.md) | findings and remediation plan at tree `c625da8`, including the memory-profile rows — read P3 before trusting any diagnosis in them |
| [Decisions taken working that audit](analysis/decisions-2026-08-10.md) | every call made without asking, with the reasoning and what would reverse it, so a reviewer can disagree with one without re-deriving it |
| [Architecture review 2026-08-11](analysis/architecture-review-2026-08-11.md) | ten deepening candidates across `serve/`, `eval/`, `api/` and the `ui/` seam, at tree `506ad9b`, each marked verified-here or reported |
| [Hand-parsed model replies](analysis/parsed-model-output.md) | every place `src/` parses a model reply by hand, and the two fail-open defects that found — both fixed at `95e3b07` |

## Frontend, and external data

- UI: [`ui/`](../ui/) in this repository — running it and its checks are in [usage](usage.md#ui)
- Data: [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) (`../BIRD-Data-Obfuscation`)
