# corpus/

_[English](README.md) · [简体中文](README.zh.md)_

Git-tracked typed YAML assets for the semantic layer. Spec:
[ADR 0005](../docs/adr/0005-v2-memory-layer-and-faceted-retrieval.md).
Serve typically loads from `GOVERNED_BI_CORPUS_DIR` or a directory under
`corpora/` (see [usage](../docs/usage.md)).

## Layout

```
corpus/   (or corpora/<name>/)
  <schema>/
    tables/      tbl_<schema>_<name>.yaml
    joins/       join_<left>_<right>.yaml
    few-shots/   fs_<schema>_<n>.yaml
    terms/       term_<name>.yaml
    metrics/     metric_<name>.yaml
    notes/       note_<name>.yaml
    negatives/   neg_<schema>_<n>.yaml
```

`<schema>` is a schema namespace, not a database name. A connection may hold many
schemas. Loader and asset IDs use the field name `schema`.

## Field tiers

Assets split into **Facts** (catalog truth), **Inference** (authored semantics),
and **Audit** (not injected into model context), plus optional **Governance**
overrides.

## Validate

Use the corpus package APIs / tests under `src/governed_bi/corpus/` and
`tests/corpus/`. There is no `governed_bi.corpus.cli` module in this tree.
