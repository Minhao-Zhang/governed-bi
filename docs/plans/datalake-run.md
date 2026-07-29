# Data-lake run: runbook + status

_Implements [D15](../design-decisions.md#d15-multi-schema-serving-one-database-many-schemas)
(one database, many schemas). Companion to
the single-DB arm/method record in git history (
terminology) and the operator checklist in
[experiment-runbook.md](experiment-runbook.md). Read the runbook before a paid
pooled run. Arm names: `baseline` / `seeded` / `curated` / `curated_sme`
._

## What it is

The single-DB harness (`governed_bi.eval.run_experiment`) pins **one schema**
per run. The data-lake run instead loads **all 69 BIRD `db_id`s as 69 schemas
in one Postgres database** (`pg_rename_decoy`, port 5435) and adds a **schema
router** that picks the schema per question at serve time: the D15 topology
at eval scale.

Driver: `src/governed_bi/eval/run_datalake.py`, invoked as
`python -m governed_bi.eval.run_datalake`.

Default fair arms are `baseline`, `seeded`, `curated`, `curated_sme` (same
ladder as the experiment runbook). The arms
differ only in the corpus fed in; routing, guardrails, and grading are shared.

## How it works

Three phases, run in sequence by the one driver invocation.

### 1. Build

For each requested `db_id`, build the requested arms (default:
`baseline` / `seeded` / `curated` / `curated_sme`) into shared roots
`corpus_<arm>/`. Each db writes its own `<root>/<db_id>/` subtree, so the
69 dbs share one root per arm instead of the single-DB harness's one root per
run.

- **Resumable.** A db is skipped only when a durable `BUILD_COMPLETE.json`
  exists under `<db>/_build/` (with YAML present). Partial YAML without that
  marker is rebuilt, not adopted. `--no-resume` starts clean: it rebuilds every
  corpus *and* re-serves every question already scored in the run directory.
- **Parallel builds.** With `--build-workers > 1`, each build uses a private
  staging root and is promoted into the shared arm root by a same-filesystem
  swap (incoming → live; prior tree removed only after the new tree lands).
- **Sidecar relocation.** Per-db curator sidecar files (`run_manifest.json`,
  `validate_findings.jsonl`, etc.) are moved to `<root>/<db_id>/_build/` so
  that a shared root doesn't have 69 dbs clobbering the same sidecar
  filenames.
- **Partial-failure tolerant.** A db that fails to build is dropped from the
  pool and recorded in `build_errors`; one bad db does not abort the run
  unless build coverage falls below the abort fraction (see the experiment
  runbook).

### 2. Pool

Test questions are loaded per db and tagged with their `db_id` (the
`EvalItem` type itself carries no `db_id` field, so the tag lives alongside it
in the pooling step). Gold hashes are merged across all dbs, keyed by
`question_id`. This is safe because `question_id` is globally unique: verified
at 2030 test questions / 2030 distinct `question_id`s, so pooling is
collision-free.

Suspect/decoy columns are **not** pooled the same way. They're kept as a
**per-db set**, because pooling suspect sets across all 69 schemas would let one
db's decoy name false-positive against another db's question. Each db's questions
are scored against that db's own suspect set only.

Within a db, decoy-touch resolves each column reference to its own query scope
before matching (`arms._touches_suspect`), so a reused alias cannot attribute a
column to the wrong table. A genuinely unqualified, ambiguous reference still
counts as a touch — fail-closed, matching how guardrail L3 reads the same
ambiguity — so the metric can over-count but never silently under-count.

### 3. Serve

One **unpinned** `PostgresConnector(schema=None)` spans every schema for the
whole run. The engine emits fully schema-qualified `schema.table` SQL, and a
bare or invented reference fails closed (D15's guardrail contract).

Each arm's corpus is loaded with `_load_built_corpus(root, built)` — scoped to the
dbs actually being scored, not to whatever is on disk. The arm roots are shared and
cumulative, so a directory-wide load served every subtree any prior attempt had
written: a db dropped from `built` (a transient Postgres blip is enough) kept
competing as a router candidate for every other db's questions, silently changing
the routing problem's difficulty between runs, while `corpus_census` and
`corpus_validation` described a different corpus than the one being served.

Scoring: EX against the pooled gold hashes, plus a live `routing_recall`
metric (the share of questions whose true schema survived the routing
shortlist), reported separately so mis-routing doesn't hide inside a low EX
number.

## Routing configuration (the crux)

Two `Settings` knobs, new for this run, drive the schema router:

| Knob | Meaning | Default (product/single-db) | Data-lake driver default |
|---|---|---|---|
| `schema_route_top_k` | candidate schema shortlist size | 3 | 10 |
| `schema_route_llm_pick` | LLM picks exactly one schema from the shortlist | `False` | `True` |
| `schema_pick_max_columns` | column names per table shown to the picker (0 = names only) | 12 | 12 |

When `schema_route_llm_pick=True`, an LLM picks exactly one schema from the
shortlist (D15) and **cross-schema join expansion is
skipped**. This is the single-schema-answer regime, which is correct for
BIRD (every test question targets exactly one `db_id`). The default
(`False`) is the general cross-schema regime and is unchanged for the
single-db/product serve paths.

The data-lake driver also turns the embedder on by default, on top of
`top_k=10` and `llm_pick=True`. Schema-document vectors are embedded once at
rails-build time (`embed_schema_documents`), not re-embedded per question.

CLI knobs: `--route-top-k N`, `--schema-pick-max-columns N`, `--no-llm-pick`,
`--no-embedder`. All four are recorded in `manifest.json`, guarded on `--resume`,
and read by the run ledger's comparability rule, so two runs that differ in any of
them are reported as not comparable rather than quietly compared.

### The key risk (and the routing design)

Schema routing, not curation, is the binding constraint on this run: a
mis-routed question scores EX 0 no matter how good the corpus is. A probe over
the full 2030-question test set against the tables-only `../BIRD-corpus`
measured schema-routing recall three ways:

| strategy | recall@1 | recall@3 | recall@5 | recall@10 |
|---|---|---|---|---|
| BM25 (lexical) | 0.234 | 0.351 | 0.435 | 0.572 |
| **embedding-only** | **0.517** | **0.700** | **0.785** | **0.860** |
| BM25 + embedding RRF | 0.346 | 0.535 | 0.626 | 0.746 |

Two findings drove the router design:

- BM25 alone is weak here. BIRD questions rarely share identifiers with
  schema/table names, so a dozen schemas (`olympics`, `retails`,
  `european_football_2`, ...) score 0.00 recall@3 lexically.
- RRF-fusing BM25 with the embedding signal is **worse than embedding alone**
  at every k: the weak lexical ranks drag the strong embedding ranks down.

So `shortlist_schemas` now ranks by embedding similarity when an embedder is
present, and only falls back to BM25 without one. On top of the shortlist,
`pick_schema` (the LLM single-pick) narrows to one schema.

A live run of the full path (gpt-5.6-luna, embedder shortlist `top_k=8` + LLM
pick, 138-question sample across all 69 schemas, tables-only corpus) measured
the following. Note the `top_k=8`: this run predates the driver default moving to
10, so it is not directly comparable to a run made today — the knob table above
gives the current defaults.

**Retired 2026-07-25 — do not quote.** Produced before the measurement fixes; kept
as the record of what was run.

| metric | value (RETIRED) |
|---|---|
| shortlist recall@8 | 0.848 (117/138) |
| `pick_schema` pick accuracy (end to end) | 0.732 (101/138) |
| pick accuracy when true schema is in the shortlist | 0.863 (101/117) |

Effective single-schema routing is ~0.73, up from the ~0.35 BM25 ceiling, and
this is on the thin tables-only corpus, so the curated arms (richer schema
docs) should do at least as well. Most residual misses are genuinely ambiguous
sibling schemas in this obfuscated data lake (`food_inspection_2` vs
`food_inspection`, `movielens` vs `movies_4`, `computer_student` vs
`cs_semester`), which no single-pick router fully resolves.

## Prerequisites

- `pg_rename_decoy` Postgres running on port 5435 with the schemas loaded (it
  currently holds 171 schemas total; all 69 BIRD targets are present).
  Loading happens in the sibling repo `../BIRD-Data-Obfuscation`
  (docker-compose + numbered pipeline scripts), **not** in this repo.
- Gold hashes + trap manifests under `../BIRD-Data-Obfuscation/eval_dataset`
  and `/artifacts`, covering all 69 `db_id`s.
- `.env` with `OPENAI_API_KEY`. Postgres for this CLI is `--pg-dsn` (default
  `host=127.0.0.1 port=5435 dbname=bird user=bird password=bird`). The driver
  does **not** auto-read `PG_RENAME_DECOY_DSN`; pass it explicitly if that is
  the DSN you use (`--pg-dsn "$PG_RENAME_DECOY_DSN"`). Product
  `[datasource]` overlays may still name that env var.
- Model: `gpt-5.6-luna` (`governed_bi.toml [models].llm_model`).
- Optional: pin eval routing in TOML with a commented `[routing]` profile that
  matches the driver defaults (`top_k = 10`, `llm_pick = true`); product
  dataclass defaults remain shortlist@3 without pick. See
  `governed_bi.toml` and the experiment runbook.

## Running it

**Offline plumbing smoke** (no model call; exercises build → pool → serve →
grade against live Postgres):

```bash
uv run python -m governed_bi.eval.run_datalake --skip-agent --limit 2 --dbs beer_factory,address --out runs/datalake/
```

**Subset dry run** (the recommended first real step, to validate end to end
and get a cost/latency-per-db estimate before committing to the full run):

```bash
uv run python -m governed_bi.eval.run_datalake --limit-dbs 5 --out runs/datalake/
```

**Full run.** Defaults: all test databases, arms
`baseline,seeded,curated,curated_sme`. That is 69-DB curation (one LLM curator
pass + one SME round; `baseline`/`seeded` are free to build) followed by
2030 × 4 agentic serve calls — five if you add `--replicate curated`
(10,150 scored turns). Size and cost discipline: [experiment-runbook.md](experiment-runbook.md).

```bash
uv run python -m governed_bi.eval.run_datalake --build-workers 6 --workers 8 --replicate curated --out runs/datalake/
```

Other flags: `--dbs a,b,c` (explicit db list instead of all test dbs),
`--arms baseline,seeded,curated,curated_sme` (subset of
`baseline,seeded,curated,curated_sme`; baseline-only skips
expensive curation), `--limit N` (cap test questions per db), `--limit-dbs N`,
`--pg-dsn`, `--bird-dir`, `--max-agent-steps`, `--allow-git-sha-drift` (paid
resume after a code edit; ledger still marks the run unquotable).

## Outputs

Under the timestamped `--out` directory. Full field-by-field detail, and how
to localise a specific kind of failure, is in
[`measurement.md`](../measurement.md); this section is the artifact list.

- `generations.<arm>.jsonl`: per-question rows, including `db_id`,
  `routed_schemas`, `routed_hit`, `schema_pick`, and — the outcome/stage
  taxonomy (`governed_bi.stages`) — `outcome` (`answered` / `refused` /
  `clarification` / `capped` / `crashed`), `failed_stage`, `refused_by`,
  `n_tool_calls`, `by_guardrail_layer`. `outcome`/`failed_stage` are what
  separate a genuine refusal from a crash the serve path degraded into one;
  see `measurement.md` before trusting `refusal_rate` on a run that predates
  this field.
- `stage_events.jsonl`: one record per stage per question served *in this
  attempt* (`stage`, `status`, `ms`, `detail`, tagged with `question_id` /
  `arm` / `db_id`). A row replayed on `--resume` contributes nothing here —
  it has no fresh timings — so on a resumed run this file is a subset of
  `generations.<arm>.jsonl`, joinable by `(question_id, arm)`.
- `summary.json`: per-arm EX (lenient/strict/gradeable), `routing_recall`,
  `schema_pick_accuracy`, per-db breakdown, deltas, `build_errors`,
  `gold_hash_self_check`, and the outcome partition: `by_outcome`,
  `by_failed_stage` (live Outcome/Stage from `classify_row` — distinct from
  offline taxonomy `arms.<arm>.errors.by_error_stage`), `crash_rate` (kept
  separate from `refusal_rate`, which is now genuine refusals only),
  `n_unmapped_refused_by`, `n_with_difficulty`, `tool_calls` and
  `by_guardrail_layer` (summed across rows).
- `manifest.json`: the knobs that change what a scored row means (`split`,
  `model`, `route_top_k`, `route_llm_pick`, `schema_pick_max_columns`,
  `use_embedder`, `prompt_variants`/`prompt_set_hash`, `git_sha`, plus scope
  `arms` / `db_ids` / `oracles` / `replicate_of` / `limit` / `limit_dbs`),
  read back by `--resume` and by the run ledger's comparability check.
- The built corpus roots (`corpus_baseline/`, `corpus_seeded/`,
  `corpus_curated/`, and `corpus_curated_sme/`
  when that arm is scored).
- One record appended to `runs/index.jsonl` (the run ledger,
  `governed_bi.eval.index`), computing whether this run's artifact hygiene is
  `ledger_ok` / `hygiene_ok` / `quotable` (aliases — **not** claim readiness)
  and which prior runs it is `comparable` to. Appended
  automatically at the end of `run_datalake()`; re-index an existing run with
  `uv run python -m governed_bi.eval.index --add runs/datalake/<ts>`.

## Known limitations / notes

- **No cross-check EX.** The gold self-check runs against a schema-pinned
  gateway per sampled db (gold `sql_rename` is schema-unqualified, so it
  needs a `search_path`). The cross-check EX that re-executes gold SQL
  against the span-all connector is therefore skipped in data-lake mode.
- **Intra-schema joins only.** The curator builds joins only from that db's
  own train SQL; it never builds cross-schema joins. Correct for BIRD
  (every test question is single-db), but a genuinely cross-schema question
  would fail closed with a missing-edge refusal (D15's declared-join-only
  contract).
- **Pre-existing seed-quality issue, not data-lake-specific.** Some dbs' seed-
  derived joins carry reference-integrity findings (e.g. `address` produced 2
  `join-on-unresolved` findings from `seed_from_train_sql`). The CI-green
  gate surfaces these loudly in `summary.json.corpus_validation` and warns,
  but does not abort, which is non-fatal by design.
- Both phases are resumable, by separate mechanisms: curation skips a db only
  when `BUILD_COMPLETE.json` is present, and the serve phase replays rows already in
  `generations.<arm>.jsonl` (see "Splits, resume, and offline analysis" below).

## Status

> **Nothing numeric on this page is quotable.** The 2026-07-25 retirement covered
> "these EX values"; the routing numbers date to 2026-07-19, inside the same window,
> and survived it only because the caveat named EX specifically. They were produced
> by the same instrument, under the same crash-counted-as-refusal definitions, so
> they are retired too. Treat every figure below as a record of what was attempted.

The driver runs end to end and the eval ladder replicates at multi-db scale.
Mechanically confirmed (shape, not magnitude):

- Offline (`--skip-agent`) build → pool → serve → grade on 1- and 2-db pools.
- Live schema routing at 69-schema scale runs: embedder shortlist + `pick_schema`
  measured ~0.73 effective single-schema routing (routing table above) against a
  ~0.35 BM25 ceiling. **Retired — do not quote.** The direction (embedding beats
  lexical for schema routing) is the design premise and is independently visible in
  `schema_router`'s own docstring; the magnitudes need re-measuring.
- **5-db, 3-arm live dry run** (72 pooled questions, 15/db, `address` `airline`
  `app_store` `authors` `beer_factory`) — **numbers retired 2026-07-25, see the
  caveat below**:

  | arm | EX | vs prev |
  |---|---|---|
  | baseline | 0.208 | |
  | curated | 0.333 | +0.125 |
  | curated_sme | 0.417 | +0.083 |

  The shape this run was looking for (a curated moat, an SME lift on top) is
  what the table shows. That is not evidence it is there: the same measurement
  faults that retired the numbers also moved them, and per arm by different amounts,
  so the *ordering* is no more trustworthy than the values. What this run
  established is that the harness executes end to end. Decoy-touch fell 0.35 → 0.0 → 0.01 (curated
  reliability annotations working); all arms CI-green; gold self-check 5/5; no
  build failures. Routing recall here reads ~0.97 only because the pool holds 5
  schemas — the real 69-schema routing number is the ~0.73 above, not this.

> **These EX values are retired and must not be quoted as a result** — nor may the
> conclusion drawn from them be carried forward while the numbers are dropped, which
> is what the first version of this caveat allowed.
> They were
> produced under metric definitions since found wrong — most importantly a solver
> crash was counted as a refusal, so `refusal_rate` was inflated and EX depressed
> by an amount that differs per arm. Their run artifacts were deleted rather than
> re-analysed. The table stays only as the record of what the run was looking for.
> The next run under the corrected definitions is the first quotable one, and the
> comparison to make is a paired one (`governed_bi.eval.analysis`), not a
> point-estimate difference across runs.

The full 69-schema run has **not** been executed. Two operational notes for it:

- **Rate limits.** Live curation hit the org's 200K TPM cap on `gpt-5.6-luna`.
  The deep-agent curator degraded gracefully (it stopped that db's curation
  early rather than crashing), but one db (`app_store`) then got no curated lift.
  The full run needs curation throttling / backoff, or it will silently
  under-curate some dbs.
- The 5 dbs here are small. Larger dbs (more train questions) cost more to
  curate, so extrapolate the full-run budget from a larger db, not these.

## Splits, resume, and offline analysis

`--split test` (default) scores the held-out split. `--split train` scores the
larger training split, but those are the very questions the curator read to build
`curated` / `curated_sme`, so it is a **diagnostic for comparing routing or prompt
changes at higher power, never a held-out result**.
The driver prints a warning and records the split in `manifest.json`,
`summary.json` and every generation row.

Serve-phase resume is separate from the build resume above. Rows stream to
`generations.<arm>.jsonl` as they are scored, so an interrupted run keeps its
work. **Repeat the original scope flags** — omitting one means the CLI default,
not "keep what was there", and drift is refused before spend:

```bash
uv run python -m governed_bi.eval.run_datalake --resume-from runs/datalake/<timestamp> \
  --arms <original arms> --dbs <original dbs> --oracle <original oracles> \
  --replicate <original replicate> \
  --limit <original limit if any> --limit-dbs <original limit-dbs if any> \
  --build-workers 6 --workers 8
```

Questions already in the file are replayed rather than re-served, and the
summary is computed over replayed and fresh rows together, so a resumed run
scores identically to an uninterrupted one. Guards: resuming across a different
`--split` is fatal (the question pools are disjoint); a change to scope
(`arms` / `dbs` / `oracle` / `replicate` / `limit` / `limit-dbs`) is fatal;
paid `git_sha` drift is fatal unless `--allow-git-sha-drift` (ledger still
marks the run unquotable); a change to other knobs recorded in `manifest.json`
(model, `route_top_k`, `llm_pick`, `schema_pick_max_columns`, embedder,
`prompt_set_hash`) warns or refuses per the resume contract, because rows
already scored keep the old configuration. Crash-row rewrite of
`generations.<arm>.jsonl` is atomic (temp + replace).

`stage_events.jsonl` resumes differently from the row file: a replayed
question writes no stage-timing records (it has none to report), so the
timing file only ever grows on the questions actually served in the current
attempt, and it is a subset of `generations.<arm>.jsonl` on any run that has
been resumed at least once.

Once a run exists, `governed_bi.eval.analysis` reports — with no model, database
or API cost — what the run itself cannot:

```bash
uv run python -m governed_bi.eval.analysis runs/datalake/<timestamp>
```

- **Table selection.** Splits right-schema failures into wrong-table vs
  right-table-wrong-SQL, and attributes a wrong table to *retrieval* (never
  offered) or *selection* (offered, unused) using the `retrieved_tables`
  provenance. Those need opposite fixes.
- **Paired significance.** Exact McNemar between arms. Serve decoding is not
  pinned, so two runs of the same arm disagree on a nontrivial share of
  questions; comparing point estimates across unpaired runs is not a substitute.
- **Gradeable EX.** EX with frozen `VALUES(...)` golds and order-sensitive
  dataset-excluded rows (`order_sensitive_qids.json`) removed from the
  denominator — the same `ex_gradeable` rule as the live summary.
- **Gold-rank buckets.** EX and pick accuracy by the true schema's rank in the
  shortlist, separating a shortlist miss from a picker error.
