# Assessment: storing the semantic layer as Ossie

Status: an independent assessment, 2026-10-23, written from the brief
(`2026-10-ossie-storage-brief.md`), the code at `491f6c9`, `../BIRD-corpus` at `8ba312ed`, `../ossie` at
`d85e9e1` and `../ossie-viewer` at `c384341`. The earlier proposals named in the request were not read.

What I ran, all outside the repositories:

- Counts of every field of every loaded BIRD asset.
- The existing exporter, then `../ossie/validation/validate.py` on its output (57 of 57 pass).
- A throwaway prototype of the layout recommended below: it wrote BIRD as 57 Ossie files, read them
  back, and compared. All 13,304 assets came back equal as dataclasses. All 57 files pass
  `validate.py` with no warnings. Writing a file, reading it and writing it again gives the same bytes.
- Thirteen hand-made files to test single structures against `validate.py` (listed in section 3).

## 1. Recommendation

**Put Ossie on disk as the only format: one Ossie document per database. Delete the per-asset YAML
layout and the exporter.**

Reasons:

1. **A two-way converter costs as much as the on-disk format.** To keep YAML and still be compatible,
   you need a lossless conversion in both directions. That is the same placement table and the same
   reader and writer that Ossie-on-disk needs. Keeping YAML adds the YAML store on top. With Ossie on
   disk, the YAML layout is the part you can delete.
2. **It can be lossless.** The prototype round-trips all of BIRD once one schema summary loses a
   trailing newline (section 5, step 3). The current exporter loses `physical_type` and few-shot
   `body`. The first is shown to the model by the `inspect_schema` tool (`serve/fetch.py`). The second
   can be rebuilt exactly.
3. **Compatibility is checked all the time, not only at export.** `validate.py` and the viewer open
   the corpus itself. A drift from the spec fails a test on the day it happens.
4. **Less code and fewer files.** 7,357 files become 57. The store's inline-column split, the
   `_shared` directory, the exporter (616 lines) and its report go. The reader and writer together
   came to about 330 lines in the prototype.
5. **Smaller and faster.** 196k lines and 8.0 MB, against 208k lines and 8.6 MB today. Parsing the 57
   files took 0.8 s. The current load, which also validates and builds the assets, took 6.3 s.

What it costs:

- A spec change can force a rewrite of all 57 files. With a canonical writer this is a load and a
  write. The proof is the same equality test (section 6, question 10).
- One YAML syntax error loses a whole database instead of one asset. A test that loads BIRD catches
  this before merge.
- Git shows one large file per database instead of one file per definition. Section 2 shows that a
  change to one definition still reads as a small hunk that names the field.

What would change my mind:

- Ossie 0.2.0 closes `ai_context` to extra keys (`additionalProperties: false`). Most of what Ossie
  lacks would then move to JSON-in-string extensions, and diffs would get much worse. I would then
  keep YAML and export.
- Several people editing one database at the same time, through an app or by hand. A file per
  definition merges better than a file per database.
- The context replay in section 5 shows a difference that no test explains.

Brief option "YAML kept with conversion both ways" is the fallback. "Ossie split into several files
per database" is rejected: the spec has no includes, so no part file would be a valid model, and a
build step would put back the second format.

## 2. Layout, and how a reviewer sees a change

```text
<corpus root>/
  <schema>/<schema>.yaml      one Ossie 0.2.0.dev0 document; `name` equals the directory
  README.md, LICENSE, .gitattributes
```

Keeping one directory per schema means `identity.namespace_of`, the `schemas=` filter and
`corpus/hash.py` do not change. The directory holds nothing else.

The writer is canonical, and a test enforces it (write, read, write gives the same bytes):

- Keys in a fixed order: the Ossie keys in spec order, then ours. The model's `ai_context`, which
  holds the few-shots, is written last, so the file opens on the datasets.
- Block style. A string that contains a newline is a literal block (`|`, `|-`, `|+`), so trailing
  newlines survive. 4,008 column bodies end in one, and the prompt shows it.
- No line folding (unlimited width). One changed word is one changed line. Today's YAML uses folded
  scalars, where one word can reflow a paragraph.

A change to one column's description, with `*.yaml diff=ossie` in `.gitattributes` and
`git config diff.ossie.xfuncname '^(- name: .*|  - name: .*)$'` (tested in a scratch repository):

```diff
@@ -1137,7 +1137,7 @@   - name: ronda
       - dialect: ANSI_SQL
         expression: ronda
     description: |-
-      ronda, the round number of a race within its season.
+      ronda, the round number of a Grand Prix within its season.
```

The path names the database and the hunk header names the field or dataset. The `xfuncname`
setting lives in each clone's git config, so `docs/corpus-format.md` has to state it. If a
reviewer wants "which definitions changed", a small diff tool that loads two revisions and compares
assets by id would answer it. It is optional and not part of the migration.

## 3. Where every field goes

Rule for placement:

1. Use an Ossie field when one means the same thing.
2. Leave out what the position in the file already says: the type, the schema, parent ids, and ids
   that are derived.
3. Put the rest of the definition in `ai_context`, under the engine's own field name. `ai_context`
   is a YAML mapping, so diffs stay readable. The viewer shows it. Most of these values reach the
   model anyway.
4. Put `governance` and `audit` in one `GOVERNED_BI` extension per Ossie entity. They are records
   *about* a definition, and the engine keeps `audit` out of the prompt. Another Ossie tool should
   not feed them to its model either.
5. Records Ossie has no entity for (terms, few-shots, joins that are not relationships) are lists
   under `ai_context`. Each keeps all its fields there, `audit` included, because a list item has no
   `custom_extensions` slot.
6. Delete what nothing reads.

Counts are from BIRD. "Derived" means the reader rebuilds the value and the test shows it matches.

| Asset | Field | Goes to | Why, where not obvious |
|---|---|---|---|
| all | `asset_type` | position | |
| all | `governance` | `GOVERNED_BI` extension (entities), inline (list records) | 0 in BIRD |
| all | `audit` | `GOVERNED_BI` extension (entities), inline (list records) | `check_train_only.py` reads it |
| all | `confidence` | deleted | 22 values. Only range-checked in `corpus/validate.py`; nothing reads it |
| Schema | `id` | derived = model `name` | 57/57 equal |
| Schema | `name` | model `name` | |
| Schema | `summary`, `body` | model `description`: summary, a blank line, body | summary may not contain a newline (1 fix) |
| Schema | `rules` | model `ai_context.instructions`, one rule per line | a rule may not contain a newline (0 do) |
| Table | `id` | derived: `table_id(model, dataset name)` | 656/656 |
| Table | `schema` | position (model `name`) | |
| Table | `physical_name` | dataset `name`; `source` is `<schema>.<name>`, checked on load | |
| Table | `summary`, `body` | dataset `description` | |
| Table | `rules` | dataset `ai_context.instructions` | 4 tables |
| Table | `grain`, `row_count` | dataset `ai_context.grain`, `.row_count` | 0 in BIRD; rendered when set |
| Table | `columns` | order of `fields` | |
| Column | `id`, `schema`, `parent_table` | derived from position | 5,947/5,947 |
| Column | `physical_name` | field `name`; `expression` is the identifier, quoted only when needed | 1 is quoted |
| Column | `summary`, `body` | field `description` | |
| Column | `logical_type` | field `datatype` | `Float` and `DateTimeTz` chosen from `physical_type`, read back as `decimal`/`datetime` |
| Column | `physical_type` | field `ai_context.physical_type` | `inspect_schema` shows it; the prompt shows it when `logical_type` is missing (seeded corpora) |
| Column | `nullable` | field `ai_context.nullable` | rendered |
| Column | `role` | `primary_key` → dataset `primary_key`; other roles → `ai_context.role` | 0 in BIRD |
| Column | `reliability` | field `ai_context.reliability` (`status`, `note`) | 2,285 suspect |
| Column | `is_unique`, `sample_values`, `references` | deleted | 0 in BIRD, no reader. `is_unique` would map to `unique_keys` if ever wanted |
| Join (592) | `id` | relationship `name` | |
| Join | `left_table`, `right_table` | `from`, `to`; swapped for one_to_many | |
| Join | `on` | `from_columns`, `to_columns`; text rebuilt on load | rebuilt text equals the stored text for 706/706 |
| Join | `cardinality` | none for many_to_one; `ai_context.cardinality` for one_to_many (327) and one_to_one (55) | section 4 |
| Join | `summary`, `body` | relationship `ai_context.instructions` | a relationship has no `description` |
| Join | `cost` | deleted | 0 in BIRD, no reader |
| Join (114 others) | whole record | model `ai_context.joins` | 112 many_to_many, 2 without cardinality |
| Metric | `id` | metric `name` | |
| Metric | `name` (display) | metric `ai_context.display_name` | rendered; the id follows the name for only 336 of 478 |
| Metric | `base_table` | position: that dataset's `metrics` (451); else model `metrics` plus `ai_context.base_table` (27) | the 27 fail the dataset-scope check |
| Metric | `expression` | `expression`, dialect `ANSI_SQL` | |
| Metric | `summary`, `body` | `description` | |
| Metric | `dimensions` | metric `ai_context.dimensions` | 15; column ids, may name another table |
| Term (603) | `id`, `name`, `summary`, `body`, `synonyms` | one item in `ai_context.terms` of the bound entity | summary and body stay separate keys: 6 term summaries end in a newline |
| Term | `binding` | position: field (576), dataset (26), metric (0), model (1, the schema) | unbound terms: model list with `unbound: true` (0) |
| Term | `related_terms` | deleted | 0, no reader |
| Few-shot (4,857) | `id`, `summary`, `sql` | one item in model `ai_context.few_shots` | |
| Few-shot | `schema` | position | |
| Few-shot | `body` | not stored; rebuilt as `Question: {summary}\nSQL:\n{sql}` | matches 4,857/4,857 |
| Few-shot | `bound_terms`, `complexity` | deleted | 0, no reader |
| Negative example | the type | deleted | 0 in BIRD and no consumer. `schema=None` (all schemas) has no home in a per-database file |

Owned lists keep the engine's field names (`id`, `summary`, `left_table`, `on`, ...), so the reader
is `{"asset_type": ..., **item}` plus what the position adds. The YAML 1.1 rule that reads an unquoted
key `on` as `true` still applies to the join items, so the reader keeps `store.py`'s loader that keeps
`on` a string.

Tested against `validate.py` (each case is a separate file; BIRD-scale results are in the header):

| Structure | Result |
|---|---|
| All 57 prototype files: nested `ai_context` objects, dataset-scoped metrics, multi-line extension JSON | pass, no warnings |
| `ai_context` objects with our keys on model, field and relationship | pass |
| Model-level metric with unqualified columns (`SUM(amount)`) | pass (the spec says to qualify; not checked) |
| Dataset-scoped metric naming an undeclared column | fails `[Scope]` |
| Dataset-scoped metric with the same name as a field | fails `[Unique]` |
| Relationship whose `from_columns` names no field | **passes**: field references are not checked |
| Relationship whose `to_columns` is not a declared key | warning only, passes |
| `synonyms` or `examples` holding objects | fails: both must be string lists |
| Extension `data` as a YAML mapping | fails: must be a string |
| Dataset with no fields | pass |
| Dialect `POSTGRES` | fails: not in the enum |

So few-shots cannot go in the standard `examples` key, and our loader must check relationship field
references itself.

## 4. What Ossie cannot express, and how extensions are written

**Joins that are not many-to-one.** many_to_one is a relationship as written. one_to_many is the
same relationship seen from the other side: it is stored with `from` as the many side and
`ai_context.cardinality: one_to_many`, and the reader swaps back. This matters because the prompt
prints `join L >< R on ... cardinality=...` in the authored direction. one_to_one is a valid
relationship (a unique key on the `to` side) with `ai_context.cardinality: one_to_one`.
many_to_many, no cardinality, a non-equality `on`, or a join across schemas cannot be a relationship
without misleading other tools. These go whole into the model's `ai_context.joins`.

A later choice, measured on its own: rewrite the 327 one_to_many joins as many_to_one. That changes
the prompt for those joins, so it is a corpus change with its own run, not part of the migration.

**Cardinality** lives in `ai_context.cardinality` only when it is not many_to_one, and on every
item in `ai_context.joins`.

**Ids.** Ossie identity is the name inside its scope. Every id the engine derives is derived again
on load: schema, table, column. The join id is the relationship `name`. All 706 BIRD join ids match
`join_id()`, but the name is stored, not recomputed, so editing `on` does not rename a
join. Metric ids are the metric `name`. Term and few-shot ids are an `id` key in their items: they
cannot be derived (453 of 603 term ids follow the name).

**Extensions.** At most one `GOVERNED_BI` entry per entity, written only when non-empty. `data` is
JSON with two-space indent, sorted keys and unescaped UTF-8, in a YAML literal block. A change to one
key is a one-line diff:

```yaml
  custom_extensions:
  - vendor_name: GOVERNED_BI
    data: |-
      {
        "audit": {
          "evidence": "equality between carreras and circuitos in train gold SQL",
          "provenance": {
            "source": "gold",
```

In BIRD that gives 1,783 entries (57 models, 656 datasets, 592 relationships, 478 metrics) and
about 26k lines, nearly all `audit`. Fields carry none, because BIRD columns have no audit. The Ossie
commit is not written into the files. `version` names the spec, and the commit is a constant in the
reader module, so a pin bump does not rewrite 57 files.

## 5. Migration plan

The change serves no current milestone: P15 delivered the exporter. The owner would add a milestone
and an ADR that supersedes 0020. Each step below is its own commit or PR.

| # | Step | Proof |
|---|---|---|
| 0 | ADR superseding 0020, with the placement table above. | Owner sign-off. |
| 1 | Check that asset order does not matter: build sessions from BIRD in load order and in sorted order, render contexts for the test questions offline, and compare `context_hash`. If it does matter, sort in `from_assets` first and prove that change the same way. | Equal context hashes. |
| 2 | Delete the fields nothing reads (`confidence`, `cost`, `complexity`, `bound_terms`, `related_terms`, `is_unique`, `sample_values`, column `references`) and the negative-example type, from the engine and from BIRD (22 `confidence` lines). | Suite green. BIRD loads 13,304 assets with 0 problems. Contexts unchanged, since none of these is rendered. |
| 3 | In BIRD, drop the trailing newline from the `books` schema summary, and add a load check that a summary joined into `description` has no newline. | Replay the `books` questions. The one expected difference is that summary's embedding. List it in the PR beforehand. |
| 4 | Add `corpus/ossie.py`: the reader, the canonical writer and the placement table, beside the YAML store. Add a one-shot conversion command. | Test 1: for BIRD, `read(write(load_yaml()))` equals `load_yaml()` (dataclasses, sorted by type and id; the order too if step 1 kept order). Test 2: `validate.py` passes every written file. Test 3: write, read, write gives the same bytes. Fixture tests for each table row BIRD lacks: `role`, `governance`, an unbound term, `grain`, a non-equality join, a one_to_one. |
| 5 | Convert BIRD in one commit: delete 7,357 asset files, add 57, add the `.gitattributes` line. | Test 1 against the converted tree and the YAML tree of the previous BIRD commit. Then a context replay over all 1,351 test questions at one engine commit, old tree against new: every `context_hash` equal. |
| 6 | Switch the engine. `store.load` reads Ossie; `from_live_schema` writes one file per schema; the YAML store, inline columns, `_shared`, `ossie_export.py` and its tests, and the conversion command are deleted. `check_train_only.py` stops building `{id}.yaml` paths. Rewrite `docs/corpus-format.md`. | Suite green. BIRD loads 13,304 assets with 0 problems. The step 5 replay repeated on the new loader. |
| 7 | Record in `docs/measurement.md`: the new BIRD commit and corpus hash, and that it holds the same definitions as `8ba312ed` apart from steps 2 and 3, with the test that shows it. | Review. |

The replay in steps 1, 3 and 5 renders context only: route, resolve, connect, assemble, with the
cached embeddings and no chat model. If some retrieval step calls a model, the replay covers the
first context of each question, and the asset equality test carries the rest. Equal assets with equal
order give equal engine behaviour, because past the loader the engine reads only the dataclasses.

**Corpus hash.** It changes at steps 2, 3 and 5. That is expected: it is a byte hash of the tree.
It is not part of `config_hash` (that hashes settings only), and `comparison_quotable` compares
`context_hash` and the settings, not the corpus hash. So a run on the new corpus is compared with the
P16 baseline by the repository's own rule, through `context_hash`. No new baseline run is needed. A
rerun would not prove more: the two P16 runs on the same corpus already disagreed on 11.3% of
questions, so only the context comparison can show the layer is the same.

I would not change the corpus hash to a hash of loaded definitions. It would survive the next format
change, but `snapshot.py` and drift detection want the bytes, and a second hash is a second answer.

## 6. Questions 8 to 10

**8. Should code names follow Ossie (`dataset`, `field`, `relationship`, `verified query`)?** No.
The engine's names reach the prompt (`table x`, `column`, `join`), and the prompt must stay
byte-identical. The engine's types are also not Ossie's. A join includes many_to_many, which an Ossie
relationship excludes. A column is a physical column, while an Ossie field may be computed. "Schema"
is a Postgres schema. One module, `corpus/ossie.py`, speaks Ossie. Renaming eight types across the
engine would be churn with no change in behaviour. The keys we add to `ai_context` use the engine's
names (`terms`, `joins`, `few_shots`) so a grep finds both sides.

**9. Should Ossie models written by other tools load, and with what defaults?** Not as a goal now:
there is one user and no such file. But every key we add is optional, so the same reader loads a plain
model that follows our three conventions: dataset `name` is the table name, `source` is
`<schema>.<table>`, and every field's expression is a bare column. Defaults:

| Missing | Default |
|---|---|
| `GOVERNED_BI` extension | no audit, not excluded |
| `ai_context.cardinality` | many_to_one |
| `datatype` | no logical type; the prompt shows no type |
| `physical_type`, `nullable` | absent; the inspect tool returns null |
| description | summary = the name. A summary outside the configured bounds is a load problem, as now |
| terms, few-shots, rules | none |
| entity `ai_context.synonyms` | ignored. Turning them into terms would invent summaries; do it only when a real file needs it |

Refuse with a load problem, not a guess: a computed field (the engine writes SQL against physical
columns), a dataset whose name is not its table or whose `source` is a query, a model-level metric
that names more than one dataset, and expressions in a dialect other than `ANSI_SQL` or
`OSSIE_SQL_2026`. Add `../ossie/examples/tpcds_semantic_model.yaml` as a fixture once loading foreign
files becomes a goal. It appears to meet the conventions, except for the source form `tpcds.public.store_sales`,
which needs a rule for three-part sources.

**10. How is a later spec change absorbed?** Pin the commit in one constant in `corpus/ossie.py`.
The existing test that runs `validate.py` keeps running against the BIRD files. On a bump:

1. Move `../ossie` to the new commit. Run the tests. If BIRD still validates, change the constant and
   stop.
2. If the document format changed, change the writer (and the reader, if a key moved). Rewrite BIRD
   with load, then write. Prove it with step 4's test 1 against the previous BIRD commit. The corpus
   hash moves; contexts do not.
3. When 0.2.0 is released, `version` changes in 57 files by the same route.

Because the engine sees only dataclasses, a spec change never reaches past `corpus/ossie.py`. What we
depend on, most fragile first: `ai_context` accepting extra keys, dataset-scoped metrics (added on
2026-10-21), the relationship structure, and `custom_extensions.data` as a string. If dataset-scoped
metrics go, all metrics move to the model with `ai_context.base_table`, a change in one function.

## 7. Risks, and what the brief got wrong

Risks:

- **Draft spec.** Covered by question 10. The worst case is `ai_context` closing, which is the first
  item under "what would change my mind".
- **Key collisions.** Ossie may later define an `ai_context` key we use (`terms`, `cardinality`) with
  another meaning. A test fails on the bump, and the fix is a rename in one module.
- **Spec deviations we keep.** 27 model-level metrics use unqualified or subquery expressions, and
  Postgres SQL is labelled `ANSI_SQL`, because Ossie has no Postgres dialect. `validate.py` accepts
  both. Fixing the 27 changes the prompt, so it is curation with its own run.
- **Whole-file failure.** A syntax error loses a database. Mitigated by the BIRD load test and the
  canonical-form test.
- **Hand edits.** A valid but non-canonical edit makes the next write noisy. The canonical-form test
  catches it before merge.
- **Other Ossie tools read our `ai_context`.** They will see `physical_type`, `nullable`,
  `reliability` and the few-shots. That is mostly useful. It is why `audit` stays in extensions.
- **The viewer.** The model's AI-context panel will list the few-shots: 255 in `works_cycles`.
  The viewer may want to fold long lists.
- **Seeding.** `seed.py` writes joins with bare table names and no cardinality, so seeded joins land
  in `ai_context.joins`, not in relationships. The writer must resolve bare names. Setting
  many_to_one in the seed is true by construction for a foreign key, but it changes seeded prompts,
  so it is a separate change.

Where the brief is wrong or incomplete:

1. *"a summary containing a blank line"* (`schema.summary`/`schema.body`, 1 each). There is no
   such summary. The loss is the `books` schema summary, which ends in a newline; joined with
   `\n\n` it splits one character late. The `soccer_2016` body has blank lines and round-trips fine.
2. *"That hash is part of the config hash every measurement records."* It is not. `config_hash`
   hashes the resolved settings (`registry/settings.py`, `serve/session.py`). `corpus_content_hash`
   is a separate row field, and the cross-config comparison does not use it. In P16 run A, 180 of
   1,351 rows have no corpus hash at all.
3. *What reads the files* misses `corpus/snapshot.py`, which looks for `.yaml`/`.yml` files.
   *What reaches the model* misses `serve/fetch.py`: `inspect_schema` shows `physical_type`,
   `nullable` and ids, and `read_body` shows any asset's `body`. So the exporter's `physical_type`
   loss would change answers.
4. *"validate.py checks ... relationship references."* It checks that the datasets exist and gives
   a warning when `to_columns` misses a declared key. It does not check that the columns are fields.
5. The few-shot `body` loss is recoverable: every one is exactly `Question: {summary}\nSQL:\n{sql}`.
6. *"a field whose expression is the quoted column name."* Quoted only when needed: 1 of 5,947.
7. Not wrong, but it decides much of the table: BIRD sets none of `role`, `governance`, `grain`,
   `row_count`, `cost`, `complexity`, `bound_terms`, `related_terms`, `is_unique`, `sample_values`
   or column `references`. `confidence` is set 22 times, and no column has `audit`.

I did not check the brief's count of 29 core-spec commits, or whether the Python package is on PyPI.
Neither affects the recommendation.
