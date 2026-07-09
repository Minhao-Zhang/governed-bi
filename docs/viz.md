# Agentic BI Viz

_[English](viz.md) · [简体中文](viz.zh.md)_

The audit **cockpit** for the [Agentic BI System](system-overview.md) corpus.
This repo ships a **read-only** cockpit: browse and audit the AI-built layer, and
ask a question to see the governed answer plus its reliability stamp.

**Editing the corpus and opening PRs is intentionally not built here.** Because
git is the source of truth (D9), a correction is "edit a file + PR", which is
served by generic git/PR tooling plus CI (dev), or by the enterprise application
(prod). This repo owns the write *primitives* such an editor reuses; it does not
own the interactive editor or the PR orchestration.

> Implementation: [`src/governed_bi/viz/`](../src/governed_bi/viz/) (read-only).
> `presenter.py` holds UI-agnostic view models; `app.py` is the Streamlit
> renderer, the only UI-specific module (optional `viz` extra).

## Scope: engine vs product

The cockpit's *reading* is engine-adjacent: a dev / audit / showcase tool over
the corpus. The cockpit's *editing* is product surface: an interactive form plus
a git/PR workflow that embeds this engine. Keeping editing out avoids baking a UI
framework and git/PR orchestration into the library, and matches the two-product
split (a generic public engine; a private enterprise fork where owner + PR + CI
review actually lives).

| Concern | Where it lives |
|---|---|
| Asset schema (for schema-driven forms) | this repo (`corpus/schemas`) |
| Serialize edits back to YAML | this repo (`corpus/serialize.write_corpus`) |
| Validate on the PR (the CI gate) | this repo (`corpus/validate` + CLI) |
| Read-only cockpit (health / tables / assets / skills / ask) | this repo (`viz/`) |
| Interactive edit form + git/PR orchestration | downstream tooling / enterprise app |

## The write path (downstream)

Editing is deliberately just "edit a file + PR", reusing the existing git
mechanism rather than a bespoke store:

1. A human edits the corpus YAML/MD, in any editor or a downstream
   form-over-schema tool that serializes with `write_corpus`.
2. Commit and open a PR (git / GitHub / `gh`).
   - **Dev/BIRD:** the developer is the reviewer, so this can commit directly.
   - **Prod/enterprise:** real owner + PR + CI (D6). The adversary has already
     pre-filtered, so the human only certifies draft-quality assets.
3. CI runs reference-integrity (`governed-bi validate ...`).

This is the same mechanism as the correction loop (D8; the memory/corpus
distinction collapses). A serve-side correction can pre-populate a draft edit for
a human to confirm, again in whatever tool owns editing. One small corpus-level
helper could reasonably live here if a downstream editor wants it (a `certify`
that appends a `source: human` provenance entry and flips `draft -> certified`);
it is not implemented today.

## Editability model = the tier model (the contract editors honor)

Whatever builds the editor honors the tier contract this repo defines and
validates:

| Tier | Editing rule |
|---|---|
| **Facts** | **read-only**: catalog truth; a human never edits dtypes/samples |
| **Inference** | **editable**: the human corrects the curator (description, role, references, reliability, confidence) |
| **Audit** | system-written; a human edit appends a provenance entry (`source: human`, who, when, reason) |
| **Governance** | **human-only**: this is where `governance.excluded` is set |

Editing an asset flips its status `draft -> certified` (the certifying act, D6),
and the audit trail becomes **three-party: proposer -> adversary -> human**.

## Views

Built here (read-only), computed from the corpus:

- **Corpus health** (home). Asset counts, CI status, and the flags a reviewer
  triages first: # suspect columns, # excluded assets, # low-confidence joins.
- **Table view**. Facts + Inference side by side; `suspect` and `excluded`
  columns flagged with their reason; per-column provenance status.
- **Assets**. The non-table assets (joins, metrics, terms, rules, few-shots,
  negatives), filterable by type, with provenance status.
- **Skills**. Rendered markdown.
- **Ask**. Runs the server flow and shows the tier, SQL, answer, and the
  guardrail/plan trace (the reliability stamp).

Design vision, not built here (a fuller cockpit, or the downstream product):

- **FK graph** (join projection, edges styled by confidence).
- **Gold-diff** (BIRD: curator vs gold per asset).
- **Search** (BM25 plus optional semantic search).
- The **editable** forms and the **save -> PR** button (see the write path above).

## Simple by design

A local read-only app computed from the corpus: UI-agnostic view models
(`presenter`) plus a thin Streamlit renderer (`app`), so the frontend is
swappable and carries no logic of its own. No SaaS, no multi-tenant, no in-repo
editing or PR orchestration.

Links: [Design decisions](design-decisions.md) (D6 ownership, D9 corpus contract,
D10 curator), [Asset schemas](asset-schemas.md), [Curator](curator.md),
[Server](server.md).
