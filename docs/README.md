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
9. [Return path](return-path.md) — how reader and engineer feedback becomes a corpus change
   ([ADR 0015](adr/0015-the-return-path.md), **steps 0–6 built and on `main`**; the
   agentic pipeline, T4/T5, the categorised capture surface and `/reports` are not). Both pages open with a note on
   where the design and what shipped differ, and the evidence is in
   [open work](open-work.md) §3.10a–3.10c.

   The tools, in the order a change moves through them:

   | | |
   |---|---|
   | `tools/import_eval_failures.py` | an eval artifact's failures become observations. **Not the only row source, and not the only one with a caller:** `POST /turns/{id}/raised` ships mounted and unauthenticated, and `ui/components/answer/raise-note.tsx` — which predates this branch — renders on the answer card and calls it. What the design called the capture UI is the *richer* surface that is absent: a category picker, an `expected` field, `/reports` |
   | `tools/verify_patch.py` | the free ladder, T0–T2. Delta gates over the patched tree, in memory |
   | `tools/reproduce_observation.py` | T3: does this question still miss a gold table? Free, and **run it with `--embed`**. `--state open` asks the whole queue in one pass — measured 2026-08-24: **52 of the open queue no longer reproduced**, in 72 seconds, because the *engine* has moved since the artifact was measured. Not the corpus: [open work](open-work.md) §1.5 shows the corpus assets byte-identical across the digest change. `--decline` moves those to `declined`/`cannot_reproduce`. Exit **2** means it could not run; **1** means something still reproduces |
   | `tools/export_bundle.py` | a bundle an engineer applies with `git apply`. Two content checks are fatal here and nowhere else |
   | `tools/check_landed.py` | did it land? Read off the corpus, stored nowhere |
   | `tools/check_ratchet.py` | the corpus's conformance debt may shrink and may not grow |
10. [Enterprise fork](enterprise-fork.md) — what an enterprise deployment must implement for
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
| [0015](adr/0015-the-return-path.md) | The return path: reader feedback into the corpus — **Accepted; steps 0–6 built** (the agentic pipeline, T4/T5, the categorised capture surface and `/reports` are not; a minimal note control does ship) |
| [0016](adr/0016-gating-the-corpus-repository.md) | Gating the served corpus: "did the corpus add a finding since somebody last looked?" — **Accepted; the nightly is now on `main`** (a job **here**, not in the corpus: the rules are statements about this engine, so the consumer runs the check. `ci.yml`'s `corpus` job, `schedule` plus `workflow_dispatch`. Baseline is a corpus SHA in `tools/corpus_baseline.py`; bumping it is the acknowledgement. Answers 0015's open "who owns the corpus repository's CI" — nobody, by design. What is still open is that the baseline equals the corpus tip, so the first green run proves nothing: [open work](open-work.md) §3.10e) |

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

## Frontend, and external data

- UI: [`ui/`](../ui/) in this repository — running it and its checks are in [usage](usage.md#ui)
- Data: [BIRD-Obfuscation](https://github.com/Minhao-Zhang/BIRD-Obfuscation) (`../BIRD-Data-Obfuscation`)
