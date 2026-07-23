# Eval correctness & efficiency backlog — 2026-07-22

From the 2026-07-22 experiment audit (four-perspective pass over `src/governed_bi/eval/`).
This is the **Q1 tracker**: correctness and single-threaded-efficiency items on the eval
harness that we are working on. None are blockers for the core EX methodology — that is
sound (vendored result-hash normalizer, fail-closed live gold self-check, globally-unique
gold keys, verified train/test disjointness, asserted no-leakage SME brief). These are the
gaps around it.

Companion docs from the same audit: [`eval-concurrency-design.md`](eval-concurrency-design.md)
(Q2 — configurable concurrency) and the doc-vs-code fixes landing separately (Q4). Q3
(statistical power / small-N) is being addressed by the full ~2,000-question pooled run now
in flight — see the note under **Live-run caveats**.

## Live-run caveats — check before trusting the in-flight scale run

Two items are **not** "someday" backlog: they bear directly on whether the pooled
`run_datalake` numbers now being produced are trustworthy. Both are **checkable read-only
from the output** when it lands — no rerun.

| # | Gap | Where | Check when the run lands |
|---|-----|-------|--------------------------|
| C1 | A solver crash is silently counted as a **refusal**, inflating `refusal_rate` and depressing EX for the crashier arm. `false_refusal_rate` reuses this inflated rate. | `run_datalake.py:289-309`; `run_experiment.py:170-194,565` | Classify the `error` field in `generations.*.jsonl` as an exception string vs the literal `"refusal"`; recompute refusal_rate on genuine refusals only. |
| C2 | `run_datalake` **swallows curator build errors** that `run_experiment` surfaces. A curated/SME corpus that failed to build (recursion limit, TPM cap) is scored on degraded seed-only content with no headline signal. | `run_datalake.py:59-66` (imports neither `_collect_curator_errors` nor `_warn_if_curator_errors`); `pipeline.py:614-631` (records, does not raise) | Grep each `corpus_<arm>/<db>/_build/run_manifest.json` for `error` / `fix_pass_error`; exclude or re-run any arm×db that failed to build cleanly. |

Fix direction for both: port `_collect_curator_errors` + `_warn_if_curator_errors` into
`run_datalake`, and distinguish a crash (exception) from a refusal (`sql is None` with no
exception) in the per-row scorer so the summary counts them separately.

## Correctness backlog (nice-to-have)

| # | Gap | Where | Bias / impact |
|---|-----|-------|---------------|
| C3 | Strict-hash normalizer is never self-checked — only lenient is validated against gold. `ex_strict` is unguarded. | `hash_grade.py:293-295` (check); strict at `hash_grade.py:71-105` | Unknown direction on the secondary `ex_strict` metric only; headline `ex_lenient` is guarded. |
| C4 | `run_datalake` has no train/test disjointness assertion (`run_experiment` does). | `run_experiment.py:290-294` (has it) | Defense-in-depth only — data verified disjoint (0 overlap) today; the scale run is where a bad split would hide. |
| C5 | Stale `last_solve_meta` on a solver crash → the *prior* question's `tier`/`routed_schemas`/`schema_pick` recorded on the crashed row, corrupting routing metrics. | `arms.py:186-222`; consumers `run_experiment.py:205`, `run_datalake.py:301-306` | **Fixed for free** by the return-meta refactor in [`eval-concurrency-design.md`](eval-concurrency-design.md). |
| C6 | Decoy-touch uses **bare column-name** matching → a legit column sharing a decoy's name in another table false-positives. | `arms.py:67-77`; per-db scoping `run_datalake.py:312` | `decoy_touch_rate` biased up (over-counts). Behavioral metric only, not EX. Overlaps a design finding (grade against the fixed trap manifest with qualified `table.column`). |
| C7 | 25 order-sensitive test qids flagged for exclusion are never consulted; normalizers always sort rows. | `eval_dataset/order_sensitive_qids.json`; `hash_grade.py:68,89` | ~1.2% of EX, applied uniformly across arms → deltas barely affected. |
| C8 | `by_difficulty` is degenerate — ~85% of test rows have empty difficulty, all bucketed "unknown". | `run_experiment.py:202`, `run_datalake.py:325` | Not wrong, just near-zero signal in the per-difficulty breakdown. |
| C9 | Pooled `corpus_validation` runs without a connector → no physical column/table existence check against the live catalog at scale (`run_experiment` passes the connector). | `run_datalake.py:503` | A dangling reference to a non-existent column could ride into a scored arm at scale. |

## Efficiency backlog (single-threaded; ignore rate limits & parallelism)

The expensive things are already hoisted correctly (schema-doc vectors embedded once at
graph build, graph built once per arm, only the question embedded per turn). Remaining waste:

| # | Waste | Where | Fix |
|---|-------|-------|-----|
| E1 | Cross-check re-executes gold **and** pred for every item × every arm, on top of `score_sql_hashes` already executing pred; gold is arm-invariant. | `run_experiment.py:184` → `ex.py:33-40`; `hash_grade.py:225` | Memoize the gold result-hash per `question_id` (compute once, reuse across arms); sample or reuse already-fetched rows. Removes ~N×3 gold + N×3 redundant pred executions. |
| E2 | Each corpus is loaded from disk twice — once for the solver, once by `_suspect_from_corpus`. | `run_experiment.py:465-467` then `:516-518` (`:132-142`) | Iterate `TableAsset`s on the already-loaded `Corpus`; drop the re-load. |
| E3 | `profile_database` runs twice per db (baseline + curated builds each profile the schema). | `pipeline.py:185`, `pipeline.py:551` | Profile once per db; share the `TableAsset` list. Doubles avoidable profiling I/O at 69-schema scale. |
| E4 | Baseline always rebuilt on `--resume-curated` (re-profiles the DB); `run_datalake` already guards with `_has_yaml`. | `run_experiment.py:382` | Add the same resume guard. |
| E5 | Gold self-check opens a fresh connector per db on a false premise (claims gold `sql_rename` is unqualified; 2022/2030 are already fully qualified). | `run_datalake.py:223-261` | Run the self-check on the already-open unpinned serve connector, special-casing only the ~8 unqualified rows. Folds into the per-worker connection work in the concurrency design. |
