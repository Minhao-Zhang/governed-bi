# data/

> **This document describes v1, which was deleted in commit `2347ae3`.** It is kept at this path
> because it is an entry point to the repository, and it is being rewritten against
> [ADR 0005](../docs/adr/0005-v2-memory-layer-and-faceted-retrieval.md) and
> [ADR 0006](../docs/adr/0006-execution-time-governance.md). Until that rewrite lands,
> treat every specific claim below — module names, file paths, tool names, measured
> numbers — as historical rather than current. The rest of the v1 documentation is
> in [`docs/v1/`](../docs/v1/), and [`lessons-from-v1.md`](../docs/lessons-from-v1.md) records which of its
> measurements survived re-examination and which were retired.

_[English](README.md) · [简体中文](README.zh.md)_

A small, real BIRD SQLite database, vendored **only as a demo and test/CI
fixture**.

> **Not a starting point for real work.** This DB exists so the offline demo,
> the walkthrough, and the test suite have something concrete to run against
> with zero setup. If you are building an actual BI deployment, do **not** build
> on `beer_factory` — point `[datasource]` at your own database (in a git-ignored
> `governed_bi.local.toml`) and author a corpus for *that* schema under
> `corpus/<schema>/`. Leave this fixture in place as the demo/CI backbone.

## What's here

`bird/beer_factory.sqlite`: the `beer_factory` database from the BIRD benchmark
(0.95 MB, **7 physical tables** in the SQLite file). The worked example corpus
under `corpus/beer_factory/` covers a **subset** of those tables (not a 1:1
mirror). Included **unmodified** under CC BY-SA 4.0; attribution and license in
[`bird/NOTICE`](bird/NOTICE). It is **not** covered by the repo's MIT license.

Intentionally excluded: BIRD's `database_description/` CSVs (human-written column
descriptions) and every other BIRD database. The descriptions are left out on
purpose, since inferring meaning is the curator's job.

It is the **un-obfuscated (base)** DB with real table/column names. That is why
the test suite and CI point at it: real names + real rows exercise the engine's
mechanics (catalog introspection, Facts profiling, physical-existence checks,
real query execution) deterministically. For the same reason it is a poor stand-in
for real work — it does not exercise the curator's core job (inferring meaning for
cryptic names), so the moat evaluation uses the obfuscated `rename_decoy` variant +
manifests from BIRD-Obfuscation instead.

## Adding another DB later

Keep the ~5 MB soft cap: under it, commit the `.sqlite` directly; over it, use
Git LFS or a fetch script. Add attribution for each new file to a `NOTICE`, and
commit only the DBs you actually use (not the full BIRD dev set).

## Generated corpus output

Profiling a DB (and, later, running the curator) writes corpus YAML into
[`generated/`](generated/) by convention, for example
`data/generated/beer_factory/tables/*.yaml`. That directory is a rebuildable
staging area and is gitignored; the curated, human-audited corpus lives in
`corpus/<schema>/` (D15 renamed the on-disk namespace from `<db>` → `<schema>`;
shipped). See [`generated/README.md`](generated/README.md).

## Using it

```python
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.curator.profile import profile_database

conn = SqliteConnector("data/bird/beer_factory.sqlite")   # opens read-only
facts = profile_database(conn, schema="beer_factory")     # Facts-tier table assets
gw = Gateway(conn)
rows = gw.execute("SELECT COUNT(*) FROM customers", Identity(user="dev", all_access=True))
```

The connector, gateway, and profiler have unit tests that build their own
temporary SQLite (`tests/test_connector.py`); those same tests also run an
integration check against `beer_factory.sqlite` when it is present.
