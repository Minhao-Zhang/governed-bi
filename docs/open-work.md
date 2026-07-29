# Open work

The single tracker for work that is **open**. It replaces four dated trackers
(`engineering-gaps-2026-07-16`, `eval-audit-backlog-2026-07-22`,
`clarification-sme-benchmark-build-plan`, `implementation-plan-notes-and-run-logging`),
whose closed items now live only in git history. Nothing here is a design
record — decisions belong in [design-decisions.md](design-decisions.md) and
[adr/](adr/).

## Correctness

| # | What | Where |
| --- | --- | --- |
| C3 | `ex_strict` is unguarded: `validate_gold_hashes_live` hashes only the lenient normaliser and compares it to `gold.hash_lenient`. `hash_normalised_result_strict` is never checked against `gold.hash_strict` before a run trusts `ex_strict`. | `eval/hash_grade.py` |
| C9 | Pooled `_validate_corpora(corpora)` runs with no connector, so nothing checks asset references against the live catalog at scale. | `eval/run_datalake.py` |
| G8 | The grader self-check was only ever validated on a 5-row sample. A full head-to-head needs the live DB. | `eval/hash_grade.py` |

## Efficiency

| # | What | Where |
| --- | --- | --- |
| E1 | Cross-check re-executes gold **and** prediction per item per arm, though gold is arm-invariant. Memoise the gold hash per `question_id`. | `eval/run_experiment.py` → `eval/ex.py` |
| E2 | Each corpus is loaded from disk twice — once for the solver, once by `_suspect_from_corpus`. | `eval/run_experiment.py` |
| E3 | `profile_database` runs twice per db (baseline and curated each profile independently). | `curator/pipeline.py` |
| E4 | Baseline is rebuilt unconditionally on `--resume-curated`; `run_datalake` already guards with `_has_yaml`. | `eval/run_experiment.py` |
| E5 | The gold self-check opens a fresh schema-pinned connector per sampled db, separate from the shared unpinned serve connector. | `eval/run_datalake.py` |

## Experiment design

The instrument is sound; the design does not yet isolate the claim. In priority
order.

**Before adding arms, read the conditional diagnostics.** X1 and X2 exist to
isolate *which part of the corpus does the work*, and that needs new arms. But six
within-arm conditionals now give a partial answer for free, on rows you already
have — stamp calibration, decoy-touch with vs without a caveat, EX with vs without
a note, EX after a repair, and a ceiling on guardrail-induced loss. See
[Eval metrics](eval-metrics.md#conditional-diagnostics--which-part-of-the-governance-is-doing-the-work).
If the caveat split shows no effect, X2's ablation has its answer before the run.

| # | What | Why it blocks a claim |
| --- | --- | --- |
| X1 | **No length-matched placebo arm.** Every rung is a strict content superset, so "later rung = more tokens" is guaranteed by construction. Serve schema *Y*'s corpus against schema *X*'s questions, byte-matched on `context_chars`. | Without it, every curated-arm result is confounded with prompt length. |
| X2 | **`mask_only` ablation.** `_mark_columns_absent_from_gold` flags every column train gold never touched, and that mask covers the gold columns of ~86% of test questions. Isolate it from joins/metrics/few-shots. | The headline decoy-touch result may be mechanical, not evidence about metadata. |
| X3 | **`refute()` raises `NotImplementedError`** (`curator/adversary.py`), so the `curated` rung's adversary is only the structural linter plus two confidence penalties. Either implement it or rename the rung to what it is. | An arm named after a mechanism that does not run. |
| X4 | **Single seed everywhere.** Needs ≥3 curator draws plus a serve replicate to separate build variance from serve variance. | The largest live run is n=52 and the `curated`/`sme` sign flips between consecutive runs. |
| X5 | **The 69-schema scale run** (8,134 train / 2,030 test) has only ever run with `--skip-agent`. | No result exists at the scale the design targets. |
| X6 | **The refuse-gate is unexercised, and its only negative set does not survive pooling.** BIRD questions are all answerable and `NegativeExampleAsset` is never generated (0 files across every generated corpus). The one measurement that exists — `refusal_accuracy` in `run_experiment` — draws its negatives from *other* `db_id`s, which `load_cross_db_unanswerable` documents as "unanswerable **for `db_id`**". That holds only because the single-schema driver pins the corpus to one schema. In a pooled run every other schema is in the pool, so those questions are **answerable**, and the metric would score every correct answer as a refuse-gate failure. A genuinely out-of-scope negative set (the shape of `dataset.BEER_FACTORY_UNANSWERABLE`, which is 3 hand-written questions) is what the pooled driver would need. | `false_refusal_rate` is unmeasured; refusal is indistinguishable from failure — and this is what blocks collapsing the two eval drivers into one (see below). |

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

`run_datalake.py` (4,732 lines) is a fork of `run_experiment.py` (1,264 lines): it
imports 10 of that module's private symbols, and the structural difference is one
thing — the serve connector is pinned to `schema=db_id` in the single-schema
driver and `schema=None` in the pooled one. Single-schema is the pooled case at
n=1. Collapsing them removes roughly 9,600 lines.

**Done (2026-07-28):** the manifest is no longer forked. Both modes build through
`metrics.build_manifest`, which closed a real hole — see
[Eval metrics](eval-metrics.md).

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

Mechanical, but wide: `run_experiment.py` is still imported by **11 test files**
and by `run_datalake.py` (10 private symbols) and `curator/pipeline.py`
(`_sme_fold_signal`).

1. Move the ~10 shared helpers (`_utc_ts`, `_write_jsonl`, `_cost_block`,
   `_validate_corpora`, `_collect_curator_errors`, `_sme_fold_signal`,
   `_warn_if_*`, `_suspect_from_corpus`, `_RefuseAllSolver`, `_dsn_host`) out of the
   driver into a shared module, so no driver reaches into another's privates.
2. Rewire the 11 test files. Three need structural rewrites, not import edits:
   `test_eval_concurrency`, `test_prompt_attribution` and
   `test_run_experiment_parity` drive `_run_arm_generations` (261 lines), the
   single-schema arm loop. `test_run_experiment_parity`'s entire purpose — the two
   drivers agree — dissolves when there is one driver.
3. Confirm `--resume-curated` is subsumed by the pooled staging/promotion resume,
   then delete `run_experiment.py`.
4. Rename the survivor: `run_datalake` is the wrong name for the only driver.

The register in `metrics.py` is the contract that makes step 2 checkable — it is
why the merge is now a mechanical job rather than a risky one.

## Test debt blocked on the eval driver

Twenty tests assert on implementation **source text** via `inspect.getsource`
(`test_ladder_design`, `test_hash_grade`, `test_datalake_routing`,
`test_build_isolation`, `test_run_experiment_parity`, `test_oracle_and_probes`,
`test_retrieval_index_cache`, `test_curator_seed_joins`,
`test_middleware_guardrail`). A reformat breaks them and an equivalent rewrite
defeats them.

They are **not** dead weight, and they should not be deleted as they stand. Each
pins a call-site or ordering invariant in `run_datalake()` — a 798-line function
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
- Eight `Settings` knobs are stamped into `provenance.py` but enforce nothing, so
  a run reports thresholds it never applied.

## Shipped (do not re-plan)

ADR 0003 M1–M4 and ADR 0004 M1–M2, M5 all landed (`b157834`, `3ae4eec`,
`061b00b`). The `workers` concurrency knob landed in `99f517d`. The clarification
protocol and Simulated SME landed with D12–D14. The 2026-07-25 measurement
integrity overhaul (`stages.py`, `stage_events.jsonl`, `runs/index.jsonl`) is
complete, and every number produced before 2026-07-26 is discarded.
