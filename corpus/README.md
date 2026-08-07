# corpus/

The layout and field tiers for the semantic layer's typed YAML assets. Spec:
[ADR 0005](../docs/adr/0005-v2-memory-layer-and-faceted-retrieval.md).
Serve loads from `GOVERNED_BI_CORPUS_DIR` or a directory under `corpora/`
(see [usage](../docs/usage.md)).

**This directory holds no assets, and nothing in it is under version control.**
`git ls-files corpus corpora` returns two paths: this file and `.gitignore`. The
demo assets that used to live here were removed in `a506436` and this document
went on calling the directory "Git-tracked typed YAML assets" for another week —
along with `corpus/.gitignore` and the root `.gitignore`, which carved an
exception to keep tracking a corpus that no longer existed. All three now say
what is true.

So: **the semantic layer is neither in version control nor reproducible from
anything committed.** `corpora/` is ignored wholesale, and the root `.gitignore`
called those trees "reproducible from BIRD-Data-Obfuscation" while there is still
no curator module in `src/`. That is an open problem, recorded here rather than
papered over — the corpus is the treatment every experiment in this repository
compares, and it is currently a local directory nobody else can reconstruct.

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
