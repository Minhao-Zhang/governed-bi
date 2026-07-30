# Open work

The single tracker for work that is **open**. It replaces four dated trackers
(`engineering-gaps-2026-07-16`, `eval-audit-backlog-2026-07-22`,
`clarification-sme-benchmark-build-plan`, `implementation-plan-notes-and-run-logging`),
whose closed items now live only in git history. Nothing here is a design
record — decisions belong in [design-decisions.md](design-decisions.md) and
[adr/](adr/).

**The eval rebuild has its own tracker.** All prior BIRD numbers are discarded, and
the four fixes that follow (notes without triggers, the contradictory SME prompt, the
gold-SQL-derived decoy mask, routing-failure attribution) are tracked in
[plans/eval-rebuild.md](plans/eval-rebuild.md), not here. Items below that the rebuild
supersedes are marked where they occur.

## Correctness

| # | What | Where |
| --- | --- | --- |
| C3 | `ex_strict` is unguarded: `validate_gold_hashes_live` hashes only the lenient normaliser and compares it to `gold.hash_lenient`. `hash_normalised_result_strict` is never checked against `gold.hash_strict` before a run trusts `ex_strict`. | `eval/hash_grade.py` |
| C9 | Pooled `_validate_corpora(corpora)` is called with no connector, so nothing checks asset references against the live catalog at scale. | `eval/run_datalake.py:4122` → `eval/harness.py` |
| G8 | The grader self-check was only ever validated on a 5-row sample. A full head-to-head needs the live DB. | `eval/hash_grade.py` |

## Efficiency

| # | What | Where |
| --- | --- | --- |
| E1 | Cross-check re-executes gold **and** prediction per item per arm, though gold is arm-invariant. Memoise the gold hash per `question_id`. | `eval/run_experiment.py` → `eval/ex.py` |
| E2 | Each corpus is loaded from disk twice — once for the solver, once by `_suspect_from_corpus`. Both drivers. | `eval/harness.py` |
| E3 | `profile_database` runs twice per db (baseline and curated each profile independently). | `curator/pipeline.py` |
| E4 | Baseline is rebuilt unconditionally on `--resume-curated`; `run_datalake` already guards with `_has_yaml`. | `eval/run_experiment.py` |
| E5 | The gold self-check opens a fresh schema-pinned connector per sampled db, separate from the shared unpinned serve connector. | `eval/run_datalake.py` |

## Experiment design

The instrument is sound; the design does not yet isolate the claim. In priority
order.

**Before adding arms, read the conditional diagnostics.** X1 and X2 exist to
isolate *which part of the corpus does the work*, and that needs new arms. But six
within-arm conditional blocks now give a partial answer for free, on rows you
already have — stamp calibration, decoy-touch with vs without a caveat, EX with vs
without a note, EX after a repair, and a ceiling on guardrail-induced loss. See
[Eval metrics](eval-metrics.md#conditional-diagnostics--which-part-of-the-governance-is-doing-the-work).

These splits are a **prioritisation signal only**. Every one of them conditions on
a system output, not on a randomised assignment: `decoy_touch_by_caveat` splits
rows by whether the corpus happened to inject a caveat, which is confounded with
whatever made that column suspect in the first place. So the split can tell you
which ablation to run *first*, and it can never stand in for one or cancel one. A
null caveat split is not an answer to X2; X2 still has to be run.

| # | What | Why it blocks a claim |
| --- | --- | --- |
| X1 | **No length-matched placebo arm.** Every rung is a strict content superset, so "later rung = more tokens" is guaranteed by construction. Serve schema *Y*'s corpus against schema *X*'s questions, byte-matched on `context_chars`. | Without it, every curated-arm result is confounded with prompt length. |
| X2 | ~~**`mask_only` ablation.**~~ **Moot 2026-07-29.** The ablation existed to isolate a deterministic decoy mask that flagged every column train gold never touched, since a decoy-touch result driven by it would have been mechanical rather than evidence about metadata. That mask is deleted ([plans/eval-rebuild.md](plans/eval-rebuild.md) B6): "BIRD never queried this column" is not evidence the column is unreliable, and defective gold SQL made it actively wrong. Reliability is now authored by the curator agent, so there is no mechanical arm left to isolate. What replaces it is measuring whether the agent marks decoys at all. | — |
| X3 | ~~**`refute()` raises `NotImplementedError`.**~~ **Resolved by deletion (2026-07-29).** It had zero callers (`grep -rn "refute(" src/ tests/` matched only its own definition), so it was never the `curated` rung's adversary — the rung's adversary has always been the structural linter plus two confidence penalties. Deleted rather than implemented or left as an aspirational stub; docs now describe the `curated` rung as what it is. | — |
| X4 | **Single seed everywhere.** Needs ≥3 curator draws plus a serve replicate to separate build variance from serve variance. | The largest live run is n=52 and the `curated`/`sme` sign flips between consecutive runs. |
| X5 | **The 69-schema scale run** (8,134 train / 2,030 test) has only ever run with `--skip-agent`. | No result exists at the scale the design targets. |
| X6 | **The refuse-gate is unexercised, and its only negative set does not survive pooling.** BIRD questions are all answerable and `NegativeExampleAsset` is never generated (0 files across every generated corpus). The only measurement that ever existed — `refusal_accuracy`, dropped from `run_experiment` in `9953b26` — drew its negatives from *other* `db_id`s, which `load_cross_db_unanswerable` documents as "unanswerable **for `db_id`**". That holds only because the single-schema driver pins the corpus to one schema. In a pooled run every other schema is in the pool, so those questions are **answerable**, and the metric would have scored every correct answer as a refuse-gate failure. A genuinely out-of-scope negative set (the shape of `dataset.BEER_FACTORY_UNANSWERABLE`, which is 3 hand-written questions) is what the pooled driver would need. | `false_refusal_rate` and `refusal_accuracy` are both unmeasured at scale; refusal is indistinguishable from failure. |
| X7 | **`curated_sme` bundles two mechanisms with no arm that separates them.** `STEP_MECHANISMS["curated_sme"]` (`eval/arms.py`) declares both "clarification protocol" and "BIRD human column documentation (SME brief)"; `curated_sme_blind` was removed in `c524513` as meaningless, because it built the brief from inputs Phase A already had. Splitting the confound needs a knowledge source the curator lacks and a human does not simulate — not another arm over the same inputs. Not covered by X1–X6. | The `curated_sme` delta can never be attributed to the clarification protocol, which is the headline claim. Permanent until a real external knowledge source exists. |
| X8 | **No confidence intervals anywhere.** `analysis.py` / `power.py` publish `p_value` and `p_value_holm`; nothing computes an interval on any rate or delta. | A significance verdict with no interval hides effect size, so a barely-resolvable delta reads the same as a large one. |
| X9 | **`--replicate` defaults to `None`** (`run_datalake` arg parser), so the noise-floor / MDE arm is absent unless an operator asks for it — while p-values print regardless. | The default run reports significance it cannot bound: no floor, no MDE. The `claim_ready` gate catches this, but only after the spend. |
| X10 | **The Holm family covers fair-ladder pairs only.** `analysis.py` adjusts pairs where `k in on_ladder`; the six conditional-diagnostic blocks (each a multi-level contrast) carry no p-value, no interval and no adjustment. | Reading a conditional split as a result is an unadjusted comparison outside the declared family. |
| X11 | **Two conflicting declared headlines.** `metrics.py` labels `ex_lenient` "headline execution accuracy" and `ex_no_twin` "EX with no train twin — the defensible headline". Nothing pre-registers which one is *the* number. | Two candidate headlines with no prior commitment is the shape of post-hoc selection. |

X8–X11 are reporting-side and cost no extra serve pass. Run-to-run variance is X4,
not a separate item.

## Corpus coverage

The asset schema is far richer than anything the curator produces. Either
generate the fields or delete them from `corpus/schemas.py`:

- `TermRelation` / `relation` — **0** occurrences across every generated corpus.
- `ColumnRole` — set on 76 of ~4,245 generated table assets.
- `normative_force` — only ever `advisory`; `must_honour` is never emitted.
- `activation` — only ever `always`; `on_match` is never emitted, so ADR 0003's
  trigger-pinned (PIN) retrieval mode has no data exercising it.
- `NegativeExampleAsset` — never generated (see X6).

## The two eval drivers, and what blocks collapsing them

`run_datalake.py` (4,917 lines) is a fork of `run_experiment.py` (1,011 lines).
The structural difference is one thing — the serve connector is pinned to
`schema=db_id` in the single-schema driver and `schema=None` in the pooled one.
Single-schema is the pooled case at n=1. Collapsing them deletes `run_experiment.py`
and `tests/test_run_experiment_parity.py`: roughly 1,400 lines.

**Done (2026-07-28):** the structural blockers are closed; only the tests remain.

- The manifest is no longer forked. Both modes build through
  `metrics.build_manifest`, which closed a real hole — see
  [Eval metrics](eval-metrics.md).
- Neither driver reaches into the other's privates any more (`ee3d9cf`). The ten
  shared helpers live in `eval/harness.py` (313 lines) and both drivers import
  from there. `curator/pipeline.py` does **not** import `_sme_fold_signal` — it
  only names it in a comment.

### Two metrics dropped on the record (decided 2026-07-28)

Both were single-schema-driver-only, and both blocked the collapse. Dropping them
is a **loss of measurement**, recorded here so nobody later reads their absence as
"never existed".

- **`refusal_accuracy`** — scored against a cross-DB negative set, whose validity
  rests on the corpus being pinned to one schema (see X6). Dropped rather than
  ported, because ported unchanged it would invert. **The scorer survives**:
  `eval.refuse_gate.eval_refuse_gate` + `agent_refuser`, exercised in
  `tests/test_eval.py` against `BEER_FACTORY_UNANSWERABLE` — the genuinely
  out-of-scope shape. What is missing is that set at scale, not the machinery.
- **`ex_crosscheck_agree_rate`** — the only check that hash grading agrees with
  set-equality re-execution of gold. Nothing gated on it. Consequence: **hash
  grading now has no independent cross-verification at all**, which compounds C3
  (the strict normaliser is never self-checked). `eval.ex.execution_match` itself
  is untouched and still tested.

### What is left to reach one file

Only the tests hold it up now. `run_experiment.py` is imported by **5 test files** —
`test_eval_concurrency`, `test_eval_index`, `test_eval_metrics`,
`test_prompt_attribution`, `test_run_experiment_parity` — and by no `src/` module.

1. Rewire the 5 test files. Three need structural rewrites, not import edits:
   `test_eval_concurrency`, `test_prompt_attribution` and
   `test_run_experiment_parity` drive `_run_arm_generations` (256 lines), the
   single-schema arm loop. `test_run_experiment_parity`'s entire purpose — the two
   drivers agree — dissolves when there is one driver. The other two need one-line
   redirects (`test_eval_metrics` imports the `build_manifest` wrapper, which
   already delegates to `metrics.build_manifest`).
2. Confirm `--resume-curated` is subsumed by the pooled staging/promotion resume,
   then delete `run_experiment.py`.
3. Rename the survivor: `run_datalake` is the wrong name for the only driver.
4. Fix the three stale docstrings that still point at it as the live harness:
   `gateway/__init__.py`, `gateway/connectors/__init__.py`,
   `gateway/connectors/base.py`.

The register in `metrics.py` is the contract that makes step 1 checkable — it is
why the merge is now a mechanical job rather than a risky one.

## Test debt blocked on the eval driver

Twenty tests across eleven files assert on implementation **source text** via
`inspect.getsource` (`test_ladder_design` 4, `test_run_experiment_parity` 3,
`test_build_isolation` / `test_hash_grade` / `test_oracle_and_probes` /
`test_retrieval_index_cache` 2 each, `test_curator_seed_joins`,
`test_datalake_routing`, `test_eval_index`, `test_eval_metrics`,
`test_middleware_guardrail` 1 each). A reformat breaks them and an equivalent
rewrite defeats them.

They are **not** dead weight, and they should not be deleted as they stand. Each
pins a call-site or ordering invariant in `run_datalake()` — an 820-line function
that needs live Postgres, a model and about an hour to drive — and most say so in
their own docstring. Two examples of what they hold: the gold pre-flight must run
*before* the build phase, or a bad DSN costs a full curator pass over every
schema; the replicate must be appended *last* in `serve_order`, or the noise
floor it measures is a within-moment figure rather than one that spans an arm's
serve.

The fix is not to delete the tests, it is to make the driver drivable. Once the
two eval drivers are unified behind a testable `grade_one` / `run_arm` seam,
these become ordinary behavioural tests. `tests/test_eval_index.py` (the
`manifest_model` rewrite) is the worked precedent for the conversion.

## Governance gaps

- A simulated SME's answer defaults to `status=certified` (`corpus/clarify.py`),
  and `pin_require_certified` gates note pinning on that status — the top trust
  tier is minted by a model.
- `AssetBag.repair_references` / `repair_term_bindings` auto-fix dangling
  references *before* the structural adversary gate runs, so the gate is green by
  construction.
- `auto_accept_corpus` is hashed into `serve_config_hash` (`provenance.py`) and has
  no reader outside config and that hash, so the digest still moves on a knob that
  gates nothing. (The eight dead memory/cache knobs this item used to name were
  deleted in `2f86547`, which also added the five note-governance knobs that do
  gate behaviour.)

## Shipped (do not re-plan)

ADR 0003 M1–M4 and ADR 0004 M1–M2, M5 all landed (`b157834`, `3ae4eec`,
`061b00b`). The `workers` concurrency knob landed in `99f517d`. The clarification
protocol and Simulated SME landed with D12–D14. The 2026-07-25 measurement
integrity overhaul (`stages.py`, `stage_events.jsonl`, `runs/index.jsonl`) is
complete, and every number produced before 2026-07-26 is discarded.
