# Writer brief — one schema of the BIRD corpus

You are writing the semantic layer for **one** database schema. The structure already exists;
you are filling in the words. A validator decides when you are done.

## The one thing to understand first

Two fields, two different jobs, and they do not overlap:

| field | who reads it |
|---|---|
| `summary` | **the retrieval index, and only the index.** BM25 and the embedding both score this string. The model never sees it. |
| `body` | **the model, and only the model.** Rendered when the asset is retrieved. The model never sees the summary, so a body that assumes the summary was read is broken. |
| `reliability.note` | the model, in its own block, and the context budget can never drop it. |

So: write `summary` to be **found** by someone asking a question in business language. Write
`body` to be **enough on its own** to use the thing correctly.

## Your inputs

- `scripts/corpus_rebuild/_build/packets/<schema>.json` — everything you are given:
  - `bird_documentation` — the dataset's own per-column notes. Short (median 28 characters) and
    9% of them just restate the column name. **A starting point, not a description.**
  - `value_samples` — up to five real values per column, plus min and max.
  - `evidence_clauses` — business phrasings harvested from training questions, classified by
    shape. Raw material for terms and metrics.
  - `unreliable_tables`, `unreliable_columns`, `unreliable_join_keys` — see below.
- The scaffold files under `../BIRD-corpus/<schema>/`, which you edit in place.

**Do not open** `test_final.jsonl`, any `gold_result_hashes*` file, or
`question_paraphrases.jsonl`. Everything else in `BIRD-Data-Obfuscation` is fair game if you
want more context.

## What to write

### 1. Every table file, `../BIRD-corpus/<schema>/tables/*.yaml`

Replace the table's `summary` and `body`, and the `summary` and `body` of **every inline
column**. Do not add, remove or reorder columns. Do not add `id`, `schema` or `parent_table` to
a column — those are derived, and supplying one is an error.

- Table `summary`: what this table is, containing its `physical_name`. One sentence.
- Table `body`: what it is, its grain (one row per what?), what it is easily confused with,
  and how it connects to its neighbours.
- Column `summary`: what the column is, containing its bare `physical_name`. A sentence or a
  clean noun phrase. **No example values here.**
- Column `body`: the value domain — units, format, the code table if it has one, how it differs
  from its siblings. **This is where the sample values go.**

### 2. The schema file, `../BIRD-corpus/<schema>/<schema>.yaml`

- `summary`: what this database is *for*, containing the schema name. **250 characters, same as
  every other asset** — the cap is one global knob enforced in the model, not a per-type one.
  This single string is what routes a question to this database out of 57, so spend all 250 on
  the domain vocabulary a user would actually say, not on a list of table names.
- `body`: business background and cross-table conventions.
- `rules`: a short list of hard constraints that must hold on every query — a join path that is
  easy to get wrong, a filter that is always required. Few and load-bearing, or none at all.
  These render every turn and are never dropped, so a long list is a tax on every question.

### 3. Every join file, `../BIRD-corpus/<schema>/joins/*.yaml`

`summary` and `body`, and `cardinality` when you can tell from the data
(`one_to_one`, `one_to_many`, `many_to_one`, `many_to_many`).

Do not change `id`, `on`, `left_table` or `right_table`. **The summary must contain the last
component of `left_table` and of `right_table` exactly as they are spelled there** — for most
tables that is the plain name, but a few carry a suffix like `Air_Carriers_66c534`.

### 4. Terms and metrics — new files you create

From `evidence_clauses`. Write to `../BIRD-corpus/<schema>/terms/term_<schema>_<slug>.yaml` and
`../BIRD-corpus/<schema>/metrics/metric_<schema>_<slug>.yaml`.

**Deduce; do not transcribe.** A clause is how one training question happened to be phrased.
Several clauses usually describe one concept — merge them into one asset whose `synonyms` carry
every phrasing you saw. A clause like `above-average X refers to X > DIVIDE(SUM(X), COUNT(...))`
is a metric definition wearing a filter's clothes: extract the metric, put the comparison in
`body` as a boundary condition.

```yaml
asset_type: term
id: term_<schema>_<slug>            # [A-Za-z0-9_]+, unique in the schema
name: gross merchandise value
summary: "GMV (gross merchandise value, total transaction value): the total value of goods
  transacted in a period."          # MUST contain every synonym, verbatim
synonyms: [GMV, gross merchandise value, total transaction value]
binding: {target_type: column, target_id: <schema>.<table>.<column>}
body: Full definition and boundary conditions.
audit: {provenance: {source: curator, status: draft, version: rebuild-1,
        source_refs: [train_final.jsonl]}}
```

```yaml
asset_type: metric
id: metric_<schema>_<slug>
name: average elevation
base_table: <schema>.<table>
expression: AVG(elevation)
summary: "average elevation: the mean elevation across zip codes."
body: Full definition, boundary conditions, common mistakes.
audit: {provenance: {source: curator, status: draft, version: rebuild-1,
        source_refs: [train_final.jsonl]}}
```

`binding.target_id` and `base_table` must name an asset that exists. An unbound term casts no
vote in routing.

### 5. Unreliable tables and columns

Some columns hold data that does not correspond to the business facts the table records. The
packet names them. For each one:

```yaml
reliability:
  status: suspect
  note: "Unreliable for analysis. Do not use this column to answer questions."
```

Use that sentence verbatim, on every affected column.

Three hard rules about how you write them:

1. **Never explain why.** Do not write `decoy`, `trap`, `fabricated`, `synthetic`, `planted`,
   `mimic` or `imitate` anywhere. The validator rejects those words.
2. **Never name a column it resembles.** If `postal_code` is unreliable and `zip_code` is the
   real one, `postal_code`'s summary must not contain `zip_code`. This is not discretion — the
   summary is the search index, so naming the real column makes the unreliable one rank for
   that column's questions and take its slot.
3. **Keep its `summary` thin.** It must be non-empty and must contain the column's
   `physical_name`, so the model can spell it. It must not be written to win a search. Say what
   it is called and what it holds; do not give it the domain vocabulary of a real column.

```yaml
# real column
summary: The alternate place or city name recorded for a ZIP code's postal area (alias).
body: One alias per zip_code. Join to zip_data on zip_code.

# unreliable column in the same table
summary: alt_alias, a text column on the alias table.
body: >
  Values in this column are not dependable and do not correspond to the business facts the
  table otherwise records. Do not use it in a SELECT list, a filter, or a join key.
reliability:
  status: suspect
  note: "Unreliable for analysis. Do not use this column to answer questions."
```

For a column in `unreliable_join_keys`, add one clause to `body`: it must not be used as a join
key. For a table in `unreliable_tables`, mark **every** column of it, and write the table's own
body to say the table is not dependable.

## How you know you are done

```bash
uv run python tools/check_corpus_conformance.py --file <path-to-the-file-you-just-wrote>
```

Run it after every file. Exit 0 or fix it and run again. When the whole schema is written:

```bash
uv run python tools/check_corpus_conformance.py --corpus-dir ../BIRD-corpus/<schema>
```

That must report **zero** violations on every rule it evaluates. The rules, in short:

| | |
|---|---|
| V1 | summary is 1–250 characters, every type |
| V2 | summary is not still `TODO ...` |
| V3 | summary contains the asset's own identifier |
| V4 | summary is prose — function-word ratio ≥ 0.10, and not a template or a list of names |
| V5 | summary has no `e.g.`, no `such as`, no quoted literal, no `(column x)` tail |
| V6 | body is non-empty |
| V7 | a column body is not a tautology like `Means 'x'` |
| V8 | a term's summary contains every one of its synonyms |
| V9 | `binding.target_id`, `base_table` and join endpoints resolve |
| V10 | none of the forbidden words |
| V11 | an unreliable column's summary does not name the column it resembles |
| V13 | no file over 32 KB |
| V14 | the engine's loader accepts the file |

V4 is the one that will catch you. `alias (alias): zip_code, alias` fails. `The alternate place
name recorded for a ZIP code` passes. Write English sentences.

Do not edit the validator. Do not edit files outside `../BIRD-corpus/<schema>/`.

## Report back

One paragraph: tables written, columns written, terms and metrics created, and the final
validator output. If any rule would not go to zero, say which and why rather than working
around it.
