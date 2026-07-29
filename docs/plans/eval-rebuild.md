# Eval rebuild: tracking plan

All prior BIRD eval numbers are discarded. Section 1 says why. The rest tracks the
four fixes that follow, the artifact cleanup, and the order to do them in. The
general tracker is [open-work.md](../open-work.md); the driver this eval runs on is
[datalake-run.md](datalake-run.md).

## 1. All prior eval results are discarded

Not "under review". Discarded. Every EX, decoy-touch and routing-recall figure ever
attributed to a BIRD run. Five reasons, each verified independently.

**BIRD's own gold SQL is wrong on some questions.** Two proven by execution against
the untouched original SQLite (`data/train/train_databases/address/address.sqlite`
in the sibling `BIRD-Data-Obfuscation` repo):

- "What is the number of households in the FL-10 district?" has gold SQL
  `SUM(CASE WHEN T2.district='FL-10' THEN 1 ELSE 0 END)`, which returns 59. That is
  exactly the `COUNT` of zip rows in the district. The intended `SUM(households)`
  returns 346,317, and `zip_data.households` exists and is populated. Off by 5,870x.
- An Atmore/asian-population question where BIRD's own `evidence` field says
  `Divide(asian_population, population_2020)` while its gold SQL divides by
  `population_2010`. Gold 0.5061 against a correct 0.5176. BIRD's own authoritative
  hint contradicts its own answer key.
- This is not a convention we misread. `train_5082` in the same database computes the
  same measure correctly with `SUM(T1.households)` = 36,526.

**The graded split inherits the same annotation pool.** 1,723 of 2,030
`test_final.jsonl` rows (84.9%) appear verbatim in BIRD `train.json`; `train_final`
is 85.0%. There is no clean held-out half.

**Bad gold SQL poisoned the corpus.** `_mark_columns_absent_from_gold` marks a column
suspect when no train gold SQL references it. In `address`, `persons_per_household`
has 0 train references and was marked suspect, so a real demographic column is
flagged unreliable. `households` survived only because 4 other queries happened to
reference it. 26 of 58 `zip_data` columns are suspect.

**Every SME note fired on every question in its schema.** All 162 notes were
`activation: always` with empty `triggers`, so a note clarifying one question was
injected into every question in that schema.

**The SME prompt contradicted itself and destroyed answers.** `sme_rules` said "Never
write database queries" while the runtime user message said "You may run read-only
probe queries." 11 of 381 real answers (2.9%) were truncated to a fallback string by
the SQL sanitiser. They cluster on decoy-confirmation questions (`card_games`,
`restaurant` and `world` at 25% each), which are the ones the curator most needed
answered.

### One retraction

An earlier estimate in this investigation put the BIRD evidence/SQL contradiction
rate at 11% of graded rows. That figure is retracted. Hand-checking found the
dominant flagged pattern, `evidence` writing `COUNT(col)` while the SQL writes
`COUNT(*)`, is an equivalent paraphrase rather than a defect. The true rate is
unmeasured and needs per-question adjudication. Do not cite 11% anywhere.

### Verified good, so do not re-investigate

The Simulated SME's addressing is correct across all 69 schemas: 0 identifiers absent
from the physical schema, 0 traps or decoys described as real, 0 nonexistent table
headings, column coverage median 100% and minimum 80%. The SME does not fabricate on
decoys either; 8 of 8 adversarial framings declined and pointed back to the real
column. 381 clarifications across 65 schemas leaked no test material at all.
`load_trap_columns` is correctly schema-qualified, and `_split_suspect_refs` already
prevents bare-name over-counting.

## 2. Artifact cleanup

| Path | Size | Tracked | Action |
| --- | --- | --- | --- |
| `runs/` (including `runs/index.jsonl`, the run ledger) | 70M | 0 files | delete, local cleanup only |
| `corpora/` | 15M | 0 files | delete, local cleanup only |
| `data/checkpoints/` | 1.9M | 0 files | delete, local cleanup only |

All three are gitignored with nothing tracked, so removing them leaves nothing to
commit.

The only tracked stale numbers are the schema-routing recall table at
[`datalake-run.md:126`](datalake-run.md) and its two citations, at
[`adr/0003-governed-notes-tri-modal-retrieval.md:90`](../adr/0003-governed-notes-tri-modal-retrieval.md)
and [`design-decisions.md:566`](../design-decisions.md). All three are derived from
the question pool, so the new dataset invalidates them.

Retire the numbers, keep the reasoning. The conclusion they support (embedding-only
routing beats BM25, and beats RRF over weak lexical) is worth keeping, because
deleting it loses real knowledge. Both citations already carry a retirement caveat
pointing at `datalake-run.md`'s status section. Extend that caveat to say plainly
that the conclusion is now unevidenced pending re-measurement on the new question
pool, not merely pending re-measurement in general.

## 3. The four work items

### D1: notes must carry triggers

Highest priority, fix immediately.

162 of 162 notes are `activation: always`, empty `triggers`, `schema:`-scoped. The
`always` branch of `select_notes_for_injection`
(`src/governed_bi/analyst/note_inject.py:213`) never consults the question. Question
relevance only enters the `on_match` branch, via `matched_ids`
(`note_inject.py:222-223, 236-238`).

There is a single root cause. `AssetBag.record_caveats`
(`src/governed_bi/curator/asset_bag.py:620`) is the only note producer, and
`AssetBag.propose_note` (`asset_bag.py:586`) accepts no `triggers` or `activation`
parameter, so `NoteKind.context`'s default of `always` always wins.

A second defect sits in the same area. `apply_always_budget` (`note_inject.py:179`)
applies `global_max` (default `DEFAULT_ALWAYS_NOTE_GLOBAL_MAX`) only to notes with
empty scope (`is_global = not note.scope`, `note_inject.py:197`). Every note is
schema-scoped, so none of them is capped by it. The only remaining gate is
`char_max` (`DEFAULT_ALWAYS_NOTE_CHAR_MAX`), and the eviction order
`_precedence_key` (`note_inject.py:158`) contains no relevance term, so which notes
get dropped over budget is arbitrary with respect to the question being asked.

The decision: `propose_note` gains `triggers` and `activation`. `record_caveats`
derives triggers deterministically from quoted literals and identifiers in the
clarification text, sets `activation=on_match` when it finds any, and keeps `always`
when it finds none. Emit keyword triggers only, since ADR 0003 defers regex and
`fire_triggers` leaves regex triggers inert. Cap always-notes properly while there.

Done. On the real 2026-07-27 clarifications the 162 notes now split 133 `on_match`
to 29 `always`. `apply_always_budget` counts every always-note against `global_max`
rather than only the global-scoped ones, and `_precedence_key` takes an optional
relevance term that sorts below force and status, so the AUDIT R8 ordering still
holds.

The trigger channel is dead on the eval path, which changes what this bought.
`pin_triggers_enabled` defaults False (`config.py:249`), `governed_bi.toml` had no
`[notes]` table (added since), and both drivers build `Settings.for_env(Environment.dev, ...)`
(`run_datalake.py:3922`, `run_experiment.py:521`) which sets only four fields, so
TOML note knobs never reach it and `fire_triggers` returns an empty list. The
derived triggers are authored correctly and inert at runtime.

Delivery therefore runs entirely through the other channel, which is live:
`retrieval.note_ids`. `asset_document` indexes `NoteAsset.summary` (`rvgd.py:146`),
notes get a `note_k=5` per-type budget (`rvgd.py:511`), and eval runs with an
embedder by default. So an `on_match` note reaches the prompt when it lands in the
semantic top 5 for the question and its scope matches. What D1 bought is narrowing
from "every note in the schema" to "the five most relevant", not trigger-driven
pinning.

**T1: resolved, option A.** `pin_triggers_enabled` is wired through to eval and made
separately measurable rather than bundled into the `curated_sme` arm.

`for_env` could not express any note knob, which is why the channel was unreachable
from a graded run: `load_settings` read a `[notes]` table, the drivers threw that
Settings away and kept only `.models`. It now takes a `NoteGovernance` parameter
object carrying the five knobs, both drivers gained a `--pin-triggers` flag defaulting
off so current behaviour stays the baseline, and `governed_bi.toml` documents the
`[notes]` table for the first time.

The load-bearing half was attribution. `pin_triggers_enabled` was already in
`serve_config_hash`, but the *manifest* carried neither the hash nor the knob, and
`comparable()` reads the manifest — so PIN on and PIN off compared as the same
experiment. That is the third instance of the defect class already fixed for
`llm_temperature` and `question_pool_hash`. All five knobs joined `MANIFEST_KNOBS`, so
they entered the comparability gate through the derivation rather than a fourth
hand-maintained list. `pin_require_certified` and `pin_max` record `None` when pinning
is off, so a manifest cannot claim a gate that never ran.

One thing to carry into any comparison: **PIN has two effects.** It forces a matched
note into the prompt ahead of RRF, and it prepends that note's schema to the router
shortlist. The merge is additive and cannot evict the correct schema, but a PIN
difference can move routing and not only note text.

**Naming drift to clean up.** `always_note_global_max` and
`DEFAULT_ALWAYS_NOTE_GLOBAL_MAX` now mean per-turn rather than global-scoped. The
rename touches `config.py`, which D1 did not own.

### C1: replace the SME rules prompt

`sme_rules` v1 (the default) and v2 both say "Never write database queries. Describe
meaning in prose only." (`src/governed_bi/prompts/registry.py:199, 223`), while the
runtime user message in `SimulatedSme.answer`
(`src/governed_bi/curator/sme.py:407-411`) says "You may run read-only probe queries
to check the data first if it helps." Same call, opposite instructions. The cost is
measured at 11 of 381 answers destroyed.

The decision: delete v1 and v2 outright and write one correct variant. It must permit
the read-only probe tool explicitly, forbid SQL in the answer only (which is what the
sanitiser actually enforces), and instruct that an identifier absent from the brief is
one the SME has never heard of, to be reported as unrecognised with a recommendation
not to use it for analysis, never guessed at from its name.

Fix `_sanitize_sme_answer` (`sme.py:276`) at the same time. It currently keeps only
the lines before the first SELECT-looking line (`sme.py:280-286`), so an answer that
leads with SQL sanitises to empty and falls through to the canned string
`"Unsure — declining to invent a definition; treat this column cautiously."`
(`sme.py:287-289`). Change it to strip SQL blocks and lines from anywhere, keep all
remaining prose, and fall back only when nothing survives.

Two touchpoints. `prompt_set_hash` changes, which is acceptable now that the old
numbers are discarded. And `tests/test_eval_ladder.py:719`
(`assert prompts.variants("sme_rules") == ["v1", "v2"]`) needs rewriting.

To verify: unit tests on the sanitiser with SQL-leading input, since recorded
clarifications are post-sanitisation and the raw output cannot be replayed. Then a
small live run measuring the fallback rate against the 2.9% baseline.

### B6: reliability marks become AI-authored

`_mark_columns_absent_from_gold` (`src/governed_bi/curator/pipeline.py:504`, applied
at `pipeline.py:709`) marks a column suspect when no train gold SQL references it.
"BIRD never queried it" is not "this column is unreliable", and bad gold SQL makes it
worse (section 1).

One fact makes this change safe. Grading uses
`_suspect_from_corpus(roots["baseline"]) | trap`, and baseline corpora carry 0 suspect
refs across 6 real run directories, because `build_baseline_corpus` only profiles. So
the grading target is effectively the ground-truth `trap` manifest alone, and changing
how `suspect` is populated on the curated arms does not move it.

The decision: delete `_mark_columns_absent_from_gold` and its application outright.
Reliability is authored by the curator agent through the mechanism that already
exists. `annotate_column(suspect=True, note=...)` is an exposed agent tool
(`src/governed_bi/curator/deep_agent.py:199`) and it auto-prefixes `"DO NOT USE —"`.
No new schema fields are needed. Strengthen the curator prompt to sweep every table
and column and mark the unreliable ones explicitly. When an SME answer reports an
unrecognised or unreliable column, the fold should record a column-level suspect mark
rather than only a schema-scoped note.

Record the invariant, which already holds and must keep holding: reliability and
suspect are AI-authorable, while `governance.excluded` is human-only. It is enforced
by absence. There is no exclusion tool in the curator's tool list and no reference to
`excluded` anywhere under `src/governed_bi/curator/`.

One prerequisite is not yet met. For a suspect mark to land on a column, the
clarification's scope must be `column:` or `table:`. Today's decoy questions arrive
with `table:` or `pair:` scope, so the curator has to start asking column-scoped
questions.

The risk is real and worth stating. This removes the curated arm's only current decoy
defence, so `decoy_touch_rate` may get worse before it gets better. That is precisely
what the experiment exists to measure, so it has to be measured rather than assumed.
It also moves `corpus_content_hash`, which is expected.

Done. The mask and its `decoy_stats` plumbing are gone; its one consumer, the Phase A
manifest key `decoy_defense`, is replaced by `suspect_columns: bag.suspect_count()` so
a reader who quoted the old key has an honest successor rather than an orphan. The
`curator_phase_a` prompt gained a reliability sweep with named grounds and an
anti-over-marking clause, plus "you cannot exclude". The SME fold gained
`answer_disowns_column` and `mark_unrecognised_columns`, wired into both fold modes.

On the scope-granularity constraint, both options were taken. The fold marks at column
granularity when the scope allows and counts `no_column_in_scope` otherwise, printing
it so the gap is measured rather than hidden; and Phase A now tells the curator to scope
column doubts as `table:T.col` while Phase B tells the agent to mark suspect itself,
because the agent fold path never calls the deterministic fold and option (b) alone
would have left it unbacked.

The human-only exclusion invariant still holds and is now pinned by a test that checks
code tokens rather than prose, plus a comment at the tool list and a line in
docs/curator.md.

Still to verify against a real build: whether the agent actually marks decoys often
enough to replace what the mask did. Until a curated build runs, the curated arm's
decoy defence is untested rather than known-good. `X2`'s `mask_only` ablation is moot
and marked so in open-work.md.

### D2: attribute routing failure

This is offline attribution only. At runtime the correct schema is unknown, and
nothing about runtime behaviour changes here.

Cross-schema note bleed is a routing symptom rather than a note-scoping bug, because
the note selector correctly pulls the routed schema's notes. The attribution still
needs care. With the pooled driver's config (`route_top_k=10`, `route_llm_pick=True`)
the deciding step is `pick_schema`'s LLM choice among the candidates, not shortlist
rank-0, and `routed = frozenset([picked])`. But `pick_schema` falls back to
`candidates[0]`, which *is* rank-0, on both `call_failed` and `unparseable_reply`. So
"the notes came from rank-0" is consistent with two failures that need opposite fixes:
the true schema was absent from the shortlist, which is a retrieval problem; or the
LLM pick failed and silently fell back, which is a problem in the `schema_pick` prompt
and `_parse_schema_reply`.

The bleed rate is also config-dependent. On the defaults (`route_llm_pick=False`,
`top_k=3`) the path is `routed = expand_schemas_via_curated_joins(shortlisted)`, so
the whole shortlist plus curated-join expansion gets licensed and notes from 3 or more
schemas fire on every question. That is structural rather than a misroute symptom. Any
reported figure has to state `top_k` and `llm_pick`.

Step 1 is answered: all three buckets are computable from artifacts we already have,
with no new provenance field and no rerun. The generations row carries
`shortlisted_schemas` in relevance order (`agent.py:640` through `arms.py:492` to
`run_datalake.py:3629`), the picked schema (`schema_pick`, `pick_hit`), the fallback
*reason* string (`schema_pick_fallback`, one of `call_failed` /
`unparseable_reply` / `parsed_nonfinal_line`), `gold_schema_rank`, and the true
schema as `db_id`. So:

| Bucket | Predicate on a generations row |
| --- | --- |
| shortlist miss | `shortlisted_schemas` non-empty and `db_id` not in it |
| picked wrong | `db_id` in `shortlisted_schemas`, `schema_pick != db_id`, no fallback |
| pick fell back | `schema_pick_fallback in {"call_failed", "unparseable_reply"}` |

`stage_events.jsonl` is the weak surface and cannot do this alone: its stage detail
holds only `n_candidates` (a count) and `fallback` (a bool), confirmed on disk.

No existing metric reports the split. `schema_pick_accuracy` is
`n_pick_hit / len(picks)` (`run_datalake.py:2238-2240, 2591`) and conflates all
three; `schema_pick_accuracy_excl_fallback` removes only the fallback bucket;
`by_gold_rank` (`analysis.py:563-609`) separates the shortlist miss but not the
fallback.

Step 2 remains: land the three-way split as a summary metric so it is tracked rather
than re-derived by hand.

**This collides with the artifact wipe.** The only run directories carrying usable
rows are `runs/serial-v1`, `runs/serial-v2`, `runs/smefix-v1` and `runs/smefix-v2`
(52 and 34 rows each, 3-schema pools), and those are the ones section 2 deletes. The
code records everything needed, so a rerun regenerates it, but until then there is no
on-disk data to compute against. Extract the six routing columns to a small file
before wiping, or accept the gap. On `serial-v1` the split is already 52 rows with 0
shortlist misses, 1 picked wrong and 0 fallbacks; `smefix-v1` has 2 rows with
`schema_pick_fallback == "call_failed"`.

## 4. Cross-cutting: the new dataset

The sibling `BIRD-Data-Obfuscation` repo is filtering questions down to a set whose
SQL does not contradict its evidence or other metadata. Schemas stay as they are, the
train/test split changes, and some schemas may end up with no questions at all because
of the existing 60-question-per-schema filter.

That needs one code change. Runs before and after the new dataset are not comparable,
and nothing records the difference today. Add a `question_pool_hash` to
`MANIFEST_KNOBS` in `src/governed_bi/eval/metrics.py:115`. Because `COMPARABILITY_KEYS`
in `src/governed_bi/eval/index.py:104` is derived from `MANIFEST_KNOBS` minus an
explicit exclusion set, the new key joins the comparability gate automatically.

Deferred: a guard for schemas that end up with zero questions, which must not count as
built-but-unscored and must not break the pool census. That needs the real dataset to
test against.

## 5. Ordering

Phase 1 runs in parallel, because the files are disjoint and none of it has open
design questions: D1 (`asset_bag.py`, `note_inject.py`, tests), C1
(`prompts/registry.py`, `curator/sme.py`, tests), the `question_pool_hash` addition
(`metrics.py`, `index.py`), and D2 step 1, which is read-only investigation.

Phase 2 is B6, after C1 lands, because the fold wiring depends on the SME's answer
behaviour.

Phase 3 is D2 steps 2 and 3, once step 1 says whether existing runs suffice.

## Status

| Item | What | Status |
| --- | --- | --- |
| D1 | Notes carry triggers; `on_match` replaces default-always | done, 133/29 split |
| C1 | Replace contradictory SME rules prompt; fix sanitiser | done, one variant |
| pool-hash | `question_pool_hash` in `MANIFEST_KNOBS` and the gate | done |
| D2 step 1 | Is the three-way split computable? | done, yes, no rerun needed |
| B6 | Delete `_mark_columns_absent_from_gold`; AI-authored suspect marks | done, sweep untested |
| artifact cleanup | Wipe pre-rebuild artifacts | done, 89M, routing columns kept |
| T1 | Wire `pin_triggers_enabled` for eval; make it separately measurable | done, option A |
| config | `[notes]` table documented; stale toml/env comments corrected | done |
| B6-verify | Does the agent sweep actually mark decoys? Needs a curated build | not started |
| D2 step 2 | Land the three-way split as a summary metric | not started |
| routing table | Retire the stale recall numbers in `datalake-run.md` and its 2 citations | not started |
| naming | `*_GLOBAL_MAX` now means per-turn; rename in `config.py` | not started |
