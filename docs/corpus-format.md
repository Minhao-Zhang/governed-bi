# Corpus format

The semantic layer is a directory of YAML files. This repository loads it. It does not build it. The specification is [ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md). The export to Ossie is [ADR 0020](adr/0020-ossie-export-and-a-separate-viewer.md).

## Where it lives

`GOVERNED_BI_CORPUS_DIR` names the directory. A relative path is resolved from this repository's root, not from the process's working directory. The default is `../BIRD-corpus`. The database in `GOVERNED_BI_PG_DSN` has to be the warehouse that corpus describes. The two move together.

The content hash of the tree is part of what a measurement records. Quote the corpus commit with any figure. Write nothing into that checkout except assets, because anything else changes the hash. The embedding cache lives outside the tree, at `GOVERNED_BI_VECTOR_CACHE`, which defaults to `runs/vectors`.

## Layout

A checkout such as BIRD-corpus is arranged like this:

```text
<corpus root>/
  <schema>/
    <schema>.yaml
    tables/
    joins/
    few-shots/
    terms/
    metrics/
```

`<schema>` is a schema name, not a database name. One connection can hold many schemas. Columns are written inside their table file, and the loader gives each column an id. There is no column directory.

That arrangement is a convention. The loader reads every `.yaml` file under the root, wherever it sits. It skips `.git`, `.hg`, `.svn`, `__pycache__`, `.ipynb_checkpoints`, `.github`, and `.conformance`. A file with another suffix is reported and not loaded. The writer, `corpus/store.py`, writes `<root>/<namespace>/<asset id>.yaml` with no further subdirectory. On the way out, the namespace has to be a bare identifier. An asset id has to be a legal filename.

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

## Export to Ossie

```bash
uv run --frozen python -m governed_bi.corpus.ossie_export --corpus-dir <corpus> --out <dir>
```

Both flags are required. The command writes `<dir>/<database>.yaml`, one Ossie `0.2.0.dev0` model per database, and `<dir>/export-report.json`. It prints the totals, and it exits 1 when the corpus has load problems. Write the output under `runs/`, or outside this repository.

The model records Ossie commit `d85e9e1` in a `custom_extensions` entry whose `vendor_name` is `GOVERNED_BI`. Ossie relationships are many-to-one and carry no cardinality, so the exporter writes cardinality into that extension. Joins it cannot express as a relationship, joins with no cardinality, terms it cannot bind, and metric expressions sqlglot cannot parse are counted in the report. The mapping in full is in ADR 0020.

Reading an Ossie file as the corpus the server loads is not implemented. The server reads this YAML layout.
