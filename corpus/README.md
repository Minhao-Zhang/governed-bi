# corpus/

_[English](README.md) · [简体中文](README.zh.md)_

The **semantic layer** is the moat. Git-tracked plain Markdown + YAML typed
assets, curator-authored / human-audited (D9). **Git is the single source of
truth.** Every other store (in-memory graph, vector, BM25, Postgres) is a
derived, rebuildable projection under `_generated/`, never authored directly.

Full spec: [`docs/asset-schemas.md`](../docs/asset-schemas.md).

## Layout

```
corpus/
  <schema>/
    tables/      tbl_<schema>_<name>.yaml      # columns inline
    joins/       join_<left>_<right>.yaml
    few-shots/   fs_<schema>_<n>.yaml
    terms/       term_<name>.yaml
    metrics/     metric_<name>.yaml
    notes/       note_<name>.yaml            # governed annotations (D17)
    negatives/   neg_<schema>_<n>.yaml
  _generated/    # search index, embeddings, compiled graph (gitignored)
```

> **D15:** the `<schema>` level is a **schema** namespace, not a database — a run's
> database (a connection-config constant, not a modeled corpus level) may hold
> many schemas. On-disk YAML and load/write APIs use the field/param name
> `schema` (hard cut from `db`). Asset IDs are unchanged.

`beer_factory/` is the **worked example**, authored over the real BIRD
`beer_factory` database (`data/bird/beer_factory.sqlite`). It exercises every
asset type and validates against that DB (physical-existence). Use it as the
reference for authoring your own.

## Field tiers

Every asset splits into **Facts** (catalog truth, never inferred), **Inference**
(the semantic layer the curator writes), and **Audit** (why, never
injected into the Analyst context). Plus a human-only **Governance** override.

## Validate

```bash
uv run python -m governed_bi.corpus.cli corpus/beer_factory
```

A green run (ID conventions + reference integrity) is the curator's
machine-checkable "done-enough" signal.
