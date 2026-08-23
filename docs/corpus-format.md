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
| **Served today** | `../MS Fabric Facilities/corpus` — the facilities warehouse's flattened serving layer, one namespace, against the `facilities` Postgres on 5432 |
| **What the benchmark numbers were measured on** | [`../BIRD-corpus`](https://github.com/Minhao-Zhang/BIRD-corpus) — commented out in `.env`, still checked out |

`GOVERNED_BI_CORPUS_DIR` and `GOVERNED_BI_PG_DSN` are **one switch**: a corpus describes one
warehouse, so swapping the corpus without swapping the DSN points the engine's semantic layer
at tables that are not there. `.env` says so at the line above the pointer.

**The corpus is the treatment identity of every measurement.** `corpus_content_hash` digests the
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
`negative_example` has none because the shipped BIRD-corpus contains not one — so `negative_gate`
never fires on it, and any measurement of that rail is a measurement of a different corpus.

Nothing derived is written into the corpus, because the corpus tree is the treatment identity.
The vector store lives outside it, under `GOVERNED_BI_VECTOR_CACHE` (default `runs/vectors/`).

## Field tiers

Assets split into three tiers, plus optional governance overrides:

| Tier | What it holds |
|---|---|
| Facts | Catalog truth, read from the database |
| Inference | Authored semantics |
| Audit | Provenance and review state; never injected into model context |

## Validate

Use the corpus package under `src/governed_bi/corpus/` and the tests in `tests/corpus/`. There is
no `governed_bi.corpus.cli` module.

To check a corpus against the conformance rules:

```bash
uv run --frozen python tools/check_corpus_conformance.py
```

The rule ids and their descriptions live in that file's `RULES` table, which is what the report
prints; this document does not keep a second copy. Two of them bound size, and the split between
them is the part worth knowing:

| rule | measures | cap |
|---|---|---|
| V13 | one asset's **body** | 4,000 for `few_shot`, else 8,000 |
| V16 | a table's **rendered closure** — structural line, body, and the roster its columns fold into | 20,000 |

**Neither measures the file, and that is deliberate (2026-08-13).** V13 measured
`path.stat().st_size` until then, on the stated grounds that an asset too big for the context
block is not deliverable — but the file is not the delivery unit. `corpus/store.py` splits a
table's inline columns into their own assets and leaves the parent holding a list of ids, and
nothing on the serve path reads the YAML. Measured on the facilities corpus, the six files that
failed the old 32,000-byte cap deliver 3,871–8,435 characters; file size overstated the real cost
by 7.7× on the worst of them. The rule was in practice firing on column count, which is a fact
about a schema rather than a defect.

V16 exists because a per-asset cap cannot see an aggregate. A roster entry runs about 53
characters, so a 1,500-column table would render the whole 80,000-character
`context_budget_chars` while every individual asset sat comfortably under V13. It is measured
with `serve/context.py`'s own `_structural_line` and `_roster_entry` rather than a second copy of
that arithmetic, so the rule cannot drift from the renderer it claims to bound.
