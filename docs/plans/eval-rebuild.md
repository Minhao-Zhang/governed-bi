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

To verify: a unit test asserting that emitted triggers fire on the originating
question and do not fire on a sibling question in the same schema. Then one small
live `curated_sme` build over 2 or 3 schemas, confirming the emitted mix is not 100%
`always` again.

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

To verify: run the decoy-touch metric on a small curated build before and after, and
report the direction rather than landing the change silently.

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

Three steps:

1. Check whether the shortlist contents are recorded per row in provenance. The stage
   detail records `n_candidates` and `fallback`, but if the members are absent then the
   split cannot be computed on existing runs and needs one cheap provenance field.
2. Compute the three-way split: true schema absent from the shortlist, present but
   picked wrong, or pick fell back.
3. Land it as a summary metric, so it is tracked rather than re-derived by hand.

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
| D1 | Notes carry triggers; `on_match` replaces default-always | not started |
| C1 | Replace contradictory SME rules prompt; fix sanitiser | not started |
| B6 | Delete `_mark_columns_absent_from_gold`; AI-authored suspect marks | not started |
| D2 | Attribute routing failure (shortlist-miss, pick-wrong, fallback) | not started |
| pool-hash | `question_pool_hash` in `MANIFEST_KNOBS` | not started |
| artifact cleanup | Delete `runs/`, `corpora/`, `data/checkpoints/`; retire stale routing table | not started |
