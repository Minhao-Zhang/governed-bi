# Corpus format

The semantic layer is a directory of [Apache Ossie](https://github.com/apache/ossie) models, one per schema. This repository loads it. It does not build it. The asset types are [ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md); the file format is [ADR 0022](adr/0022-ossie-is-the-storage-format.md).

## Where it lives

`GOVERNED_BI_CORPUS_DIR` names the directory. A relative path is resolved from this repository's root, not from the process's working directory. The default is `../BIRD-corpus`. The database in `GOVERNED_BI_PG_DSN` has to be the warehouse that corpus describes. The two move together.

The content hash of the tree is part of what a measurement records. Quote the corpus commit with any figure. Write nothing into that checkout except model files, because anything else changes the hash. The embedding cache lives outside the tree, at `GOVERNED_BI_VECTOR_CACHE`, which defaults to `runs/vectors`.

## Layout

```text
<corpus root>/
  <schema>/
    <schema>.yaml      one Ossie 0.2.0.dev0 document whose name is <schema>
```

`<schema>` is a schema name, not a database name. One connection can hold many schemas. The loader reads `<schema>/<schema>.yaml` and reports any other `.yaml` or `.yml` file instead of loading it. It skips `.git`, `.hg`, `.svn`, `__pycache__`, `.ipynb_checkpoints`, `.github`, and `.conformance`. `corpus/store.py` writes one schema's file with `write(root, schema, assets)`.

## Where each field goes

`corpus/ossie.py` is the only module that knows Ossie. A field Ossie defines holds a value when it means the same thing: the schema is the model, a table a dataset whose `source` is `<schema>.<table>`, a column a field whose expression is its name, and `logical_type` the field's `datatype`. Summary and body are one `description`, the summary, a blank line, then the body. Rules are `ai_context.instructions`, one per line, and `instructions` is the only `ai_context` key the corpus uses.

Everything else is in the JSON string of a `custom_extensions` entry whose `vendor_name` is `GOVERNED_BI`, on the entity it belongs to, under the engine's own field names: `physical_type`, `nullable`, roles other than primary key, reliability, grain, row count, a metric's display name and dimensions, governance and audit. Terms sit in the extension of the field, dataset or metric they bind to. Few-shots, and joins Ossie cannot express, are lists in the model's extension.

A join is an Ossie relationship when it is one-to-one, many-to-one or one-to-many, its `on` is column equalities between two tables of the schema, and its text rebuilds exactly from those columns. A one-to-many join is stored from the many side, with its cardinality in the extension, and read back as written. Many-to-many joins, joins with no cardinality, and the rest go in the model's `joins` list, never as relationships, because other tools read every relationship as many-to-one.

A metric whose expression names only its base table's fields is in that dataset's `metrics`. Any other metric is at model level, with `base_table` in its extension. Expressions are labelled `ANSI_SQL`; Ossie has no Postgres dialect.

The writer is canonical: keys in the spec's order, a literal block for any text with a newline, no line folding, and extension JSON indented with sorted keys. Writing, reading and writing again gives the same bytes, and a test checks that every BIRD file is in this form. The reader refuses any key it does not place, so a typo fails the load. The full placement table is section 3 of the [plan](reviews/2026-10-workspace-and-ossie-storage.md#3-where-each-field-goes).

To make each diff hunk name its dataset or field, BIRD-corpus marks `*.yaml diff=ossie`, and each clone sets:

```bash
git config diff.ossie.xfuncname "^(- name: .*|  - name: .*)$"
```

The format was checked against `apache/ossie` at `d85e9e1`, the constant `OSSIE_COMMIT` in `corpus/ossie.py`. Models written by other tools are not read.

## The seven asset types

The types are declared in `src/governed_bi/registry/assets.py`. Each one has `summary` and `body`. `governance` can hide an asset. `audit` records why an authored field was written, and it stays out of the text the model sees.

| Type | What it holds |
|---|---|
| Schema | The schema name, `summary`, `body`, and `rules` |
| Table | `physical_name`, an optional grain and row count, `rules`, and the column ids |
| Column | The parent table, `physical_name`, `physical_type`, nullability, a role, and a reliability note. `suspect` marks the column unreliable |
| Join | The two tables, the `on` expression, and an optional cardinality |
| Metric | A name, a base table, and an `expression` |
| Term | A business word, its synonyms, and the asset it binds to |
| Few-shot | A question the layer can answer, with the SQL |

A few-shot's `body` is built from its question and SQL and is not stored. A summary of a schema, table, column, join or metric, and each rule, is one line: the loader reports a newline in one.

`GET /examples` serves the few-shot questions. It does not send the SQL.
