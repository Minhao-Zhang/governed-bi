# Brief: the semantic layer today, and Ossie

Status: a brief of facts, 2026-10-23, for an independent assessment of how to store the semantic
layer in the Ossie format. It states what exists and what is asked, and leaves the answer open.
Facts were checked against `main` at `491f6c9`, `../BIRD-corpus` at `8ba312ed` and `../ossie` at
`d85e9e1`. Check any of them against the code before relying on it.

The [assessment](2026-10-ossie-storage-assessment.md) written from this brief lists, in its
section 7, the other facts here that turned out wrong or incomplete. They are left as written, so
the brief stays a record of what the assessment was given.

## What the owner asked

The owner wants the repository's storage format for the semantic layer to become compatible with
Apache Ossie. Earlier, ADR 0020 decided to keep the current YAML layout and add an Ossie export;
the owner wants to revisit that. The owner also noted that Ossie supports extensions
(`custom_extensions`) and asked whether those can carry what Ossie has no field for.

Constraints from the owner:

- Greenfield project with no external users. Bold changes are welcome, and deleting is preferred
  to adding (`AGENTS.md`).
- One user today: the owner, on BIRD-corpus. Backend is Python under `src/governed_bi/`, run with
  `uv`.
- The engine must keep answering exactly as it does now from the same definitions.

## The layer today

`GOVERNED_BI_CORPUS_DIR` (default `../BIRD-corpus`) is a directory of YAML files, one asset per
file. The format is described in `docs/corpus-format.md`.

```text
<corpus root>/<schema>/<schema>.yaml
                      /tables/  /joins/  /few-shots/  /terms/  /metrics/
```

BIRD-corpus has 57 schemas (one per database) and 7,357 files, about 208k lines and 8.6 MB.
Loaded, that is 13,304 assets: 57 schemas, 656 tables, 5,947 columns (written inside their table
files), 706 joins, 478 metrics, 603 terms, 4,857 few-shots and 0 negative examples. The largest
database, `works_cycles`, is about 15.8k lines on disk. BIRD-corpus has no tooling of its own;
its checks live in governed-bi's `tools/`.

### The eight asset types (`src/governed_bi/corpus/schema.py`)

Every type has `id`, `summary`, `body`, `governance` (`excluded`, `reason`, `by`, `at`),
`confidence` and `audit` (`provenance`, `evidence`, `extra`). Beyond those:

| Type | Fields |
|---|---|
| Schema | `name`, `rules` |
| Table | `schema`, `physical_name`, `grain`, `row_count`, `rules`, `columns` (ids) |
| Column | `schema`, `parent_table`, `physical_name`, `physical_type`, `logical_type`, `nullable`, `is_unique`, `sample_values`, `role` (primary_key, foreign_key, key, measure, dimension), `references`, `reliability` (`status` ok or suspect, `note`) |
| Join | `left_table`, `right_table`, `on` (a SQL text such as `formula_1.carreras.id_circuito = formula_1.circuitos.id_circuito`), `cardinality` (one_to_one, one_to_many, many_to_one, many_to_many, or none), `cost` |
| Metric | `name` (display), `base_table`, `expression` (SQL, often unqualified, e.g. `AVG(milisegundos)`), `dimensions` |
| Term | `name`, `synonyms`, `binding` (target type and id), `related_terms` |
| Few-shot | `schema`, `sql`, `bound_terms`, `complexity`; `summary` is the question, and in BIRD `body` reads "Question: … SQL: …" |
| Negative example | `schema` (may be none, meaning every schema) |

Join cardinalities in BIRD: 327 one_to_many, 210 many_to_one, 55 one_to_one, 112 many_to_many, 2
with none (both in `talkingdata`). All 706 are single-column equalities.

Some of these fields reach the model's prompt (`serve/context.py` formats summary, body, rules,
grain, row_count, reliability and others). `audit` is kept out of what the model sees. The
`COLUMNS` check reads `reliability.status` and `governance.excluded`.

### What reads the files

Past the loader, the engine reads only the dataclasses above. The on-disk format is known to:

- `corpus/store.py`: `load` walks every `.yaml` file (a file may also hold a list of assets);
  `write` writes `<root>/<namespace>/<id>.yaml`.
- `corpus/identity.py`: `namespace_of` takes the first directory as the schema; with `schemas=`
  set, a file at the root is skipped.
- `corpus/hash.py`: hashes relative paths and file bytes. That hash is a field of its own on each
  evaluation row. It is not part of `config_hash`, which hashes the settings only, and two runs
  are compared by their context hashes (`eval/report.py`, `comparison_quotable`). (Corrected
  after the assessment; the first version of this brief said the corpus hash was part of the
  config hash.)
- `serve/session.py` (around line 328): seeding a corpus from a live database schema calls
  `store.write` per asset.
- `tools/check_train_only.py` and `tools/check_no_benchmark_discriminators.py` build or scan YAML
  paths.

The embedding cache is keyed by model, dimensions and summary text, not by file.

### The current Ossie exporter

`src/governed_bi/corpus/ossie_export.py` (616 lines) writes one Ossie `0.2.0.dev0` model per
schema, plus a report, and has a reader for its own output. Tests are in
`tests/corpus/test_ossie_export.py`. All 57 BIRD models pass `../ossie/validation/validate.py`.
What it does now:

- Schema → the model. `summary` and `body` are joined with a blank line into `description`;
  `rules` are joined with newlines into `ai_context.instructions`.
- Table → a dataset, `source` `schema.table`, `primary_key` from column roles.
- Column → a field whose expression is the quoted column name, `datatype` from `logical_type`.
- Join → a relationship for many_to_one, one_to_one and one_to_many (flipped so `from` is the
  many side). many_to_many, no-cardinality and non-equality joins go whole into lists in the
  model's extension.
- Metric → a model-level metric, expression verbatim under `ANSI_SQL`.
- Term → synonyms copied onto the bound entity's `ai_context.synonyms`, and the whole term
  (audit included) in the model's `ai_context.terms`.
- Few-shot → model `ai_context.verified_queries` as `{name, question, sql, governed_bi: …}`.
- Negative example → model `ai_context.unanswerable`, whole.
- Everything else of an entity → one `GOVERNED_BI` extension per entity, `data` a single-line JSON
  string.

Round-trip losses the test pins: `column.physical_type` (all 5,947), `few_shot.body` (all
4,857), and `schema.summary`/`schema.body` (1 each, a summary containing a blank line). Also not
lossless: a rule containing a newline, the original `on` text (rebuilt from column pairs),
negative examples with no schema and terms it cannot place (BIRD has none). Model-level metrics
are unqualified, which the spec says should be `dataset.field`; `validate.py` does not check it.

## Ossie at `d85e9e1`

Read `../ossie/core-spec/spec.md` and `core-spec/ossie-schema.json`.

- Version `0.2.0.dev0`, a draft: "schema may change before 0.2.0 is released". The last release
  is 0.1.1. 29 core-spec commits since September; `fae8f41` (2026-10-21) added dataset-scoped
  metrics; an earlier change replaced the `semantic_model` array with a flat document.
- One document is one model; the spec "does not define a bundle format or cross-model
  references". No includes.
- Properties: model `name, description, ai_context, datasets, relationships, metrics,
  custom_extensions`; dataset `name, source, primary_key, unique_keys, description, ai_context,
  fields, metrics, custom_extensions`; field `name, expression, dimension (is_time), label,
  description, datatype, ai_context, custom_extensions`; relationship `name, from, to,
  from_columns, to_columns, ai_context, custom_extensions`; metric `name, expression,
  description, datatype, ai_context, custom_extensions`. Entities forbid other properties.
- `ai_context` is a string, or an object with `instructions` (string), `synonyms` and `examples`
  (string lists), and any other keys.
- `custom_extensions` is a list of `{vendor_name, data}`; `data` must be a string, described as a
  JSON string; any vendor name is accepted.
- Identity is the name, unique within its scope. No other ids.
- A relationship is always many-to-one, foreign key to unique key. No cardinality field.
- 12 dialect values; only `OSSIE_SQL_2026` has defined semantics.
- `validate.py` checks the JSON schema plus unique names, relationship references, key coverage,
  column arity, sqlglot syntax and dataset-scoped metric references.
- `../ossie/python/` has pydantic models; the package is not on PyPI.
- `../ossie-viewer` is the owner's separate viewer for Ossie files (0.1.1 and 0.2.0.dev0).

## Questions open

None of these is decided:

1. Should Ossie be the format on disk, or should the YAML stay with conversion both ways?
2. If on disk: one file per database, or some split, and how per-definition review in git works.
3. Where each asset field goes: an Ossie field, `ai_context`, `custom_extensions`, or nowhere.
4. How to keep what Ossie cannot express: many-to-many and other joins, cardinality, ids.
5. How extension data is written so it stays reviewable.
6. How to migrate BIRD-corpus and prove the engine loads the same definitions.
7. What happens to the corpus hash and the P16 baseline's comparability.
8. Whether code names (`few_shot`, `join`, `table`) should follow Ossie's names.
9. Whether Ossie models written by other tools should load, and what defaults they would need.
10. How a later spec change is absorbed.
