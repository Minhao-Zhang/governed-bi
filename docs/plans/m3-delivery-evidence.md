# M3 delivery evidence (N9 / N10 / N10a)

Paste into each PR body. Local branches only — no `gh`. Recorded 2026-07-31 against [batch-m3.md](batch-m3.md); ledger completed after review against collected nodeids.

| Branch | Tip (at write) | Base | Stack |
|---|---|---|---|
| `m3/n9` | `7f0fb97` | `5c7468f` (pre-M3 on `impl/rebuild-first-batch`) | first |
| `m3/n10a` | `d9b575e` | `5c7468f` | parallel with N9 |
| `m3/n10` | `13af916` | `m3/n9` (`7f0fb97`) | after N9; does **not** include N10a |
| `impl/rebuild-first-batch` | tip | linear N9 → N10a → N10 | integration |

**Collected:** `5c7468f` **1714** → tip **1695** = **−19** net (**37** removed nodeids, **18** added). This batch is the only planned exemption to “tests only grow”; the ledger below is the voucher.

**`skip_agent` accept note:** plan text said `grep skip_agent|skip-agent|git_sha_drift` in `src/` empty. Actual: **zero live identifiers** (no CLI flag, no manifest field, no Metric, no kwarg) + **historical comments only** (`formerly --skip-agent` / `used to be` / Option A help text / the `arms = ()` resume-safety note). Prefer keeping the archaeology over erasing it; treat the accept bar as “zero live refs + historical comments.”

---

## Full test-count ledger (37 removed → 18 added)

### Moved (same assertion, new module) — 7 removed + 7 added

From deleted `tests/test_run_experiment_parity.py` → `tests/test_datalake_row_discipline.py` (rewired onto `run_datalake`):

| Removed | Added |
|---|---|
| `…parity.py::test_cost_block_totals` | `…discipline.py::test_cost_block_totals` |
| `…parity.py::test_cost_block_keeps_no_data_distinct_from_zero` | same name under discipline |
| `…parity.py::test_manifest_host_comes_from_the_dsn_not_a_literal` | same |
| `…parity.py::test_manifest_host_never_carries_the_password` | same |
| `…parity.py::test_a_fingerprint_over_this_drivers_rows_is_observed_not_blank` | same |
| `…parity.py::test_a_row_without_delivery_fields_reads_as_unverified_not_as_zero` | same |
| `…parity.py::test_rows_carry_result_shape_fields` | same (onto `_run_pool_arm`) |

### Dropped N9 two-driver / driver-only — 13

- `test_run_experiment_parity.py::test_arm_summary_reports_shape_and_attempt_aggregates`
- `test_run_experiment_parity.py::test_cost_block_matches_the_pooled_driver_shape`
- `test_run_experiment_parity.py::test_a_crash_is_not_scored_as_a_refusal` → covered by `test_datalake_stage_attribution`
- `test_run_experiment_parity.py::test_a_genuine_refusal_is_still_a_refusal` → same
- `test_run_experiment_parity.py::test_an_empty_arm_reports_unmeasured_rather_than_zero` → same
- `test_run_experiment_parity.py::test_the_row_builder_records_every_field_the_treatment_check_reads`
- `test_run_experiment_parity.py::test_treatment_and_errors_land_where_the_ledger_looks`
- `test_run_experiment_parity.py::test_the_single_db_driver_indexes_its_run`
- `test_eval_metrics.py::test_the_single_schema_driver_serves_the_graded_delivery_its_manifest_claims`
- `test_eval_metrics.py::test_the_single_schema_summary_emits_nothing_undeclared`
- `test_prompt_attribution.py::test_the_pinned_driver_row_carries_the_prompt_stamp`
- `test_eval_concurrency.py::test_experiment_arm_generations_workers_invariance`
- `test_eval_concurrency.py::test_missing_factory_when_parallel_raises`

### Dual-track collapse (N10) — 5 → 2

| Removed | Successor |
|---|---|
| `test_paid_resume_refuses_git_sha_drift_by_default` | `test_resume_refuses_git_sha_drift` |
| `test_allow_git_sha_drift_opts_paid_resume_in` | (absorbed; always fatal) |
| `test_smoke_resume_warns_on_git_sha_drift` | (absorbed; always fatal) |
| `test_resuming_after_a_code_change_is_fatal_on_paid_resume` | `test_resuming_after_a_code_change_is_fatal` |
| `test_resuming_after_a_code_change_warns_on_smoke` | (absorbed; always fatal) |

### Renames kept (N10) — 2 → 2

| Removed | Added |
|---|---|
| `test_a_skip_agent_smoke_run_is_not_comparable_to_a_real_one` | `test_a_smoke_run_with_no_model_is_not_comparable_to_a_real_one` |
| `test_skip_agent_does_not_seed_over_a_resolved_relocated_ledger` | `test_offline_sme_does_not_seed_over_a_resolved_relocated_ledger` |

### Parametrized shrink — 3

- `test_every_resume_drift_key_is_actually_checked[skip_agent]` (key gone from `RESUME_DRIFT_KEYS`)
- `test_every_ledger_gate_key_is_present_in_both_modes[skip_agent-skip_agent]` (knob gone from `MANIFEST_KNOBS`)
- `test_an_unknown_variant_on_the_cli_exits_before_any_work[governed_bi.eval.run_experiment]` (+ the `[run_datalake]` param nodeid; successor is the unparametrized `…exits_before_any_work` on `run_datalake` only)

### Knob / dual-driver disappear — 6

- `test_a_skip_agent_run_is_never_quotable`
- `test_a_real_run_is_not_penalised_for_the_flag_being_recorded`
- `test_a_run_predating_the_skip_agent_field_is_not_accused`
- `test_both_drivers_record_no_model_under_skip_agent`
- `test_skip_agent_records_no_model_in_either_mode`
- `test_resuming_a_skip_agent_directory_with_a_model_is_fatal` — hazard retired by construction (`arms = ()` under `--oracle-only`; see comment at `run_datalake.py` oracle_only entry)

### Pure additions (not renames/moves) — 6

- `test_rvgd_phys_map_agrees_with_table_by_name_on_none` (N10a)
- `test_rvgd_phys_map_agrees_on_bird_when_present` (N10a)
- `test_manifest_schema_version_bumps_when_knobs_change` (N10 bump guard)
- `test_oracle_only_empty_arms_records_no_model_via_build_manifest`
- `test_empty_fair_arms_record_no_model_when_caller_passes_none`
- `test_resuming_after_a_model_change_warns` (documents model drift; points at `arms = ()` for the old skip_agent resume poison)

Authoritative count is the exact nodeid lists below (**37** removed / **18** added), taken from `pytest --collect-only` at `5c7468f` vs tip.

### Exact removed nodeids (37)

```
tests/test_datalake_stage_attribution.py::test_resuming_after_a_code_change_is_fatal_on_paid_resume
tests/test_datalake_stage_attribution.py::test_resuming_after_a_code_change_warns_on_smoke
tests/test_eval_analysis.py::test_resuming_a_skip_agent_directory_with_a_model_is_fatal
tests/test_eval_concurrency.py::test_experiment_arm_generations_workers_invariance
tests/test_eval_concurrency.py::test_missing_factory_when_parallel_raises
tests/test_eval_index.py::test_a_real_run_is_not_penalised_for_the_flag_being_recorded
tests/test_eval_index.py::test_a_run_predating_the_skip_agent_field_is_not_accused
tests/test_eval_index.py::test_a_skip_agent_run_is_never_quotable
tests/test_eval_index.py::test_a_skip_agent_smoke_run_is_not_comparable_to_a_real_one
tests/test_eval_index.py::test_both_drivers_record_no_model_under_skip_agent
tests/test_eval_index.py::test_every_resume_drift_key_is_actually_checked[skip_agent]
tests/test_eval_metrics.py::test_every_ledger_gate_key_is_present_in_both_modes[skip_agent-skip_agent]
tests/test_eval_metrics.py::test_skip_agent_records_no_model_in_either_mode
tests/test_eval_metrics.py::test_the_single_schema_driver_serves_the_graded_delivery_its_manifest_claims
tests/test_eval_metrics.py::test_the_single_schema_summary_emits_nothing_undeclared
tests/test_prompt_attribution.py::test_an_unknown_variant_on_the_cli_exits_before_any_work[governed_bi.eval.run_datalake]
tests/test_prompt_attribution.py::test_an_unknown_variant_on_the_cli_exits_before_any_work[governed_bi.eval.run_experiment]
tests/test_prompt_attribution.py::test_the_pinned_driver_row_carries_the_prompt_stamp
tests/test_run_experiment_parity.py::test_a_crash_is_not_scored_as_a_refusal
tests/test_run_experiment_parity.py::test_a_fingerprint_over_this_drivers_rows_is_observed_not_blank
tests/test_run_experiment_parity.py::test_a_genuine_refusal_is_still_a_refusal
tests/test_run_experiment_parity.py::test_a_row_without_delivery_fields_reads_as_unverified_not_as_zero
tests/test_run_experiment_parity.py::test_an_empty_arm_reports_unmeasured_rather_than_zero
tests/test_run_experiment_parity.py::test_arm_summary_reports_shape_and_attempt_aggregates
tests/test_run_experiment_parity.py::test_cost_block_keeps_no_data_distinct_from_zero
tests/test_run_experiment_parity.py::test_cost_block_matches_the_pooled_driver_shape
tests/test_run_experiment_parity.py::test_cost_block_totals
tests/test_run_experiment_parity.py::test_manifest_host_comes_from_the_dsn_not_a_literal
tests/test_run_experiment_parity.py::test_manifest_host_never_carries_the_password
tests/test_run_experiment_parity.py::test_rows_carry_result_shape_fields
tests/test_run_experiment_parity.py::test_the_row_builder_records_every_field_the_treatment_check_reads
tests/test_run_experiment_parity.py::test_the_single_db_driver_indexes_its_run
tests/test_run_experiment_parity.py::test_treatment_and_errors_land_where_the_ledger_looks
tests/test_silent_failure_remediation_f1_f7.py::test_allow_git_sha_drift_opts_paid_resume_in
tests/test_silent_failure_remediation_f1_f7.py::test_paid_resume_refuses_git_sha_drift_by_default
tests/test_silent_failure_remediation_f1_f7.py::test_skip_agent_does_not_seed_over_a_resolved_relocated_ledger
tests/test_silent_failure_remediation_f1_f7.py::test_smoke_resume_warns_on_git_sha_drift
```

### Exact added nodeids (18)

```
tests/test_corpus_table_by_name.py::test_rvgd_phys_map_agrees_on_bird_when_present
tests/test_corpus_table_by_name.py::test_rvgd_phys_map_agrees_with_table_by_name_on_none
tests/test_datalake_row_discipline.py::test_a_fingerprint_over_this_drivers_rows_is_observed_not_blank
tests/test_datalake_row_discipline.py::test_a_row_without_delivery_fields_reads_as_unverified_not_as_zero
tests/test_datalake_row_discipline.py::test_cost_block_keeps_no_data_distinct_from_zero
tests/test_datalake_row_discipline.py::test_cost_block_totals
tests/test_datalake_row_discipline.py::test_manifest_host_comes_from_the_dsn_not_a_literal
tests/test_datalake_row_discipline.py::test_manifest_host_never_carries_the_password
tests/test_datalake_row_discipline.py::test_rows_carry_result_shape_fields
tests/test_datalake_stage_attribution.py::test_resuming_after_a_code_change_is_fatal
tests/test_eval_analysis.py::test_resuming_after_a_model_change_warns
tests/test_eval_index.py::test_a_smoke_run_with_no_model_is_not_comparable_to_a_real_one
tests/test_eval_index.py::test_oracle_only_empty_arms_records_no_model_via_build_manifest
tests/test_eval_metrics.py::test_empty_fair_arms_record_no_model_when_caller_passes_none
tests/test_manifest_schema_bump.py::test_manifest_schema_version_bumps_when_knobs_change
tests/test_prompt_attribution.py::test_an_unknown_variant_on_the_cli_exits_before_any_work
tests/test_silent_failure_remediation_f1_f7.py::test_offline_sme_does_not_seed_over_a_resolved_relocated_ledger
tests/test_silent_failure_remediation_f1_f7.py::test_resume_refuses_git_sha_drift
```

---

## N9 · retire `run_experiment` (`m3/n9`)

- Delete `src/governed_bi/eval/run_experiment.py`; `run_datalake.py` is the only eval driver.
- Gateway “live covered by” docs → `run_datalake`. E4 closed. ADRs untouched.
- Parity file triaged (not wholesale `rm`): discipline kept; two-driver asserts dropped.

### Smoke

`run_datalake --dbs beer_factory --limit 5` — run locally if BIRD/Postgres available; note skip if not.

---

## N10a · rvgd ↔ `table_by_name` (`m3/n10a`, parallel)

- `phys_name_to_table_id` in `retrieval/rvgd.py` (O(n)); docstring forbids per-name `table_by_name` loops.
- Assert `table_by_name(bare) is None` iff map `[bare] is None`.

---

## N10 · delete dual-track / `--oracle-only` (`m3/n10`)

- **Option A:** `--oracle-only` = oracle ladder only, `arms = ()`, no model. Delete `skip_agent` / `allow_git_sha_drift`.
- `MANIFEST_SCHEMA_VERSION` 1 → 2; bump guard in `tests/test_manifest_schema_bump.py`.
- Resume `git_sha` **unconditionally fatal** (`THIS DOES NOT AUTHORIZE DELETING THE CHECK ITSELF`).
- Hard gate: `drift - comparability == {"git_sha"}` (members deleted, not widened).
- **Resume poison:** old `--skip-agent` fair rows scored 0 by construction; resume replays. `--oracle-only` never writes fair generations (`arms = ()` at library entry) — hazard impossible unless that invariant breaks; comment + `test_resuming_after_a_model_change_warns` docstring record the link.
- Runbook step 0/1 → `--oracle-only` (one sentence); no full rewrite.
- Regen `docs/eval-metrics.md`.

### Intentional non-fixes

- v1 vs v2 `comparable()` still allows different schema versions (M5 / 20260730 archives).
- Missing-key-as-equal X.4 hole **not** fixed — only the “forgot to bump” guard lands.
