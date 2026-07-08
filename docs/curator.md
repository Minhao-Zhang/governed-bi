# Agentic BI Curator

The build-side agent for the [Agentic BI System](system-overview.md). It is the
offline agent that *produces* the corpus (two-harness split; `deepagents`). Runs
**per-DB, independently**. Writes the corpus defined in
[Asset schemas](asset-schemas.md); the serve-side counterpart is the
[Server](server.md). It is not a one-shot bootstrapper but a **permanent
maintainer**: cold-start plus ongoing drift-repair. Untended corpora rot
~95%→65%/month.

> Implementation: [`src/governed_bi/curator/`](../src/governed_bi/curator/).

## Inputs / outputs

- **Inputs (per DB):** the live DB (catalog + data); that DB's seed queries (`train_final.jsonl`: question + gold SQL + BIRD `evidence`). **Train only, never test (the leakage wall).**
- **Output:** the `corpus/<db>/` tree of YAML typed assets + Markdown skills, each carrying provenance.

## Proposer + adversary (D10)

The curator is **two roles, not one agent:**

- **Proposer:** hypothesizes Inference-tier assets + skills (descriptions, joins, reliability caveats, terms/metrics/rules, routing/gotcha skills), probing the DB to ground each claim.
- **Adversary:** an independent agent that tries to **refute** each proposed Inference/skill asset before it is committed. It re-derives or attacks the claim, runs falsifying probe queries, and checks consistency and evidence. Verdict: accept / revise / reject.

**The adversary boundary = the Facts/Inference boundary.** Facts (dtypes, nullability, uniqueness, samples, row counts) are generated **programmatically** as the deterministic foundation. They are never proposed and never checked. Everything the *model asserts* passes the adversary.

Status lifecycle in each asset's `provenance.status`:

`proposed` (proposer) → `draft` (adversary-passed) → `certified` (human sign-off, **prod only**, D6)

- **Dev (BIRD):** the adversary is the *only* reviewer; auto-accept to `draft` on its pass.
- **Prod (enterprise):** the adversary is the **automated first-line reviewer**. It catches the obvious errors, so the human owner only certifies adversary-passed drafts.

Both the proposer's claim/evidence **and** the adversary's verdict/reasons land in the asset's `audit` block → rendered in the viz/audit surface ("proposed X; adversary challenged with Y; resolved to Z"). This is the auditability payoff of an owner-less, AI-built layer.

## The loop (per DB)

1. **Profile (Facts, programmatic).** Read catalog + sample data → emit the Facts tier for every table/column. Deterministic; no LLM; correct in every arm.
2. **Propose (Inference + skills).** Proposer hypothesizes descriptions, joins (value-overlap + seed-SQL join patterns), reliability caveats (execute-and-observe against the traps), terms/synonyms, metrics/rules (from `evidence` + recurring computations), and authors **routing/gotcha/pattern skills**. Free exploration is confined to this pocket.
3. **Adversary pass.** Each proposed Inference/skill asset is challenged → accept / revise / reject. Survivors → `draft`.
4. **Self-eval & repair (inner loop, capped).** Assemble the draft layer → run the server pipeline on the DB's **train** questions → measure EX → diagnose failures → proposer patches (a failed question often *becomes* the gotcha skill that fixes it) → adversary re-checks the patch → repeat until train-EX plateaus or the iteration/budget cap hits. **Train-only.**
5. **Propose corpus.** CI reference-integrity green ∧ train-EX plateaued → emit (dev auto-accepts; prod opens a PR to the owner, D6).

**Done-enough criterion:** `CI green ∧ (train-EX plateaued ∨ cap)`.

## Reliability inference (Phase 2 detail)

The curator flags an unreliable column via **general data-quality anomalies, not BIRD-trap-specific detectors** (P2, so it transfers to an enterprise deployment; BIRD's traps merely validate that the signals fire). Each signal contributes to a confidence score. A column is marked `suspect` only above a threshold, and the adversary independently tries to refute each caveat before it commits.

| Signal | Generic form | Catches (BIRD trap) |
|---|---|---|
| **Referential-integrity break** | claims to be a key, doesn't join cleanly | permuted join keys |
| **Sibling inconsistency** | near-synonym column disagrees with its twin | sparse-perturb / cat-remap / date-offset |
| **Orphan duplicate table** | duplicates another table, no inbound FK, unused | clone tables |
| **Distributional implausibility** | values wrong for the apparent meaning | sparse-perturb / null |
| **Usage corroboration** (weak, never standalone) | unused while a near-synonym twin is used | (strengthens the above) |

**False-positive guards:** a confidence threshold; the adversary refutes ("unreliable, or just rare / legitimately different?"); flag only when a clear real alternative (the used twin) exists; in the enterprise setting a false positive only degrades the stamp, it never blocks (server env-toggle). **Usage (#5) is corroborating-only.** Never flag on "unused" alone (rare ≠ fake, and it wouldn't transfer). **Grading (BIRD):** decoy-recall + false-positive rate, both from the manifest.

## Distillation discipline (curation beats accumulation)

The curator *selects and distills*; it never dumps. That is the memory doc's central law (raw grep <1pt; Spotify accepted 12.5%; more memory can hurt).

- **Few-shots:** a **per-pattern cap**. Cover query-pattern classes and the complexity spread, dedup near-identical examples, and keep the clearest exemplar per pattern. Not the whole train split.
- **Skills:** the highest-value output and the hardest. Distilled routing/gotchas, not transcripts. Maintained continuously.

## Maintenance (permanent maintainer)

Cold-start is the first job; drift-repair is ongoing. Serve-side signals (corrections, failures) are harvested back into proposer input. A correction ≈ a PR to a skill/reference doc, so the memory/corpus distinction collapses (D8).

Links: [Design decisions](design-decisions.md) · [Asset schemas](asset-schemas.md) · [Architecture](architecture.md) §2 · *Data Agent Memory Design Overview*.
