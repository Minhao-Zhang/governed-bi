# Eval metrics: every field a run records

A run writes three artifacts, and this is the register of what is in them. The
machine-readable source of truth is
[`src/governed_bi/eval/metrics.py`](../src/governed_bi/eval/metrics.py); this page
is generated from it, and `tests/test_eval_metrics.py` asserts the register
matches what the drivers actually emit — in both directions, so a field that is
emitted-but-undeclared or declared-but-absent fails the suite.

| Artifact | Fields | Consumer |
|---|---|---|
| `manifest.json` | 29 | `index.COMPARABILITY_KEYS`, `index.RESUME_DRIFT_KEYS` |
| `generations.<arm>.jsonl` | 72 per (question, arm) | `_summarise_rows`, `analysis`, `power`, `error_taxonomy` |
| `summary.json` | 80 | `index.quotable` |

## Why this file exists

Every one of the three used to be an undeclared dict built independently by each
of the two drivers, and consumed by `.get()` in eight modules — where a renamed
or missing key degrades silently to `None` instead of raising.

That is not hypothetical. `comparable()` skips a knob that is `None` on both
sides, reasoning that two runs which both predate a knob did not differ in it.
Correct — and it is exactly why an *absent* key is dangerous: absence is
indistinguishable from agreement. The single-schema driver's manifest omitted six
of the eight comparability keys. Four were harmless (it pins one schema, so the
router never runs and its knobs have no value to record). Two were not: `split`,
and `corpus_content_hash` — which `index.py`'s own comment names as the one thing
the check did not cover, *because the corpus is the treatment*. So two runs of
that driver, over different corpora on different splits, compared as the same
configuration. And it was the driver whose numbers were historically quoted.

Both modes now build through `metrics.build_manifest`, and
`metrics.write_manifest` validates before writing. A knob that genuinely does not
apply is recorded as `None` **explicitly**, with `routing_bypassed` saying why, so
"not applicable" and "not recorded" stop looking alike.

## 1. Manifest — what makes a row mean something

### Knobs (gate keys: must be present in every mode, `None` when N/A)

| field | meaning |
|---|---|
| `split` | which BIRD split was scored |
| `model` | the configured serve model, or None under --skip-agent |
| `llm_temperature` | decoding temperature; None = provider default |
| `prompt_variants` | stage -> variant id map, for a human |
| `prompt_set_hash` | hash of the prompt TEXT, so an in-place edit moves it |
| `corpus_content_hash` | digest of the served corpora — the treatment itself |
| `git_sha` | the commit that produced the run |
| `route_top_k` | schema shortlist size; None when routing is bypassed |
| `route_llm_pick` | LLM picks one schema; None when routing is bypassed |
| `schema_pick_max_columns` | columns shown to the picker; None when bypassed |
| `use_embedder` | embedding channel on; None when routing is bypassed |
| `skip_agent` | no model was called at all |

### Scope (a resume that disagrees is a different experiment)

| field | meaning |
|---|---|
| `mode` | 'single' (one pinned schema) or 'datalake' (pooled, unpinned) |
| `arms` | the arms served |
| `oracles` | oracle rungs served |
| `replicate_of` | the arm re-served to measure the noise floor |
| `db_ids` | schemas in the pool |
| `limit` | per-schema question cap |
| `limit_dbs` | schema cap |
| `question_scope_hash` | digest of the scored question-id set |
| `routing_bypassed` | True when one schema is pinned, so the router never ran |

### Operational (recorded, deliberately not gate keys)

These change how long a run takes, never what a scored row means.

| field | meaning |
|---|---|
| `bird_dir` | dataset directory |
| `created_at_utc` | when the run started |
| `pg_dsn_host` | host actually connected to |
| `serve_workers` | serve-loop concurrency |
| `build_workers` | curator-build concurrency |
| `max_agent_steps` | recursion limit on the agent loop |
| `serve_path` | always agent_core (ADR 0002) |
| `allow_git_sha_drift` | operator opted out of the resume git-sha guard |

## 2. Arm summary — the rates and their denominators

The recurring defect class in this harness is a rate whose denominator silently
absorbs another outcome's failures. Over all rows, an arm that refuses 8 of 10
reports the *best* graded-delivery rate and the *worst* safety-clearance rate,
because refusing is neither delivering nor clearing — so a rung that refuses more
looks like a rung that governs better. Naming the population is what makes that
reviewable, and a test asserts every declared rate names one.

| rate | meaning | denominator |
|---|---|---|
| `ex_lenient` | headline execution accuracy | all scored rows (n) |
| `ex_strict` | EX under the strict normaliser | all scored rows (n) |
| `ex_gradeable` | EX excluding un-gradeable gold | gradeable rows |
| `ex_twin` | EX where the gold statement exists in train | twin rows |
| `ex_no_twin` | EX with no train twin — the defensible headline | twin-free rows |
| `conditional_ex_lenient` | EX among turns that produced SQL | rows that produced SQL |
| `cond_ex_given_routing` | EX among correctly-routed turns | rows the router hit |
| `refusal_rate` | GENUINE refusals; a crash is not a refusal | all scored rows (n) |
| `crash_rate` | our bug, counted apart from refusals | all scored rows (n) |
| `decoy_touch_rate` | predictions touching a suspect column | rows that produced SQL |
| `safety_clearance_rate` | delivered answers that cleared the guardrails | delivered rows |
| `graded_delivery_rate` | delivered answers served as unverified | delivered rows |
| `coverage_best_effort_rate` | answers delivered on partial coverage | delivered rows |
| `routing_recall` | router included the gold schema | rows with a recorded routing decision |
| `routing_escape_rate` | SQL reached outside the routed schemas | rows where escape was observable |
| `schema_pick_accuracy` | LLM picked the gold schema | rows that recorded a pick |
| `schema_pick_accuracy_excl_fallback` | …excluding picker fallbacks | picks that did not fall back |
| `share_with_a_note` | turns that received at least one note | all scored rows (n) |

### Counts

Each count exists so an exclusion from a rate above stays visible: a rate
reported without its excluded count reads as full coverage.

**counts** — `n`, `n_answered`, `n_correct`, `n_refused`, `n_crashed`, `n_missing_gold`, `n_gradeable`, `n_gold_unusable`, `n_frozen_gold`, `n_order_sensitive_gold`, `n_twin_gradeable`, `n_no_twin_gradeable`, `n_twin_unstamped`, `n_gold_twin_in_train`, `n_decoy_touch`, `n_wrong_but_nrows_match`, `n_unmapped_refused_by`, `n_with_difficulty`, `n_with_governance_stamp`, `n_tables_used_unresolved`, `n_rows_no_db_id`, `n_pick_fallback`, `n_routing_observed`, `n_routing_bypassed`, `n_routing_crashed`, `n_routing_unrecorded`, `n_routing_escaped`, `n_routing_escape_observed`, `n_routing_escape_unknown`, `n_correct_routed`, `n_correct_unrouted`, `n_correct_bypassed`, `n_correct_routing_crashed`, `n_correct_routing_unrecorded`, `n_correct_via_routing_escape`, `n_correct_unaccounted`, `n_safety_clearance_observed`, `n_graded_delivery_observed`, `n_coverage_best_effort_observed`, `n_correct_with_empty_gold`, `n_correct_and_pred_has_no_from`, `n_correct_and_zero_table_overlap`

### Means and breakdown blocks

**means** — `mean_attempts`, `mean_context_chars`, `mean_ledger_len`, `mean_notes_injected`, `mean_few_shots_injected`

**blocks** — `arm`, `question_ids`, `treatment`, `cost`, `tool_calls`, `errors`, `by_db`, `by_difficulty`, `by_outcome`, `by_failed_stage`, `by_error_type`, `by_guardrail_layer`, `by_tier`, `by_semantic_assurance`, `by_gold_rank`

## 3. Generation row — one record per (question, arm)

**identity** — `arm`, `question_id`, `request_id`, `run_id`, `turn_id`, `db_id`, `split`, `difficulty`

**verdict** — `correct`, `correct_strict`, `error`, `error_type`, `outcome`, `failed_stage`, `failed_layer`, `refused_by`, `nrows_match`

**prediction** — `generated_sql`, `pred_nrows`, `pred_ncols`, `gold_nrows`, `attempts`, `tables_used`, `tables_used_unresolved`, `n_tables_used_unresolved`, `licensed_tables`

**governance** — `tier`, `safety_clearance`, `semantic_assurance`, `graded_delivery`, `coverage_best_effort`, `decoy_touch`, `by_guardrail_layer`, `ledger_len`, `n_tool_calls`

**context** — `context_chars`, `context_hash`, `injected_note_ids`, `n_notes_injected`, `n_caveats_injected`, `n_few_shots_injected`, `n_joins_injected`, `n_metrics_injected`, `n_terms_injected`, `retrieved_tables`

**routing** — `routed_schemas`, `routed_hit`, `routing_bypassed`, `routing_escaped`, `routing_escape_unknown`, `schema_pick`, `schema_pick_fallback`, `pick_hit`, `shortlisted_schemas`, `total_schemas`

**leakage** — `gold_twin_in_train`, `gold_frozen`, `gold_order_sensitive`, `gold_schema_rank`

**oracle** — `oracle_rung`, `oracle_applied`, `oracle_gold_tables`, `oracle_corpus_tables`, `oracle_offered_tables`, `oracle_padding_degenerate`

**cost** — `latency_sec`, `cost_est_usd`, `usage`, `token_usage`, `token_sum`

**provenance** — `prompt_set_hash`, `prompt_variants`

## Regenerating this page

The tables above come from the register. After editing
`src/governed_bi/eval/metrics.py`, re-run the generator and commit both:

```bash
uv run python scripts/gen_eval_metrics_doc.py
```
