# data/generated/

> **This document describes v1, which was deleted in commit `2347ae3`.** It is kept at this path
> because it is an entry point to the repository, and it is being rewritten against
> [ADR 0005](../../docs/adr/0005-v2-memory-layer-and-faceted-retrieval.md) and
> [ADR 0006](../../docs/adr/0006-execution-time-governance.md). Until that rewrite lands,
> treat every specific claim below — module names, file paths, tool names, measured
> numbers — as historical rather than current. The rest of the v1 documentation is
> in [`docs/v1/`](../../docs/v1/), and [`lessons-from-v1.md`](../../docs/lessons-from-v1.md) records which of its
> measurements survived re-examination and which were retired.

_[English](README.md) · [简体中文](README.zh.md)_

Default target for machine-generated corpus output: profiled Facts assets today,
curator drafts later. Written by `governed_bi.corpus.write_corpus(...)`.

This is a staging area, not the source of truth. It is gitignored because it is
rebuildable from the database (`profile_database` is deterministic). The curated,
human-audited corpus that a person accepts lives in `corpus/<schema>/` (D15
renamed the on-disk namespace from `<db>` → `<schema>`; shipped) and is
committed there (D9).

Typical layout after profiling a DB:

```
data/generated/beer_factory/
  tables/tbl_beer_factory_customers.yaml
  tables/tbl_beer_factory_transaction.yaml
  ...
```

Regenerate it any time:

```python
from governed_bi.gateway import SqliteConnector
from governed_bi.curator.profile import profile_database
from governed_bi.corpus import write_corpus

conn = SqliteConnector("data/bird/beer_factory.sqlite")
write_corpus(
    "data/generated",
    "beer_factory",
    profile_database(conn, schema="beer_factory"),
)
```
