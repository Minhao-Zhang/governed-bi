# corpus/

The layout and field tiers for the semantic layer's typed YAML assets. Spec:
[ADR 0005](../docs/adr/0005-v2-memory-layer-and-faceted-retrieval.md).
Serve loads from `GOVERNED_BI_CORPUS_DIR` or a directory under `corpora/`
(see [usage](../docs/usage.md)).

**This directory holds no assets, and it is not where the corpus lives.** The
served semantic layer is its own repository — [BIRD-corpus][corpus-repo], checked
out as a sibling and reached through `GOVERNED_BI_CORPUS_DIR=../BIRD-corpus`
(resolved against this repo's root, not the process's cwd). This file documents
the *format*; that repo holds the data.

**Closed 2026-08-07.** For the whole of v2 up to that date the paragraph here read:
*"the semantic layer is neither in version control nor reproducible from anything
committed"* — the corpus was an untracked directory under `corpora/`, so
`corpus_content_hash` named bytes no reader could obtain and no number this
repository produced was reproducible from a clean checkout. The gold layer
generated 2026-08-04 is now committed in BIRD-corpus, provenance and all, and a
fresh clone was verified to reproduce the same digest (`cfdf0bac…` over the 57
schema subtrees) as the tree that produced it.

Two things that were true then and are still true, so they are not quietly
retired with the rest: `corpora/` remains gitignored and still holds local
variants nobody else can reconstruct (§6.2 covers those, not the served corpus),
and there is still **no curator module in `src/`** — so this corpus is versioned
now, but it is not yet *rebuildable* from anything committed. Those are different
guarantees and only the first one has been obtained.

[corpus-repo]: https://github.com/Minhao-Zhang/BIRD-corpus

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
