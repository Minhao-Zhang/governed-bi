# corpus/

The **semantic layer** is the moat. Git-tracked plain Markdown + YAML typed
assets, curator-authored / human-audited (D9). **Git is the single source of
truth.** Every other store (in-memory graph, vector, BM25, Postgres) is a
derived, rebuildable projection under `_generated/`, never authored directly.

Full spec: [`docs/asset-schemas.md`](../docs/asset-schemas.md).

## Layout

```
corpus/
  <db>/
    tables/      tbl_<db>_<name>.yaml      # columns inline
    joins/       join_<left>_<right>.yaml
    few-shots/   fs_<db>_<n>.yaml
    terms/       term_<name>.yaml
    metrics/     metric_<name>.yaml
    rules/       rule_<name>.yaml
    negatives/   neg_<db>_<n>.yaml
    skills/      *.md                        # prose gotchas / query-patterns
  _generated/    # search index, embeddings, compiled graph (gitignored)
```

`california_schools/` is a small **worked example** taken from the schema spec;
it exists so the loader + CI validator have something real to run on. It is not
a full corpus.

## Field tiers

Every asset splits into **Facts** (catalog truth, never inferred), **Inference**
(the semantic layer the curator writes / gold fills), and **Audit** (why, never
injected into the server context). Plus a human-only **Governance** override.

## Validate

```bash
uv run python -m governed_bi.corpus.cli corpus/california_schools
```

A green run (ID conventions + reference integrity) is the curator's
machine-checkable "done-enough" signal.
