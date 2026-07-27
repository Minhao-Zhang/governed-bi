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

| # | Gap | Where | Status |
|---|-----|-------|--------|
| C1 | A solver crash is silently counted as a **refusal**, inflating `refusal_rate` and depressing EX for the crashier arm. `false_refusal_rate` reuses this inflated rate. | `run_datalake.py`; `run_experiment.py` | **Fixed 2026-07-25.** `governed_bi.stages.classify_outcome`/`classify_row` give the serve path and the eval harness one outcome vocabulary; the per-row scorer stamps `outcome`/`failed_stage` and the summary counts crashes and genuine refusals in separate buckets (`n_crashed`/`crash_rate`, `refusal_rate` over genuine refusals only). Detail in [`measurement.md`](../measurement.md). |
| C2 | `run_datalake` **swallows curator build errors** that `run_experiment` surfaces. A curated/SME corpus that failed to build (recursion limit, TPM cap) is scored on degraded seed-only content with no headline signal. | `run_datalake.py`; `pipeline.py:614-631` (records, does not raise) | **Fixed.** `run_datalake` imports `_collect_curator_errors`, warns per failed arm×db, and writes `curator_errors` / `sme_fold` into `summary.json`. |

Both items are closed in code; they are kept here (rather than deleted) because the runs
that produced the retired numbers in [`datalake-run.md`](datalake-run.md) predate the
fixes, which is why those numbers are not quotable.

## Correctness backlog (nice-to-have)

| # | Gap | Where | Bias / impact | Status |
|---|-----|-------|---------------|--------|
| C3 | Strict-hash normalizer is never self-checked — only lenient is validated against gold. `ex_strict` is unguarded. | `hash_grade.py` (`validate_gold_hashes_live`, `score_sql_hashes`) | Unknown direction on the secondary `ex_strict` metric only; headline `ex_lenient` is guarded. | **Open.** `validate_gold_hashes_live` still hashes only `hash_normalised_result` (lenient) and compares it to `gold.hash_lenient`; `hash_normalised_result_strict`/`gold.hash_strict` are never checked against each other before a run trusts `ex_strict`. |
| C4 | `run_datalake` has no train/test disjointness assertion (`run_experiment` does). | `run_experiment.py` (has it) | Defense-in-depth only — data verified disjoint (0 overlap) today; the scale run is where a bad split would hide. | **Fixed.** `run_datalake._assert_train_test_disjoint` (added, docstring names C4) loads both splits per db and raises `AssertionError` on any overlapping `question_id`; called right after the pool is built, result recorded as `summary.json["leakage"]`. |
| C5 | Stale `last_solve_meta` on a solver crash → the *prior* question's `tier`/`routed_schemas`/`schema_pick` recorded on the crashed row, corrupting routing metrics. | `arms.py`; consumers `run_experiment.py`, `run_datalake.py` | Corrupted routing metrics on the row immediately after a crash. | **Fixed.** `solve_with_meta` returns `(sql, meta)`; no instance attribute survives a crash. Landed with [`eval-concurrency-design.md`](eval-concurrency-design.md). |
| C6 | Decoy-touch uses **bare column-name** matching → a legit column sharing a decoy's name in another table false-positives. | `arms.py` (`_touches_suspect`); per-db scoping `run_datalake.py` | `decoy_touch_rate` biased up (over-counts). Behavioral metric only, not EX. | **Fixed.** `arms._touches_suspect` (docstring names C6) resolves each column reference to its own query scope before matching: `_split_suspect_refs` separates qualified `table.column` refs from bare ones and drops a bare name once a qualified counterpart covers it, and `_binding` walks the scope chain (including correlated subqueries) so a reused alias cannot attribute a column to the wrong table. A genuinely ambiguous unqualified reference still counts, fail-closed, matching how guardrail L3 reads the same ambiguity — so the metric can still over-count, never silently under-count. |
| C7 | 25 order-sensitive test qids flagged for exclusion are never consulted; normalizers always sort rows. | `eval_dataset/order_sensitive_qids.json`; `hash_grade.py` | ~1.2% of EX, applied uniformly across arms → deltas barely affected. | **Partially addressed.** Gradeable denominators (`ex_gradeable`, twin strata) now stamp `gold_order_sensitive` from `order_sensitive_qids.json` and exclude those IDs from the gradeable pool (`run_datalake` / `leakage.is_gradeable_eval_row` / `gradeable_report`). `normalise_result` / `normalise_result_strict` still unconditionally sort every row set — order-sensitive gold is kept out of the headline denominator rather than graded with order preserved. |
| C8 | `by_difficulty` is degenerate — ~85% of test rows have empty difficulty, all bucketed "unknown". | `run_experiment.py`, `run_datalake.py` | Not wrong, just near-zero signal in the per-difficulty breakdown. | **Partially addressed.** `_summarise_rows` now also reports `n_with_difficulty` (rows carrying a real, non-`"unknown"` label) alongside `by_difficulty`, so the near-zero signal is visible in `summary.json` instead of silently read as a uniform distribution. The data limitation itself — most BIRD rows carry no difficulty label — is unchanged and outside this repo's control. |
| C9 | Pooled `corpus_validation` runs without a connector → no physical column/table existence check against the live catalog at scale (`run_experiment` passes the connector). | `run_datalake.py` (`_validate_corpora(corpora)`) | A dangling reference to a non-existent column could ride into a scored arm at scale. | **Open.** The call site is unchanged: `_validate_corpora(corpora)` with an inline comment `# no connector: public-default`. |

## Efficiency backlog (single-threaded; ignore rate limits & parallelism)

The expensive things are already hoisted correctly (schema-doc vectors embedded once at
graph build, graph built once per arm, only the question embedded per turn). Remaining waste:

| # | Waste | Where | Fix | Status |
|---|-------|-------|-----|--------|
| E1 | Cross-check re-executes gold **and** pred for every item × every arm, on top of `score_sql_hashes` already executing pred; gold is arm-invariant. | `run_experiment.py` → `ex.py` (`execution_match`); `hash_grade.py` (`crosscheck_execution_match`) | Memoize the gold result-hash per `question_id` (compute once, reuse across arms); sample or reuse already-fetched rows. Removes ~N×3 gold + N×3 redundant pred executions. | **Open.** `crosscheck_execution_match` still calls `execution_match`, which still re-executes both `pred_sql` and `gold_sql` with no cache, once per item per arm. Applies to `run_experiment.py` only — `run_datalake.py` never runs this cross-check at all (see "No cross-check EX" in `datalake-run.md`). |
| E2 | Each corpus is loaded from disk twice — once for the solver, once by `_suspect_from_corpus`. | `run_experiment.py` (arm-loaded corpus vs. `_suspect_from_corpus`) | Iterate `TableAsset`s on the already-loaded `Corpus`; drop the re-load. | **Open.** `_suspect_from_corpus` still calls `load_corpus(corpus_root, schema=schema)` itself, independent of the corpus already loaded for that arm's solver. |
| E3 | `profile_database` runs twice per db (baseline + curated builds each profile the schema). | `pipeline.py` (`build_baseline_corpus`, `build_curated_corpus`) | Profile once per db; share the `TableAsset` list. Doubles avoidable profiling I/O at 69-schema scale. | **Open.** `build_baseline_corpus` and `build_curated_corpus` each still call `profile_database(connector, schema=schema, ...)` independently; no shared profiling. |
| E4 | Baseline always rebuilt on `--resume-curated` (re-profiles the DB); `run_datalake` already guards with `_has_yaml`. | `run_experiment.py` | Add the same resume guard. | **Open.** `build_baseline_corpus(connector, db_id, corpus_baseline)` is still called unconditionally, before the `resume_curated is not None` branch — no `_has_yaml`-style skip. |
| E5 | Gold self-check opens a fresh connector per db on a false premise (claims gold `sql_rename` is unqualified; 2022/2030 are already fully qualified). | `run_datalake.py` (`_datalake_gold_selfcheck`) | Run the self-check on the already-open unpinned serve connector, special-casing only the ~8 unqualified rows. Folds into the per-worker connection work in the concurrency design. | **Open.** `_datalake_gold_selfcheck` still opens a fresh schema-pinned `PostgresConnector` per sampled db, separate from the shared unpinned serve connector opened afterward. |
