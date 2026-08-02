# Eval metrics: every field a run records

A run writes up to five artifacts, and this is the register of what is in them. The
machine-readable source of truth is
[`src/governed_bi/eval/metrics.py`](../src/governed_bi/eval/metrics.py); this page
is generated from it — every field name and every count below comes from a register
tuple, so `uv run python scripts/gen_eval_metrics_doc.py --check` fails in CI if the
two disagree.

`tests/test_eval_metrics.py` checks the register against what the drivers emit:
the pooled driver's manifest and arm summary **in both directions** (a field that is
emitted-but-undeclared or declared-but-absent fails the suite), the generation row
against what the summariser reads off it, and the single-schema driver's
`ArmSummary` in the emitted-but-undeclared direction only — that driver reports a
documented subset of `summary.json`, so "declared but absent" is expected there.

| Artifact | Fields | Consumer |
|---|---|---|
| `manifest.json` | 52 (47 in every run) | `index.COMPARABILITY_KEYS`, `index.RESUME_DRIFT_KEYS` |
| `generations.<arm>.jsonl` | 79 per (question, arm) | `_summarise_rows`, `analysis`, `power`, `error_taxonomy` |
| `summary.json` | 100 | `index.quotable` |
| `stage_events.jsonl` | 9 per (question, arm, stage) | read by hand; per-stage latency attribution |
| `split_gap.json` | 6 | read by hand; `--split both` only |

`stage_events.jsonl` is written by the pooled driver only, and `split_gap.json` only
under `--split both`. Neither is read by a gate.

## Why this file exists

Every one of the first three used to be an undeclared dict built independently by
each of the two drivers, and consumed by `.get()` in eight modules — where a renamed
or missing key degrades silently to `None` instead of raising.

That is not hypothetical. `comparable()` skips a knob that is `None` on both
sides, reasoning that two runs which both predate a knob did not differ in it.
Correct — and it is exactly why an *absent* key is dangerous: absence is
indistinguishable from agreement. The single-schema driver's manifest omitted six
of the eight comparability keys of the time. Four were harmless (it pins one schema,
so the router never runs and its knobs have no value to record). Two were not:
`split`, and `corpus_content_hash` — which `index.py`'s own comment names as the one
thing the check did not cover, *because the corpus is the treatment*. So two runs of
that driver, over different corpora on different splits, compared as the same
configuration. And it was the driver whose numbers were historically quoted.

Both modes now build through `metrics.build_manifest`, and
`metrics.write_manifest` validates before writing. A knob that genuinely does not
apply is recorded as `None` **explicitly**, with `routing_bypassed` saying why, so
"not applicable" and "not recorded" stop looking alike.

Presence is all a validator can check, though, and a *defaulted* parameter passes a
presence check while recording a value the run never used. That happened:
`llm_temperature` defaulted to `None` and the single-schema driver never passed it,
so every one of its manifests recorded "provider default" for runs whose temperature
was configured and really forwarded to the model. So every knob and every scope field
is now a **required** keyword of `build_manifest`, and `manifest_schema_version`
records that a given manifest was built that way — `comparable()` refuses a pair
whose records predate the guarantee rather than applying the None-on-both-sides rule
to manifests that cannot support it.

## 1. Manifest — what makes a row mean something

### Contract

| field | meaning |
|---|---|
| `manifest_schema_version` | contract version of this manifest; comparable() refuses a pair without it, because only from version 1 on is every declared field guaranteed present |

### Knobs (gate keys: must be present in every mode, `None` when N/A)

Every one of these is a required keyword of `build_manifest`, and
`index.COMPARABILITY_KEYS` is **derived** from this list minus an explicit,
documented exclusion set (`index.COMPARABILITY_EXCLUSIONS`) — so a knob added here
joins the comparability gate by default instead of silently skipping it.

| field | meaning |
|---|---|
| `split` | which BIRD split was scored |
| `model` | the configured serve model, or None when no fair arm and no model-needing oracle rung was requested (--oracle-only's inferred no-model path) |
| `llm_temperature` | decoding temperature; None = provider default |
| `llm_reasoning_effort` | serve/curator reasoning budget (none|low|medium|high|xhigh|max); the 2026-07-30 vs 2026-07-31 ladders differ only here and moved baseline EX by 2.5pp against a 2.3pp MDE, so it is a treatment, not an operational detail |
| `embedding_model` | the embedding model behind the schema-routing vector channel; swapping it moves shortlist recall, which is upstream of every scored row |
| `embedding_dimensions` | requested embedding width; None = the model's native size (1536 for -3-small, 3072 for -3-large), so None means different things per model and is only interpretable alongside embedding_model |
| `prompt_variants` | stage -> variant id map, for a human |
| `prompt_set_hash` | hash of the prompt TEXT, so an in-place edit moves it |
| `corpus_content_hash` | digest of the served corpora — the treatment itself |
| `question_pool_hash` | digest of the graded questions AND the gold each is graded against, so a refiltered dataset stops comparing as the same experiment |
| `question_subset` | identity of an explicit --questions id list ('<n> ids @ <digest>'), None when the run served the whole split under its caps. A knob and not scope: a subset is chosen for a REASON — the 131 questions an intervention could move — so its EX is a biased sample of the split's and the two are different quantities. question_pool_hash refuses the pair on its own; this says why in words |
| `git_sha` | the commit that produced the run |
| `route_top_k` | schema shortlist size; None when routing is bypassed |
| `route_llm_pick` | LLM picks one schema; None when routing is bypassed |
| `schema_pick_max_columns` | columns shown to the picker; None when bypassed |
| `use_embedder` | embedding channel on; None when routing is bypassed |
| `grade_semantic_failures` | graded delivery: a coverage / L3-L5 / execution-exhaustion failure hands the grader its last generated SQL stamped `unverified` instead of refusing, so the same turn scores 0 under one setting and can score 1 under the other |
| `always_note_global_max` | always-notes admitted per turn; the budget applies whether or not PIN is on |
| `always_note_char_max` | character ceiling on the admitted always-notes |
| `pin_triggers_enabled` | keyword-triggered notes PIN: forced into the prompt ahead of RRF, AND their schema prepended to the router shortlist — so this moves ROUTING too |
| `pin_require_certified` | only certified notes may PIN; None when pin_triggers_enabled is False, because nothing could pin and a recorded True would claim a gate that never ran |
| `pin_max` | cap on pinned notes, and so on the schemas PIN adds to the shortlist; None when pin_triggers_enabled is False |

### Scope (a resume that disagrees is a different experiment)

Also required keywords: `arms=()` recorded for a run that served three arms is a
false record no presence check can catch.

| field | meaning |
|---|---|
| `mode` | 'single' (one pinned schema) or 'datalake' (pooled, unpinned) |
| `arms` | the arms served |
| `oracles` | oracle rungs served |
| `replicate_of` | the arm re-served to measure the noise floor |
| `replicate_limit` | questions the REPLICATE arm served, when capped below the scored pool; None = a full replicate. Scope and deliberately NOT a comparability knob: it changes how precisely this run estimated the discordance rate, never what any fair arm served, so two runs that differ only here are still the same experiment. What it costs is recorded per comparison instead — `detectable.floor_n_pairs` / `floor_coverage` / `floor_is_subsampled` |
| `replicate_sample_seed` | seed for the db-stratified draw that picked the capped replicate's questions; None when there was no cap. Recorded so the draw is reproducible — a cap with an unrecorded seed is a floor nobody can re-measure |
| `db_ids` | schemas in the pool |
| `limit` | per-schema question cap |
| `limit_dbs` | schema cap |
| `question_scope_hash` | digest of the scored question-id set |
| `routing_bypassed` | True when one schema is pinned, so the router never ran |

### Operational (recorded, deliberately not gate keys)

These change how long a run takes, never what a scored row means, so these are the
only `build_manifest` parameters allowed a default.

| field | meaning |
|---|---|
| `bird_dir` | dataset directory |
| `base_url` | chat endpoint; None = provider default |
| `embedding_base_url` | embedding endpoint; None = same as base_url |
| `created_at_utc` | when the run started |
| `pg_dsn_host` | host actually connected to |
| `serve_workers` | serve-loop concurrency |
| `build_workers` | curator-build concurrency |
| `max_agent_steps` | operator override for the curator's per-schema TOOL-CALL budget; null = derived from schema size, and the resolved figure is each corpus's run_manifest.json tool_call_budget. Effective recursion limit is 3x + 4 |
| `serve_path` | always agent_core (ADR 0002) |
| `git_branch` | branch name when HEAD is a symbolic ref; null when detached — how the run was produced, not what was scored (operational, not a knob) |
| `main_git_sha` | SHA of refs/heads/main at run start; null/unknown when the ref is absent |
| `dirty` | True when the working tree had uncommitted changes at run start |
| `diff_sha256` | SHA-256 of git status --porcelain + git diff HEAD when dirty; null when clean |

### Stamped after the build (declared, not required)

No builder can fill these, because the value does not exist when the manifest is
written — and the manifest is written *before* the run phase so that a crashed run
still leaves its knobs on disk.

| field | meaning |
|---|---|
| `corpus_content_hash_observed` | digest of the corpora actually built, filled by stamp_corpus_hashes; differs from `corpus_content_hash` exactly when a resume served a moved corpus |
| `corpus_content_hash_by_arm` | per-arm digests, so a reader sees WHICH arm's corpus moved, not only that one did |
| `completed_at_utc` | when the run finished; absent on a crashed run, which is the signal that it did not finish (`created_at_utc` records the start) |
| `resumes` | one appended copy of each later invocation's knobs; the top level keeps the ORIGINAL run's, so this is the only record of what the earliest rows were scored under (read by index._resume_drift) |

### Mode-specific (present in one mode only)

| field | meaning |
|---|---|
| `db_id` | single mode only: the one pinned schema, kept beside `db_ids` for readers and artifacts that address a single-schema run by its schema |

## 2. Arm summary — the rates and their denominators

The recurring defect class in this harness is a rate whose denominator silently
absorbs another outcome's failures. Over all rows, an arm that refuses 8 of 10
reports the *best* graded-delivery rate and the *worst* safety-clearance rate,
because refusing is neither delivering nor clearing — so a rung that refuses more
looks like a rung that governs better. Naming the population is what makes that
reviewable, and a test asserts every declared rate names one.

| rate | meaning | denominator |
|---|---|---|
| `ex_lenient` | EX over all scored rows, twins included: the figure comparable to published BIRD numbers. Reported, not the headline (see HEADLINE_RATE) | all scored rows (n) |
| `ex_strict` | EX under the strict normaliser | all scored rows (n) |
| `ex_gradeable` | EX excluding un-gradeable gold | gradeable rows |
| `ex_twin` | EX where the gold statement exists in train | twin rows |
| `ex_no_twin` | EX on rows with no train twin: the PRE-REGISTERED HEADLINE, the one number this harness commits to in advance (HEADLINE_RATE) | twin-free rows |
| `conditional_ex_lenient` | EX among turns that produced SQL | rows that produced SQL |
| `cond_ex_given_routing` | EX among correctly-routed turns | rows the router hit |
| `refusal_rate` | GENUINE refusals; a crash is not a refusal | all scored rows (n) |
| `crash_rate` | our bug, counted apart from refusals | all scored rows (n) |
| `decoy_touch_rate` | predictions touching a suspect column | rows that produced SQL |
| `safety_clearance_rate` | delivered answers that cleared the guardrails | delivered rows |
| `graded_delivery_rate` | delivered answers served as unverified | delivered rows |
| `coverage_best_effort_rate` | answers delivered on partial coverage | delivered rows |
| `routing_recall` | the gold schema survived into `routed_schemas` — the set the turn was licensed against. NOT the retrieval channel's recall, and NOT independent of the picker: under `route_llm_pick=True` the serve path sets `routed = frozenset([picked])`, so `routed_hit` IS `pick_hit` and this rate equals `schema_pick_accuracy` BY CONSTRUCTION, to the last decimal place, on every arm of every such run (checked row-by-row on all 1351 rows of the 2026-07-31 ladder). Read `shortlist_recall` for what retrieval actually surfaced. Kept under this name and this definition because published artifacts quote it | rows with a recorded routing decision |
| `shortlist_recall` | the gold schema was in the shortlist retrieval produced, before the LLM picker narrowed it to one (`gold_schema_rank is not None`). The retrieval channel's own recall, and the term `routing_recall` cannot report while the picker collapses the routed set to a single schema: 0.952 against a pick accuracy of 0.873 on the 2026-07-31 curated arm, so two thirds of the routing loss is the picker discarding a schema retrieval had already found | rows that recorded a shortlist (bypassed and crashed turns excluded, as for routing_recall) |
| `routing_escape_rate` | SQL reached outside the routed schemas | rows where escape was observable |
| `routing_degraded_rate` | the embedding channel failed and the ranking fell back to BM25; None (not 0.0) when no turn recorded a channel, because a run that measured nothing must not read as a run that degraded nowhere | rows where a routing channel was recorded |
| `schema_pick_accuracy` | LLM picked the gold schema | rows that recorded a pick |
| `schema_pick_accuracy_excl_fallback` | …excluding picker fallbacks | picks that did not fall back |
| `share_with_a_note` | turns that received at least one note | all scored rows (n) |

### Conditional diagnostics — which part of the governance is doing the work

Each of these reports a rate on **both sides** of something the run produced. Every
input was already recorded per row and aggregated against nothing until 2026-07-28.
They are within-arm, so they cost no extra serve and apply retroactively to any
existing `generations.<arm>.jsonl`.

**All of them are observational.** None is a randomised contrast, so none may be read
as the effect of the thing it splits on: two of them condition on an output of the
system itself (post-treatment selection across arms), two split on whether retrieval
matched (corpus coverage, not note value), and one compares questions that already
failed against questions that did not (two difficulty populations). Each declaration
below carries the specific caveat.

Each block carries its own `n_*` counts, and where a row can fail to record the
input, an `n_unstamped` count — an absent input is counted out, never filed on the
negative side. That is the trap the twin strata already document: `not r.get(...)`
puts an ABSENT key in the FALSE stratum, which silently turns one side of a split
into the pooled figure.

| block | meaning | denominator |
|---|---|---|
| `ex_by_semantic_assurance` | EX per assurance level — the calibration of the semantic axis. If `unflagged` does not out-score `heuristic`, the stamp is decoration. OBSERVATIONAL: the split is on an output of the system itself, so this is within-arm calibration and post-treatment selection ACROSS arms — comparing one arm's `unflagged` EX to another's compares differently-selected populations. `n_unstamped` counts rows that recorded no level; they are excluded, never filed under a `None` level beside the real ones. | rows that recorded an assurance level |
| `ex_by_tier` | EX per display tier — the same calibration for the compact projection, and OBSERVATIONAL in the same way: the tier is the system's own output, so the strata are within-arm calibration, not an across-arm contrast. `n_unstamped` counts rows that recorded no tier, excluded rather than bucketed as `None`. | rows that recorded a tier |
| `decoy_touch_by_caveat` | decoy-touch rate with vs without an injected suspect caveat — whether the caveat is what stops the model reaching for the decoy. OBSERVATIONAL: the split is on whether retrieval matched, so a difference is confounded with which questions the corpus happens to cover. | delivered rows that recorded a caveat count |
| `ex_by_note_injected` | EX with vs without an injected note (ADR 0003's claim, previously unscored). OBSERVATIONAL: the split is on whether retrieval matched, so it measures corpus COVERAGE of the questions, not the value of a note. | rows that recorded a note count |
| `ex_by_repair` | EX after a repair (>1 run_query attempt) vs first-attempt — whether self-repair recovers correctness or just produces valid-but-wrong SQL. OBSERVATIONAL: the `with` stratum is by construction the questions that already failed once, so the two sides are different difficulty populations and the gap is not the cost of repairing. | rows that recorded an attempt count |
| `guardrail_cost_ceiling` | CEILING on answers a guardrail block may have cost, not the cost: blocked SQL cannot be graded without executing un-guardrailed SQL. Counts turns where a layer blocked and the turn still ended wrong, out of `n_observed` turns that recorded a `by_guardrail_layer` map at all — a run whose serve path never stamped one has `n_blocked == 0` for want of instrumentation, which without `n_observed` reads as a run that blocked nothing. Note that `by_guardrail_layer` creates a key at 0 when a layer is merely evaluated, so blocked means `any(v > 0)`, never a truthiness test on the dict. | rows where at least one layer blocked |

### Counts

Each count exists so an exclusion from a rate above stays visible: a rate
reported without its excluded count reads as full coverage.

**counts** — `n`, `n_answered`, `n_correct`, `n_refused`, `n_crashed`, `n_missing_gold`, `n_gradeable`, `n_gold_unusable`, `n_frozen_gold`, `n_order_sensitive_gold`, `n_twin_gradeable`, `n_no_twin_gradeable`, `n_twin_unstamped`, `n_gold_twin_in_train`, `n_decoy_touch`, `n_wrong_but_nrows_match`, `n_unmapped_refused_by`, `n_with_difficulty`, `n_with_governance_stamp`, `n_tables_used_unresolved`, `n_rows_no_db_id`, `n_pick_fallback`, `n_routing_observed`, `n_routing_bypassed`, `n_routing_crashed`, `n_shortlist_hit`, `n_shortlist_observed`, `n_routing_unrecorded`, `n_routing_escaped`, `n_routing_escape_observed`, `n_routing_channel_observed`, `n_routing_channel_embedding`, `n_routing_channel_bm25_fallback`, `n_routing_channel_none`, `n_routing_degraded_observed`, `n_routing_degraded`, `n_routing_escape_unknown`, `n_correct_routed`, `n_correct_unrouted`, `n_correct_bypassed`, `n_correct_routing_crashed`, `n_correct_routing_unrecorded`, `n_correct_via_routing_escape`, `n_correct_unaccounted`, `n_safety_clearance_observed`, `n_graded_delivery_observed`, `n_coverage_best_effort_observed`, `n_notes_observed`, `n_correct_with_empty_gold`, `n_correct_and_pred_has_no_from`, `n_correct_and_zero_table_overlap`, `n_tables`, `n_columns`, `max_table_columns`

### Means and breakdown blocks

**means** — `mean_attempts`, `mean_context_chars`, `mean_ledger_len`, `mean_notes_injected`, `mean_few_shots_injected`

**blocks** — `arm`, `question_ids`, `treatment`, `cost`, `tool_calls`, `errors`, `by_db`, `by_difficulty`, `by_outcome`, `by_failed_stage`, `by_error_type`, `by_guardrail_layer`, `by_tier`, `by_semantic_assurance`, `by_gold_rank`

## 3. Generation row — one record per (question, arm)

**identity** — `arm`, `question_id`, `request_id`, `run_id`, `turn_id`, `db_id`, `split`, `difficulty`, `serve_attempt_utc`

**verdict** — `correct`, `correct_strict`, `error`, `error_type`, `outcome`, `failed_stage`, `failed_layer`, `refused_by`, `nrows_match`

**prediction** — `generated_sql`, `pred_nrows`, `pred_ncols`, `gold_nrows`, `attempts`, `tables_used`, `tables_used_unresolved`, `n_tables_used_unresolved`, `licensed_tables`

**governance** — `tier`, `safety_clearance`, `semantic_assurance`, `graded_delivery`, `coverage_best_effort`, `decoy_touch`, `by_guardrail_layer`, `ledger_len`, `governance_ledger`, `n_tool_calls`

**context** — `context_chars`, `context_hash`, `injected_note_ids`, `n_notes_injected`, `n_caveats_injected`, `n_few_shots_injected`, `n_joins_injected`, `n_metrics_injected`, `n_terms_injected`, `retrieved_tables`, `n_columns_omitted`

**routing** — `routed_schemas`, `routed_hit`, `routing_bypassed`, `routing_escaped`, `routing_escape_unknown`, `schema_pick`, `schema_pick_fallback`, `pick_hit`, `shortlisted_schemas`, `total_schemas`, `schema_route_channel`, `schema_route_degraded`

**width** — `gold_table_max_columns`, `n_schema_tables`

**leakage** — `gold_twin_in_train`, `gold_frozen`, `gold_order_sensitive`, `gold_schema_rank`

**oracle** — `oracle_rung`, `oracle_applied`, `oracle_gold_tables`, `oracle_corpus_tables`, `oracle_offered_tables`, `oracle_padding_degenerate`

**cost** — `latency_sec`, `cost_est_usd`, `usage`, `token_usage`, `token_sum`

**provenance** — `prompt_set_hash`, `prompt_variants`

## 4. Stage events — one record per (question, arm, stage)

`stage_events.jsonl`, pooled driver only, flattened from the serve path's own
`stage_events` provenance. A separate file rather than row fields because a turn
emits many of these and the row is already the widest artifact.

**fields** — `question_id`, `arm`, `db_id`, `run_id`, `turn_id`, `stage`, `status`, `ms`, `detail`

## 5. Split gap — `train - test` per arm

`split_gap.json`, written only under `--split both`. Scoring the train split is not
a second result (`index.quotable` refuses a train-scored run); the *gap* is how much
of an arm's score does not survive being asked something new. Not paired and not
significance tested — a within-arm diagnostic, never a headline.

The gapped rates are a chosen subset of the rates above: every one is accuracy-like,
so "train is higher" means "did not transfer". Gapping `crash_rate` or
`refusal_rate` would invite reading operational noise as overfitting.

**gapped rates** — `ex_lenient`, `ex_strict`, `ex_gradeable`, `conditional_ex_lenient`, `cond_ex_given_routing`, `routing_recall`, `schema_pick_accuracy`

**file fields** — `reading`, `arms`, `arms_not_in_both`, `train_dir`, `test_dir`, `error`

## Regenerating this page

The tables and every count above come from the register. After editing
`src/governed_bi/eval/metrics.py`, re-run the generator and commit both:

```bash
uv run python scripts/gen_eval_metrics_doc.py
```

CI runs `--check`, which writes nothing and fails if this file is not what a fresh
generation would produce.
