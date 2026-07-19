# Data-lake run: runbook + status

_Implements [D15](../design-decisions.md#d15-multi-schema-serving-one-database-many-schemas)
(one database, many schemas). Companion to
[eval-ladder-results.md](eval-ladder-results.md) (single-DB arms, method,
terminology). Read that first if the arm names (`baseline` / `curated` /
`curated_sme`) or the ADR-0002 serve core are unfamiliar._

## What it is

The single-DB harness (`governed_bi.eval.run_experiment`) pins **one schema**
per run. The data-lake run instead loads **all 69 BIRD `db_id`s as 69 schemas
in one Postgres database** (`pg_rename_decoy`, port 5435) and adds a **schema
router** that picks the schema per question at serve time: the D15 topology
at eval scale.

Driver: `src/governed_bi/eval/run_datalake.py`, invoked as
`python -m governed_bi.eval.run_datalake`.

It scores the same three fair rungs as the eval ladder (`baseline`,
`curated`, `curated_sme`) through the same ADR-0002 agentic serve core. The
arms differ only in the corpus fed in; routing, guardrails, and grading are
shared.

## How it works

Three phases, run in sequence by the one driver invocation.

### 1. Build

For each requested `db_id`, build `baseline` / `curated` / `curated_sme`
corpora into three **shared** roots: `corpus_baseline/`, `corpus_curated/`,
`corpus_curated_sme/`. Each db writes its own `<root>/<db_id>/` subtree, so the
69 dbs share one root per arm instead of the single-DB harness's one root per
run.

- **Resumable.** A db whose subtree already has YAML is skipped on the next
  invocation. Force a rebuild with `--no-resume`.
- **Sidecar relocation.** Per-db curator sidecar files (`run_manifest.json`,
  `validate_findings.jsonl`, etc.) are moved to `<root>/<db_id>/_build/` so
  that a shared root doesn't have 69 dbs clobbering the same sidecar
  filenames.
- **Partial-failure tolerant.** A db that fails to build is dropped from the
  pool and recorded in `build_errors`; one bad db does not abort the run.

### 2. Pool

Test questions are loaded per db and tagged with their `db_id` (the
`EvalItem` type itself carries no `db_id` field, so the tag lives alongside it
in the pooling step). Gold hashes are merged across all dbs, keyed by
`question_id`. This is safe because `question_id` is globally unique: verified
at 2030 test questions / 2030 distinct `question_id`s, so pooling is
collision-free.

Suspect/decoy columns are **not** pooled the same way. They're kept as a
**per-db set**. The decoy-touch metric matches on bare column names, and
pooling suspect sets across all 69 schemas would let one db's decoy name
false-positive against another db's question. Each db's questions are scored
against that db's own suspect set only.

### 3. Serve

One **unpinned** `PostgresConnector(schema=None)` spans every schema for the
whole run. The engine emits fully schema-qualified `schema.table` SQL, and a
bare or invented reference fails closed (D15's guardrail contract). Each
arm's merged corpus is loaded once with `load_corpus(root, schema=None)`.

Scoring: EX against the pooled gold hashes, plus a live `routing_recall`
metric (the share of questions whose true schema survived the routing
shortlist), reported separately so mis-routing doesn't hide inside a low EX
number.

## Routing configuration (the crux)

Two `Settings` knobs, new for this run, drive the schema router:

| Knob | Meaning | Default (product/single-db) | Data-lake driver default |
|---|---|---|---|
| `schema_route_top_k` | candidate schema shortlist size | 3 | 8 |
| `schema_route_llm_pick` | LLM picks exactly one schema from the shortlist | `False` | `True` |

When `schema_route_llm_pick=True`, an LLM picks exactly one schema from the
shortlist (pipeline-design §5.1) and **cross-schema join expansion is
skipped**. This is the single-schema-answer regime, which is correct for
BIRD (every test question targets exactly one `db_id`). The default
(`False`) is the general cross-schema regime and is unchanged for the
single-db/product serve paths.

The data-lake driver also turns the embedder on by default, on top of
`top_k=8` and `llm_pick=True`. Schema-document vectors are embedded once at
rails-build time (`embed_schema_documents`), not re-embedded per question.

CLI knobs: `--route-top-k N`, `--no-llm-pick`, `--no-embedder`.

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
`select_schema` (the LLM single-pick) narrows to one schema.

A live run of the full path (gpt-5.6-luna, embedder shortlist `top_k=8` + LLM
pick, 138-question sample across all 69 schemas, tables-only corpus) measured:

| metric | value |
|---|---|
| shortlist recall@8 | 0.848 (117/138) |
| `select_schema` pick accuracy (end to end) | 0.732 (101/138) |
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
- `.env` with `OPENAI_API_KEY` and `PG_RENAME_DECOY_DSN`.
- Model: `gpt-5.6-luna` (`governed_bi.toml [models].llm_model`).

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

**Full run.** The heaviest run in the project: 69-DB curation followed by
2030 × 3 agentic serve calls.

```bash
uv run python -m governed_bi.eval.run_datalake --out runs/datalake/
```

Other flags: `--dbs a,b,c` (explicit db list instead of all test dbs),
`--arms baseline,curated,curated_sme` (subset of arms; baseline-only skips the
expensive curation), `--limit N` (cap test questions per db), `--pg-dsn`,
`--bird-dir`, `--max-agent-steps`.

## Outputs

Under the timestamped `--out` directory:

- `generations.<arm>.jsonl`: per-question rows, including `db_id`,
  `routed_schemas`, `routed_hit`, `schema_pick`.
- `summary.json`: per-arm EX (lenient/strict), `routing_recall`,
  `schema_pick_accuracy`, per-db breakdown, deltas, `build_errors`,
  `gold_hash_self_check`.
- `manifest.json`.
- The three built corpus roots (`corpus_baseline/`, `corpus_curated/`,
  `corpus_curated_sme/`).

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
- Build (curation) is resumable across invocations; the serve phase always
  re-runs fresh.

## Status

The driver runs end to end and the eval ladder replicates at multi-db scale.
Confirmed:

- Offline (`--skip-agent`) build → pool → serve → grade on 1- and 2-db pools.
- Live schema routing at 69-schema scale: embedder shortlist + `select_schema`
  give ~0.73 effective single-schema routing (routing table above), up from the
  ~0.35 BM25 ceiling.
- **5-db, 3-arm live dry run** (72 pooled questions, 15/db, `address` `airline`
  `app_store` `authors` `beer_factory`):

  | arm | EX | vs prev |
  |---|---|---|
  | baseline | 0.208 | |
  | curated | 0.333 | +0.125 |
  | curated_sme | 0.417 | +0.083 |

  Both the curated moat and the SME lift show up. Decoy-touch fell 0.35 → 0.0 →
  0.01 (curated reliability annotations working); all arms CI-green; gold
  self-check 5/5; no build failures. Routing recall here reads ~0.97 only
  because the pool holds 5 schemas — the real 69-schema routing number is the
  ~0.73 above, not this.

The full 69-schema run has **not** been executed. Two operational notes for it:

- **Rate limits.** Live curation hit the org's 200K TPM cap on `gpt-5.6-luna`.
  The deep-agent curator degraded gracefully (it stopped that db's curation
  early rather than crashing), but one db (`app_store`) then got no curated lift.
  The full run needs curation throttling / backoff, or it will silently
  under-curate some dbs.
- The 5 dbs here are small. Larger dbs (more train questions) cost more to
  curate, so extrapolate the full-run budget from a larger db, not these.
