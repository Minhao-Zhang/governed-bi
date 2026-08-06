# data/

Vendored BIRD SQLite used as a **demo and test/CI fixture**, not as the default
LangGraph serve datasource (serve expects Postgres — see [usage](../docs/usage.md)).

## What's here

`bird/beer_factory.sqlite`: the `beer_factory` database from the BIRD benchmark
(~0.95 MB), included unmodified under CC BY-SA 4.0; attribution in
[`bird/NOTICE`](bird/NOTICE). Not covered by the repo MIT license.

## Generated output

[`generated/`](generated/) is a rebuildable staging area (gitignored). Curated
corpora that people accept live under `corpora/` or whatever path
`GOVERNED_BI_CORPUS_DIR` points at.
