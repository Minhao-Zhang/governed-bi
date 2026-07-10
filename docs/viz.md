# Agentic BI Viz

_[English](viz.md) · [简体中文](viz.zh.md)_

The **read-only audit surface** for the [Agentic BI System](system-overview.md)
corpus. This repo ships **no bundled UI**; instead it ships the pieces a UI reads
from — the `presenter` view models (UI-agnostic) and the `governed_bi.api`
HTTP/JSON API — so you can browse and audit the AI-built layer, and ask a question
to see the governed answer plus its reliability stamp. The interactive UI is a
separate project (see [ui-frontend-design.md](ui-frontend-design.md)).

**Editing the corpus and opening PRs is intentionally not built here.** Because
git is the source of truth (D9), a correction is "edit a file + PR", which is
served by generic git/PR tooling plus CI (dev), or by the enterprise application
(prod). This repo owns the write *primitives* such an editor reuses; it does not
own the interactive editor or the PR orchestration.

> Implementation: [`src/governed_bi/viz/`](../src/governed_bi/viz/) (read-only).
> `presenter.py` holds UI-agnostic view models; [`governed_bi.api`](../src/governed_bi/api/)
> (optional `api` extra) serves them over HTTP/JSON. Run it with
> `uv run --extra api uvicorn --factory governed_bi.api:create_app` (interactive
> docs at http://localhost:8000/docs).

## Scope: engine vs product

The audit surface's *reading* is engine-adjacent: a dev / audit / showcase tool over
the corpus. Its *editing* is product surface: an interactive form plus
a git/PR workflow that embeds this engine. Keeping editing out avoids baking a UI
framework and git/PR orchestration into the library, and matches the two-product
split (a generic public engine; a private enterprise fork where owner + PR + CI
review actually lives).

| Concern | Where it lives |
|---|---|
| Asset schema (for schema-driven forms) | this repo (`corpus/schemas`) |
| Serialize edits back to YAML | this repo (`corpus/serialize.write_corpus`) |
| Validate on the PR (the CI gate) | this repo (`corpus/validate` + CLI) |
| Read-only audit surface (health / tables / assets / skills / ask) | this repo (`viz/presenter` + `governed_bi.api`) |
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

Built here (read-only) as `presenter` view models, computed from the corpus and
served over HTTP/JSON by `governed_bi.api` for a separate UI to render:

- **Chat**. A multi-turn conversation over the governed server flow (served at
  `POST /chat`); each answer shows the two-axis stamp, the SQL, and the provenance
  trace, and follow-ups are fed back through working memory (D8).
- **Corpus health**. Asset counts, CI status, and the flags a reviewer
  triages first: # suspect columns, # excluded assets, # low-confidence joins.
- **Table view**. Facts + Inference side by side; `suspect` and `excluded`
  columns flagged with their reason; per-column provenance status.
- **Assets**. The non-table assets (joins, metrics, terms, rules, few-shots,
  negatives), filterable by type, with provenance status.
- **Skills**. Rendered markdown.

Design vision, not built here (a fuller audit surface, or the downstream product):

- **FK graph** (join projection, edges styled by confidence).
- **Gold-diff** (BIRD: curator vs gold per asset).
- **Search** (BM25 plus optional semantic search).
- The **editable** forms and the **save -> PR** button (see the write path above).

## Simple by design

A read-only surface computed from the corpus: UI-agnostic view models
(`presenter`) served over HTTP/JSON by `governed_bi.api`, so the frontend is
swappable and carries no logic of its own. No SaaS, no multi-tenant, no in-repo
editing or PR orchestration.

Links: [Design decisions](design-decisions.md) (D6 ownership, D9 corpus contract,
D10 curator), [Asset schemas](asset-schemas.md), [Curator](curator.md),
[Server](server.md).
