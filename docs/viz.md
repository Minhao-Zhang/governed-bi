# Agentic BI Viz

The audit and edit **cockpit** for the [Agentic BI System](system-overview.md)
corpus. It is a simple interactive local app (not a static site) that lets
people audit the AI-built layer, correct it, then save to disk and open a PR. It
is the operational front-end of the D6 human gate, and the place where the
correction loop (D8) happens. It reads and writes the git corpus defined in
[Asset schemas](asset-schemas.md).

> Implementation: [`src/governed_bi/viz/`](../src/governed_bi/viz/).

## Why interactive, not static

The corpus is authored by the curator, with no human owner. The human's job is to review and correct it, not just look at it. A viewer isn't enough: the cockpit must let a reviewer edit an asset and push it through git. Editing the git YAML/MD **is** editing the source of truth (D9), so the viz is a git-editing front-end rather than a derived store. (A read-only snapshot can still be exported statically for a public showcase.)

## Editability model = the tier model

| Tier | In the cockpit |
|---|---|
| **Facts** | **read-only**: catalog truth; a human never edits dtypes/samples |
| **Inference** | **editable**: the human corrects the curator (description, role, references, reliability, confidence) |
| **Audit** | system-written; a human edit appends a provenance entry (`source: human`, who, when, reason) |
| **Governance** | **human-only**: this is where `governance.excluded` is set |

Editing an asset flips its status `draft → certified` (the certifying act, D6), and the audit trail becomes **three-party: proposer → adversary → human**.

## Views

- **Corpus health** (home). Per-DB: asset counts, CI status, curator self-eval train-EX, # suspect / # excluded / # low-confidence joins. Answers where the layer needs attention.
- **Table view**. Facts + Inference side by side; `suspect` = red banner, `excluded` = struck/greyed + reason; per-column provenance. Inference and governance are editable here.
- **FK graph**. Join projection; edges styled by confidence, low-confidence ones flagged. Click an edge to inspect or correct the join.
- **Audit trail** (centerpiece). Proposer claim + evidence → adversary verdict → human override, each with a status. The reviewer inspects the exchange between proposer and adversary, then intervenes.
- **Gold-diff** (BIRD, read-only). Curator vs gold per asset: FK ✅/❌/missing, decoy-recall, description match.
- **Skills**. Rendered markdown, with referenced assets linked. Editable.
- **Search**. BM25 plus optional semantic search over assets and skills.

## Save → PR (the D6 gate, operationalized)

1. Human edits assets / skills / governance in the cockpit.
2. **Save** → serializes changes back to the exact corpus YAML/MD files on a working branch.
3. **Open PR** → commits + opens a PR; CI runs reference-integrity.
   - **Dev/BIRD:** the developer is the reviewer, so save can commit directly (no gatekeeper).
   - **Prod/enterprise:** real owner + PR + CI (D6). The adversary has already pre-filtered, so the human only certifies draft-quality assets.

A correction here means "edit a file + PR", the same mechanism as the correction loop (D8; the memory/corpus distinction collapses). Serve-side corrections can pre-populate a draft edit for the human to confirm.

## Simple by design

Local app, **schema-driven forms** (it knows the asset schemas), git integration for save/PR. No SaaS, no multi-tenant. Read-only views (gold-diff, eval, health) computed from the corpus; editable views write back to git.

Links: [Design decisions](design-decisions.md) (D6 ownership · D9 corpus contract · D10 curator) · [Asset schemas](asset-schemas.md) · [Curator](curator.md) · [Server](server.md).
