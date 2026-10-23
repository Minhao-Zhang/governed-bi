# Corpus format

The layout and field tiers for the semantic layer's typed YAML assets. The specification is
[ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md); this page is the working
reference.

## Where the corpus is

The semantic layer is not in this repository. `GOVERNED_BI_CORPUS_DIR` names a sibling
checkout, and the path resolves against this repository's root, not the process's working
directory.

**Two trees are in play, and they are not interchangeable.** Read `.env` to see which one
is live rather than assuming:

| | |
|---|---|
| **Served today** (since 2026-08-23) | [`../BIRD-corpus`](https://github.com/Minhao-Zhang/BIRD-corpus), together with `GOVERNED_BI_PG_DSN` on port 5435 — `docker compose up pg_rename_decoy` in `../BIRD-Data-Obfuscation`. 13,304 assets, 0 problems, content hash `6e5c7b4be83d5682` at that repo's HEAD |
| **Retired as the served tree** | `../MS Fabric Facilities/corpus` — the facilities warehouse's flattened serving layer, one namespace, the `facilities` Postgres on 5432. Both `.env` lines are commented out and they are one switch |

**Why the swap, because it is a standing choice and not a benchmark detour.**
**`../MS Fabric Facilities` is not a git repository** — it carries a `.gitignore`
written for one and no `.git`, so a landed change there cannot be told apart from a superseded one.
Its recorded identity does not reproduce either: `.env` claimed content hash `2f2b296e321d89ba` for
1,432 assets, and while the asset count checks out the digest measures `ddabcc43dc32b4a5…`
unrestricted and `8fb6e79f4008d7de` under `schemas=["facilities"]`. With no history, nothing can say
whether the tree moved or the note was wrong when it was written — which is the argument for
versioning a corpus, made by the one that was not.

`GOVERNED_BI_CORPUS_DIR` and `GOVERNED_BI_PG_DSN` are **one switch**: a corpus describes one
warehouse, so swapping the corpus without swapping the DSN points the engine's semantic layer
at tables that are not there. `.env` says so at the line above the pointer.

**The corpus is the identity of what was measured of every measurement.** `corpus_content_hash` digests the
tree, so a number is reproducible only if the corpus commit is known — which is why the table
above matters: a BIRD figure re-run against the facilities corpus is a different experiment, not
a replication. Quote the commit alongside any figure, and write nothing into that checkout except
assets — anything else becomes part of the corpus's identity.

The corpus is versioned and **not rebuildable from this repository**. This engine loads the
sibling checkout; producing that tree is not this project's job. Mechanical structure and
prose both live there. Versioned and reproducible-from-source are different guarantees, and
only the first one holds.

## Layout

```
<corpus root>/
  <schema>/
    <schema>.yaml                 the SchemaAsset
    tables/      tbl_<schema>_<name>.yaml      columns live inline, ids derived
    joins/       join_<schema>_<left>_<right>_<on digest>.yaml
    few-shots/   fs_<schema>_<n>.yaml
    terms/       term_<schema>_<name>.yaml
    metrics/     metric_<schema>_<name>.yaml
```

`<schema>` is a schema namespace, not a database name. One connection may hold many schemas. The
loader and the asset ids both use the field name `schema`.

**The subtrees are a convention, not a rule.** `corpus/store.py::load` walks the whole tree and
reads every `.yaml` it finds wherever it sits, and `corpus/store.py::write` writes
`<root>/<namespace>/<id>.yaml` with no subdirectory at all — so the layout above is the
convention the served checkout uses, and nothing on the read path checks it. What the loader does
enforce is per asset: `corpus/identity.py::validate_asset_id` on every id, because an id becomes a
filename. The namespace directory is validated on the **write** path only
(`identity.py::validate_path_component`, a bare identifier), and the manifest passed to `load`
decides which subtrees are read at all — a schema left out of it is not read, so a leftover
subtree cannot enter a measurement.

Six of the eight asset types appear above. `column` has no directory because columns are authored
inline under their table and `corpus/identity.py::derive_column_id` mints their ids.
`negative_example` has none because the shipped BIRD-corpus contains not one — so the
negative-example check never fires on it, and any measurement of that rail is a measurement of a different corpus.

Nothing derived is written into the corpus, because the corpus tree is the identity of what was measured.
The vector store lives outside it, under `GOVERNED_BI_VECTOR_CACHE` (default `runs/vectors/`).

## Field tiers

Assets split into three tiers, plus optional governance overrides:

| Tier | What it holds |
|---|---|
| Facts | Catalog truth, read from the database |
| Inference | Authored semantics |
| Audit | Provenance and review state; never injected into model context |

## Export to Ossie

```
uv run --frozen python -m governed_bi.corpus.ossie_export --corpus-dir <corpus> --out <dir>
```

Writes `<dir>/<database>.yaml`, one Ossie `0.2.0.dev0` model per database (mapping in
[ADR 0020](adr/0020-ossie-export-and-a-separate-viewer.md)), and `<dir>/export-report.json`. The
report counts what did not map: many-to-many joins, joins with no cardinality, joins whose `on` is
not an AND of column equalities, and metric expressions sqlglot cannot parse. Ossie's
`validate.py` rejects those metric expressions, so a model that holds one fails it. The extension
`GOVERNED_BI` records the Ossie commit. `round_trip_loss` in the same module lists what the
exporter's own reader does not bring back. The test runs `../ossie/validation/validate.py` and
skips when `../ossie` is absent. Write exports outside the repository tree, or under `runs/`.

## Validate

Use the corpus package under `src/governed_bi/corpus/` and the tests in `tests/corpus/`. There is
no `governed_bi.corpus.cli` module.

Held-out question text is checked by `tools/check_train_only.py`, which needs a corpus, the
question file, and a train-only control, and by `tools/check_no_benchmark_discriminators.py`,
which needs no data. The train-only check includes the rule that an asset's summary or body
must not quote a held-out question.
