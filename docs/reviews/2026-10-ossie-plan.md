# Ossie plan: move the corpus to Apache Ossie

Status: superseded on 2026-10-19 by [ADR 0020](../adr/0020-ossie-export-and-a-separate-viewer.md) (proposed), which keeps the corpus in its current files and adds an Ossie export instead. The mapping and gap counts below carried over into 0020.

## 1. What Ossie gives us

The spec is Apache Ossie (incubating), formerly Open Semantic Interchange. It was read at commit
`53418ba` of the local checkout `../ossie` (remote `apache/ossie`), where it is version
`0.2.0.dev0`.

- The spec calls itself a draft whose schema may change before 0.2.0, and says not to depend on
  `0.2.0.dev0` in production. The last release is 0.1.1. The only tag is `osi-0.1.1-rc1`.
- `apache-ossie` (import name `ossie`, Python 3.11 or later, frozen pydantic models with
  `extra="forbid"`) is not on PyPI. It can only be installed from a git pin.
- One document is one semantic model, and a model cannot be split across files.
- A model holds datasets with fields, relationships and metrics.
- Every level can carry `ai_context`, either a string or an object with the recommended keys
  `instructions`, `synonyms` and `examples`, plus any additional keys.
- `custom_extensions` is a list of `{vendor_name, data}` entries, where `data` is a JSON string.
- A relationship has **no cardinality field**. It is always many-to-one: `from` is the many side
  and `to` the one side, joined foreign key to unique key. `from_columns` and `to_columns` are
  arrays, so multi-column joins are supported. Explicit cardinality is an open upstream item
  (`apache/ossie` discussion #50).
- `OSSIE_SQL_2026` is the only expression dialect whose meaning the spec defines. `ANSI_SQL` is an
  accepted key, but the spec treats it as opaque.
- There is no native slot for question-and-SQL pairs. `ai_context.examples` holds questions only.
  Verified queries as a core element are on the roadmap (`apache/ossie` discussion #82).
- Validation is `../ossie/validation/validate.py`. `converters/` ships converters from dbt, Cube,
  Snowflake and others, each its own unreleased package.

## 2. Decision: Ossie is the storage format, not the engine's model

The engine keeps its eight internal asset types. `corpus/store.py` reads Ossie files and converts
them to those types at load time, and a writer does the reverse for the migration. Retrieval
builds its indexes per asset type, so nothing past the loader has to change. Because the spec is a
draft, a change to it should only touch the converter.

One Ossie model per database gives 57 files in place of today's 7,357.

## 3. Where each asset goes

| Asset today | Ossie location |
|---|---|
| `SchemaAsset` | the model itself; `summary` and `body` become `description`; `rules` go into `ai_context.instructions` |
| `TableAsset` | a dataset; `source` is `db.schema.table`; `summary` and `body` become `description` |
| `ColumnAsset` | a field whose expression is the column name; `physical_type` maps to `datatype` |
| `JoinAsset` | a relationship from the many side to the one side, with the `on` clause split into `from_columns` and `to_columns`; the engine's cardinality in the `GOVERNED_BI` extension on the relationship |
| `MetricAsset` | a metric whose expression references `dataset.field` |
| `TermAsset` | synonyms on the field or metric the term binds to; the term itself in an `ai_context` key, `terms` |
| `FewShotAsset` | the model-level `ai_context` key `verified_queries`, in the shape of Snowflake's `verified_queries` so it lines up with discussion #82 if that lands |
| `NegativeExampleAsset` | the model-level `ai_context` key `unanswerable` |
| `Provenance`, `Reliability`, `Governance` | a `custom_extensions` entry with `vendor_name: GOVERNED_BI` on the same element |

The rule behind the last two columns: what the model reads goes in `ai_context`, and what the
engine or a person manages goes in `custom_extensions`. Ossie accepts any string as a vendor name.

## 4. Known gaps, counted on BIRD-corpus at `74ff80c`

- **Joins.** All 706 are single-column equality joins. 704 declare a cardinality: 327
  `one_to_many`, 210 `many_to_one`, 55 `one_to_one` and 112 `many_to_many`. Two in `talkingdata`
  declare none. `many_to_one` maps directly and `one_to_many` maps by flipping direction. Since
  Ossie has no cardinality field, `one_to_one` keeps its cardinality only through the extension.
  The 112 `many_to_many` joins are not foreign-key joins, so they go into a model-level
  `custom_extensions` list, and the loader merges them back. The owner decides what the two joins
  without a cardinality become.
- **Metrics.** Expressions name bare columns against a `base_table`, for example
  `AVG(female_population)`, and some use functions that are not standard SQL, such as
  `DIVIDE(a, b)`. Qualifying the columns is mechanical. Rewriting the functions would change text
  the model reads, so the migration counts them first and the owner decides, together with the
  dialect key (`OSSIE_SQL_2026` or `ANSI_SQL`).
- **Conformance.** `conform/tree.py` reads raw per-asset YAML on purpose, so the conformance rules
  (V0 to V23, 20 ids in use) and the nightly corpus gate from ADR 0016 have to be ported to read the
  loaded assets, or dropped.
- **The corpus hash.** `corpus_content_hash` digests files, so the hash of the same content
  changes with the format. The comparability gates have to treat the migration commit as a new
  corpus identity.
- **Review granularity.** One file per database loses the per-definition git diff. With the return
  path gone after T2, nothing depends on that.

## 5. Steps

The rules for every step in the trim milestones apply here too, with branches named
`o<N>/<short-name>`. The snapshot tools are kept, or restored from git, for this plan.

### O1. Decide and count

1. **ADR 0017**, "Ossie as the corpus format", records §2, the mapping in §3 and the handling of
   each gap in §4. Merge it before any code.
2. **Count the gaps** on the corpus commit pinned in `tools/corpus_baseline.py`: joins by
   cardinality, metric expressions that sqlglot cannot parse in the chosen dialect, and their
   functions. Bring the counts to the owner.

### O2. Converter and migration

1. **Add the dependency.** Pin `apache-ossie` to a commit of `apache/ossie`, or to a release if
   one is published by then.
2. **Write the converter** in `corpus/`, with a reader from Ossie to the internal assets and a
   writer for the reverse. `corpus/store.py` reads Ossie, and the old per-asset reader is deleted at
   the end of the step, not before.
3. **Migrate BIRD-corpus** with a one-off script, run outside this repository, that reads the old
   tree with the old reader and writes 57 models with the new writer. Commit the result to
   BIRD-corpus on a branch, and bump `tools/corpus_baseline.py` to it.
4. **Port conformance.** Change `conform/tree.py` and the rules it feeds to read loaded assets, or
   delete the rules that only made sense per file. Each deleted rule is named in the PR with a
   reason.
5. **Corpus identity.** Make sure the comparability gates treat the migrated corpus as a new
   identity, and record the old and new hashes in ADR 0017.
6. **Documents.** Rewrite `docs/corpus-format.md` for the new layout.

### Deliverables

- ADR 0017, the converter with tests, the migrated BIRD-corpus branch, the ported conformance
  rules and the rewritten `docs/corpus-format.md`.

### Acceptance

- [ ] All 57 model files pass `../ossie/validation/validate.py` and load through
      `ossie.OssieDocument.from_ossie_yaml`.
- [ ] **Round trip.** Loading the old tree, writing Ossie and loading that back gives the same
      internal assets, field for field, including every join's cardinality. The only exceptions
      are listed in ADR 0017.
- [ ] The snapshot matches the T6 snapshot with no expected differences other than those ADR 0017
      lists for metric expressions.
- [ ] Every one of the 112 many-to-many joins survives the round trip.
- [ ] The test-question leak check passes on the migrated corpus.
- [ ] `git grep` finds no reader of the old per-asset layout in `src` or `tools`.
- [ ] A live run of the shipped configuration, as in T6, stays within 2 EX points.

### Out of scope

- Changing retrieval, the asset types or anything else the engine does with the assets.
- Rewriting definitions, fixing descriptions or marking anything reviewed.
- Converters from dbt or other formats.

### Stop and ask if

- The spec in `../ossie` changes shape during the work.
- More than 5% of metric expressions need rewriting to be valid in the chosen dialect.
- The round trip cannot be lossless for some asset type without a key that ADR 0017 does not
  name.

## 6. Decisions the owner still has to make

1. Whether to start before Ossie 0.2.0 is released, or wait for it.
2. Which dialect key metric expressions use, and how to handle the functions §4 counts.
3. What the two joins without a cardinality become.
