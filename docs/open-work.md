# Open work

The single tracker for work that is **open**. It replaces four dated trackers
(`engineering-gaps-2026-07-16`, `eval-audit-backlog-2026-07-22`,
`clarification-sme-benchmark-build-plan`, `implementation-plan-notes-and-run-logging`),
whose closed items now live only in git history. Nothing here is a design
record — decisions belong in [design-decisions.md](design-decisions.md) and
[adr/](adr/).

**This file is the inventory; [plans/rebuild-checklist.zh.md](plans/rebuild-checklist.zh.md)
is the order.** Five analyses on 2026-07-29/30 (module depth, reference-book fidelity,
framework best practices, multi-turn, governance red team, corpus drift) produced 62 items,
deduplicated to 41 in [plans/build-sequence.md](plans/build-sequence.md); a grill on
2026-07-30 then reordered them into eleven cross-cutting items plus four parallel tracks
([decisions](plans/rebuild-decisions.zh.md)). Items are not copied here — read this file for
*what is broken*, the checklist for *what to do next*, `build-sequence.md` for *which
analysis found it*.

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
| C10 | `curator_trace.jsonl` / `curator_sme_trace.jsonl` are written at the arm root but are not in `_SIDECARS`, so `_relocate_sidecars` never promotes them and `_promote_build` deletes the staging root holding them. The pooled driver therefore keeps the derived counts (`tool_calls.repeats`, `n_tool_calls`, `n_steps`) and loses the verbatim argument list, which is the only artifact that says *what* a capped agent looped on. The single-schema driver keeps it. | `eval/run_datalake.py:_SIDECARS` |
| ~~C11~~ | ~~**Fixed 2026-07-30.**~~ The oracle rungs wrote **answer-key-derived turns into the durable run log**. `oracle_solver` passed `settings` through without `run_log_kind="off"` (which `arms.py:430-434` does do), so every oracle turn landed as a row stamped `producer=serve, serve_path=agent`, with `oracle_rung` living only in the eval `meta` and never in provenance, indistinguishable from a real serve turn except by a `thread_id` prefix convention. `oracle.py:55-58` says these can never be reported as system performance. **Fix:** `oracle_solver` gained `enable_run_log: bool = False` and routes `dc_replace(settings, run_log_kind="off")` into `build_serve_rails`, mirroring `arms.agent_solver` rather than inventing a second pattern. Regression test `test_oracle_rungs_stay_out_of_the_durable_run_log` in `tests/test_eval_run_log_turns.py` is parametrised `(False, 0)` / `(True, 2)`; the `True` leg is a control proving the write path was live, so the `False` leg's zero is suppression and not a vacuous pass. Verified failing before the fix (`assert 2 == 0`). Confirmed `run_log_kind` is the complete guard: the only durable serve sink is `finalize_and_log → append_run_record`, which short-circuits on `kind == "off"` (`analyst/run_log.py:503-505`), and the conversation checkpointer is never wired on the eval path. | `eval/oracle.py` |
| C12 | **The refuse-gate eval collapses an N-question run into one durable row.** `agent_refuser` builds a fresh graph per question and defaults `n_human`, so `turn_id == f"{session_id}:1"` every time and `append_run_record` UPSERTs over it. It is the one serve call site that got neither the per-invoke turn counter (`test_eval_run_log_turns.py:60` pins it for `arms`) nor the AUDIT R6 index-cache fix, so it also re-embeds the whole corpus per question. Latent while X6 keeps the scorer unwired, but it will bite the moment a real out-of-scope set exists. **Added 2026-07-30 while fixing C11:** the same call site also passes `settings` to `answer_question_agent` with no `run_log_kind` guard, so it has C11's defect too. `arms.py:433` and now `eval/oracle.py` both guard it; this is the last unguarded serve call site on the eval path. Fold into this item rather than opening a third. | `eval/refuse_gate.py:71-80` |
| C13 | Unqualified bare table names resolve to **whichever schema loaded first**. Three copies of "table by id, falling back to physical name" (`analyst/tools.py:38`, `analyst/middleware.py:118`, `analyst/agent.py:465`) take the first match in `corpus.assets` order; `retrieval/rvgd.py:530-538` already implements the correct policy for the same lookup ("rather than to whichever table happened to be loaded last") and returns `None` on ambiguity. Measured on `BIRD-corpus` at HEAD: **27 ambiguous bare names covering 67 of 731 table assets (9.2%)** — `pais` ×5, `kunden` ×4. Reachable without an adversary: the agent reads `physical: sales.kunden` from `render_columns`, calls `sample_rows("kunden")`, and is told `tbl_beer_factory_kunden: not licensed this turn` — a table it never named, in a schema outside its routed scope, whose name the message leaks. Costs a step, can dead-loop to a step-cap refusal that scores as an **agent** failure, and flips with the order of `built` in `_load_built_corpus`. No lookup accepts the qualified form either, though that is the form the context block and `render_columns` both print. **Fix:** one `Corpus.table_by_name` that accepts the qualified form and returns `None` on an ambiguous bare name, replacing all three copies — `rvgd.py` already has the policy, it just is not shared. Ship it with a `Corpus.concat` constructor or the index goes stale on the pooled path. | `analyst/tools.py:38`, `analyst/middleware.py:118`, `analyst/agent.py:465` |
| ~~C14~~ | ~~**Fixed 2026-07-30.**~~ `read_corpus(todo_only=True)` bypassed the `READ_CORPUS_MAX_CHARS` (20k) cap: the normal render ended with `return _clip_render(lines, max_chars)`, but the `todo_only` branch returned early via `return body if body else "..."` with no clip. On a wide schema the worklist render was enormous: `works_cycles` (73 tables / 703 columns) renders **668 KB**, far past the ~20k deepagents `tool_token_limit_before_evict`, so the result was evicted to a file and the agent burned extra turns reading it back. This re-created, on the one path the fix didn't cover, exactly the read_corpus-eviction churn that `079d1fe` set out to kill, and `todo_only` was *added by that same commit* as the shrinking-worklist remedy. Observed live in the 20260730T031119Z fixed-code test build: curated `works_cycles` wrote 0 durable assets in ~18 min while repeatedly regenerating 668 KB / 212 KB `todo_only` dumps, wedging the whole build at 54/57. **Fix:** the `todo_only` early return now routes through `_clip_render(lines, max_chars)`; regression test `test_todo_only_render_is_also_bounded` in `tests/test_curator.py`. Filed as C11 on the run server, where that id was free; renumbered here because C11 was already taken by the oracle run-log finding above. | `curator/asset_bag.py` `read_corpus` (`todo_only` branch), cap at `:47` `READ_CORPUS_MAX_CHARS` |

| C15 | **`note-excluded-identifier` (C5) has the same pooled-population bug F7 just fixed.** `_excluded_identifier_tokens` collects excluded physical names from *every* table in the pooled corpus, then scans *every* note against all of them, so a note about schema B that mentions `phone` is flagged because schema A excludes a column named `phone`. Latent rather than live: measured against the 20260730 `curated_sme` corpus there are **0** excluded tables/columns, so the check is inert there and flagged nothing. The fix is to scope tokens per schema and match a note only against tokens from the schemas its scope licenses, globals against all. Deferred because it needs a decision about `tests/test_notes_c5_withholding.py`'s contract. **Compounded 2026-07-30:** `validate.py:265` scans `note.body` as well as the summary, and `note-excluded-identifier` is a **hard** finding (`adversary.py:42` blocks the write), so it aborts a schema build. Caveat notes now carry the full SME answer in `body` (F5 fix), which widens the text this pooled check scans. Still cannot fire on BIRD runs because nothing in the curator or eval path sets `governance.excluded`, but on a hand-authored corpus with exclusions a cross-schema false positive would now abort a build rather than be silently discarded. Fix the pooling before anyone relies on exclusions. | `corpus/validate.py:259-279` |
| C18 | **Nothing bounds the size of a rendered train pair except a pair *count*.** BIRD-Obfuscation rewrites some gold as a literal `VALUES` list: 48 of 5392 train pairs carry `sql_rename` over 2000 chars and the largest single pair (`video_games/train_3491`) is **2,527,929 chars**, roughly 630k tokens, larger than any context window in play. Until 2026-07-30 the 40-pair render cap was the only thing standing between that and the prompt, and it was already failing: the worst first-40 render is **323,403 chars** (`language_corpus`), re-sent on every turn of the agent loop, and 19 of 57 schemas would exceed 60k chars on a full render. Mitigated by the F1 intake fix, which clips each rendered statement at `MAX_RENDERED_SQL_CHARS` (2000) with an announced marker, bringing the widest single batch render to 43,848 chars. **Still open:** the clip is a curator-side render guard only. No other consumer of `sql_rename` bounds it, and nothing rejects or flags a 2.5 MB gold statement at dataset-load time, so the next path that renders gold inherits the same hazard. | `curator/pipeline.py` `MAX_RENDERED_SQL_CHARS`, `eval/bird_loader.py` |
| C17 | **The suspect-note character cap binds only the path that does not dominate.** `_suspect_note_from_answer` clips `mark_unrecognised_columns` output at `_SUSPECT_NOTE_MAX_CHARS` (200) and discards the remainder, justified as protecting the per-turn schema-card budget. But the agent's own `annotate_column(note=...)` is **unbounded** on the same field: the 20260730 run carries reliability notes up to 619 chars across 2104 suspect columns, against 47 clipped by the mechanical backstop. So the budget rationale is enforced on the minority path and absent from the majority one. Unlike the caveat case there is no free carrier to move the tail into (`Reliability` has only `status` and `note`, both per-turn card text), so the fix is a decision about which cap is real, not a mechanical change. Every clipped answer does survive in full in `<schema>/_build/clarifications.jsonl`. | `curator/asset_bag.py` `_suspect_note_from_answer` |
| C19 | **`events.final` appends the durable record before `narrate` runs**, so the stored row lacks the `narrate` stage whenever no narrator was passed. The re-append only happens on the narrator-ran path — which is the path the eval drivers never take, so every eval row is missing it. Append after narration, or record the stage unconditionally. | `analyst/agent.py:1265-1268` |
| C20 | **`run_id` is a parameter the code discards**: `ingest` overwrites it unconditionally, so a caller that passes one is silently ignored. Delete the parameter or stop overwriting it. | `analyst/agent.py:518-525` |
| C16 | **Pooled validation makes `dangling-ref` weaker, not stronger.** A note scoped `schema:X`, or a reference to a table id living in another schema, resolves fine in a 57-schema pool but would dangle in that schema's own corpus. This direction produces no false positives, so it is not urgent, but it means a green pooled `finding_count: 0` is **not** evidence of per-schema reference integrity, and the CI-green gate is quietly weaker than it reads. Found while fixing F7. | `eval/harness.py:125-140` |

## Efficiency

| # | What | Where |
| --- | --- | --- |
| E1 | Cross-check re-executes gold **and** prediction per item per arm, though gold is arm-invariant. Memoise the gold hash per `question_id`. | `eval/run_experiment.py` → `eval/ex.py` |
| E2 | Each corpus is loaded from disk twice — once for the solver, once by `_suspect_from_corpus`. Both drivers. | `eval/harness.py` |
| E3 | `profile_database` runs twice per db (baseline and curated each profile independently). | `curator/pipeline.py` |
| E4 | Baseline is rebuilt unconditionally on `--resume-curated`; `run_datalake` already guards with `_has_yaml`. | `eval/run_experiment.py` |
| E5 | The gold self-check opens a fresh schema-pinned connector per sampled db, separate from the shared unpinned serve connector. | `eval/run_datalake.py` |
| E6 | **`schema_vectors` is passed by nothing**, so on a multi-schema corpus every live turn re-embeds every schema document, because the API paths rebuild the graph per turn. `index_cache` cannot cover it: `schema_router.py:224-231` short-circuits on `schema_vectors` *ahead of* the cache branch, so the stack's cache never sees the call. | `retrieval/schema_router.py:224` |
| E7 | **`licensed_physical_names` is re-derived on every `run_query` attempt** as well as twice per question: ~29,000 asset visits per question through one 8-line function, for a value that changes only when `inspect_schema` licenses something. Memoise per licensed-id set. | `analyst/middleware.py:84` |
| E8 | **`_excluded_identifier_tokens` is uncached** and visits 731 assets plus all 6,877 columns per call, once per `render_notes` / `read_notes` / `grep_notes`, for a pure function of the corpus. | `analyst/tools.py:148`, `:397`, `:420` |
| E9 | **`oracle_tables` re-embeds a large corpus per question**, and a corpus-keyed cache cannot fix it: the key is the sorted asset-id tuple and the gold table set differs per question, so every lookup is a guaranteed miss. `restrict_corpus` also keeps every term, note and negative-example asset whole, all three with non-blank documents. Needs a per-document embedding memo. | `eval/oracle.py:264` |
| E10 | **`run_datalake` compiles a serial solver's graph the pooled path never uses**, paying one full schema-document embed per arm. Build it lazily. | `eval/run_datalake.py:4636-4668` |
| E11 | **`rvgd.py:597` calls `corpus.by_id` T times** while the local id→asset dict built at `:488` is in scope, and the comment at `:485-487` explains precisely why that dict exists. | `retrieval/rvgd.py:597` |

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
| ~~X11~~ | ~~**Resolved 2026-07-30.**~~ `metrics.py` labelled `ex_lenient` "headline execution accuracy" AND `ex_no_twin` "the defensible headline", with nothing pre-registering which one is *the* number. **Fix:** `metrics.HEADLINE_RATE = "ex_no_twin"` is now the single declaration, `ex_lenient` is explicitly demoted to "Reported, not the headline", and `test_exactly_one_rate_calls_itself_the_headline` encodes the defect so it cannot silently regress. Chosen because the twin-free stratum does not depend on the 115 of 1200 questions (9.6%) whose gold has a structural train twin, stamp coverage is complete (`n_twin_unstamped: 0`), and the ladder holds at +18.71pp twin-free against +19.25pp raw. The hazard was concrete, not theoretical: on `ex_no_twin` the SME arm slightly *exceeds* `curated` (0.594 vs 0.591) while on `ex_lenient` it sits slightly below, so metric choice flips the sign of a reported delta. Both are far inside noise; neither is a result. | `eval/metrics.py:607` |
| X12 | **The curator's derived step budget and `curator_phase_a@v2` are both unmeasured.** The budget replaced a constant that capped 30 of 57 Phase A agents ([Curator](curator.md#the-step-budget)); `v2` is registered and `v1` is still the default. Testing either means a rebuild, and the cap rate has to be read next to suspect coverage per column and `decoy_touch_rate`, because `v2` buying completion by curating less would look like a win on the cap rate alone. | Every curated-arm number to date comes from a build whose reliability sweep may never have run, so a curated-vs-baseline delta is confounded with how far the agent got. |

X8–X11 are reporting-side and cost no extra serve pass. X12 needs a rebuild.
Run-to-run variance is X4, not a separate item.

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

## Serve-time clarification (HITL)

The contract is agreed, the server implements it and the frontend renders it, so its
plan doc is deleted and the contract now lives in
[Analyst](analyst.md#serve-time-clarification-hitl). Four things it left open.

- **The `ask_user` timeline row carries almost nothing.** `_tool_start_detail` emits
  `{question, why}`, and the resolve has **no branch at all** — it falls through to the
  generic `events.tool(step, "ok")`. So `clarification_id` never reaches the stream
  (the design made it the join key across interrupt, resume, timeline and provenance;
  on the wire the UI pairs by tool-call id instead), `answered_by` is never emitted, and
  **a declined clarification resolves as `ok`**: the fail-closed refusal appears only in
  the final stamp as `refused_by: clarification_declined`, never on the row that caused
  it. `governed-bi-ui`'s `StepStatus` union already carries a `declined` value nothing
  on this side sends. Found 2026-07-30 while verifying the contract against the code —
  the deleted plan's §5 table described the intended payload, not the built one.
- **Durable clarification is deferred.** The clarify checkpointer is in-memory and
  per-process (`ServeStack.clarify_checkpointer`), so a paused turn dies on server
  restart and a declined turn leaves the inner thread paused in memory until GC or
  thread reuse. Needs the Postgres checkpointer — ADR 0002's deferred item.
- **Resume re-runs the deterministic prefix.** The whole pipeline sits in one `answer`
  node, so on resume route/refuse_gate/assemble re-execute while the inner agent
  replays from its checkpoint. Never confirmed to be acceptable rather than merely
  harmless; the alternative is lifting the prefix into graph nodes, which W2's
  `ServeRuntime` would make cheap.
- **`ask_user` versus `recursion_limit` is unaudited.** An interrupt pauses without
  consuming a super-step, but the resumed tool round-trip does. The cap accounting was
  never checked, so a clarifying turn's real step budget is unknown.

- **`api/app.py` omits `clarify_checkpointer` entirely**, so `enable_clarify` is False
  and `ask_user` is not bound at all. The REST `/chat` agent has a different tool set
  from the streaming path, and nothing in provenance records that clarification was
  unavailable. Decide whether REST should clarify; record the capability either way.

## Governance gaps

- **The graded-delivery re-check is weaker than the check that blocked the query.**
  `analyst/governance.py:696` re-runs `check()` before the second
  `gateway.execute` at `:720` with `allowed_tables=None`, which skips **L4
  term-semantics entirely**, and `:708` lets an L5 `cost_estimate` re-check
  *failure* fall through to execution as well. Verified by running `check()`
  directly on a two-schema pooled allowlist: a query blocked as "table outside the
  retrieved scope" passes the re-check, and so does `SELECT COUNT(*) FROM
  pg_catalog.pg_authid` (no `Column` nodes, so L3 has nothing to reject). The
  trigger needs no adversary — ask something whose answer needs an un-routed
  schema and let that be the turn's last `run_query`; `governance.py:737` then
  narrates the real rows behind an `(unverified)` prefix. The L4 skip is a
  designed trade-off and the comment at `:677` says so; what is not covered by
  that design is the L5 fall-through, and the surrounding prose reading as if the
  re-check were equivalent. Bounded by `grade_semantic_failures=False` being the
  serve default — but it is `true` in `governed_bi.local.toml` and on in both eval
  drivers, and on the 69-schema pooled lake it is a cross-schema read of
  un-licensed data, which is the boundary D15 exists to enforce. What would make the
  asymmetry visible rather than buried: make the guardrail verdict a *scoped* value —
  a token carrying the allowlist it was checked against, so re-checking with
  `allowed_tables=None` cannot silently mean "checked" — and make the checked tree the
  executed tree, which also closes `_force_row_limit` re-serialising under a hardcoded
  dialect while `check()` parses under `gateway.catalog().dialect`.
- Curator probe SQL (`curator/deep_agent.py:118`, `curator/sme.py:355`) reaches
  `gateway.execute` under an `all_access` identity with no guardrail at all —
  defensible, since the curator is what *builds* the allowlist, but L1/L2 need no
  allowlist and would still catch `pg_read_file` / `dblink`. Either run those two
  layers on probes or carve the exception out of [architecture](architecture.md)
  §1's "executes only guardrail-passed SQL".
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

Two plan docs were **deleted on 2026-07-30 because their work is finished**, backend
and frontend both: the agent step timeline and the HITL clarification contract. Neither
is a gap. What each one specified now lives in [Analyst](analyst.md) — the
[event contract](analyst.md#the-event-contract-per-step) and
[serve-time clarification](analyst.md#serve-time-clarification-hitl) — because shipped
code and ADR 0002 cite them as live interfaces, not as plans. The residue that was
genuinely still open is the HITL section above.

The **module deepening** plan was deleted the same day, and unlike those two it was
deleted **unstarted** — none of its seven workstreams shipped. That is a decision to
stop tracking a refactor, not a claim that the refactor happened. Its findings that
stand on their own are folded in above: C13 and C19–C20 in Correctness, E6–E11 in
Efficiency, the guardrail-scoping note under Governance gaps, and the REST clarify gap
under HITL. The workstream designs themselves are in git history.
