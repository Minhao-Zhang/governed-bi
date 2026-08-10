# Corpus format

The layout and field tiers for the semantic layer's typed YAML assets. The specification is
[ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md); this page is the working
reference.

## Where the corpus is

The served semantic layer is its own repository,
[BIRD-corpus](https://github.com/Minhao-Zhang/BIRD-corpus). Check it out as a sibling of this
repository and point at it:

```bash
GOVERNED_BI_CORPUS_DIR=../BIRD-corpus
```

The path resolves against this repository's root, not the process's working directory. `.env`
already sets it.

**The corpus is the treatment identity of every measurement.** `corpus_content_hash` digests the
tree, so a number is reproducible only if the corpus commit is known. Quote the commit alongside
any figure, and write nothing into that checkout except assets — anything else becomes part of
the corpus's identity.

`corpora/` in this repository is gitignored and holds local experiment variants. Nothing there
can be rebuilt from anything committed, so treat a variant as evidence only for as long as the
directory survives, and promote it into the corpus repository before quoting a number from it.

The corpus is versioned and **not rebuildable**. `scripts/corpus_rebuild/01–03` write the
mechanical half — schema, table and column structure, join edges, few-shots — and leave every
summary as a `TODO` marker. The prose half has no producer anywhere in this repository. Versioned
and reproducible-from-source are different guarantees, and only the first one holds.

## Layout

```
<corpus root>/
  <schema>/
    tables/      tbl_<schema>_<name>.yaml
    joins/       join_<left>_<right>.yaml
    few-shots/   fs_<schema>_<n>.yaml
    terms/       term_<name>.yaml
    metrics/     metric_<name>.yaml
    notes/       note_<name>.yaml
    negatives/   neg_<schema>_<n>.yaml
```

`<schema>` is a schema namespace, not a database name. One connection may hold many schemas. The
loader and the asset ids both use the field name `schema`.

A `_generated/` directory under any schema holds derived projections — the search index,
embeddings, the compiled graph (ADR 0005 D9). Nothing authors those directly, so nothing commits
them.

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
