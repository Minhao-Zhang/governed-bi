# data/

A small, real BIRD SQLite database, vendored for development and tests.

## What's here

`bird/beer_factory.sqlite`: the `beer_factory` database from the BIRD benchmark
(0.95 MB, 7 tables). Included **unmodified** under CC BY-SA 4.0; attribution and
license in [`bird/NOTICE`](bird/NOTICE). It is **not** covered by the repo's MIT
license.

Intentionally excluded: BIRD's `database_description/` CSVs (human-written column
descriptions) and every other BIRD database. The descriptions are left out on
purpose, since inferring meaning is the curator's job.

It is the **un-obfuscated (base)** DB with real table/column names, which is what
you want for building and verifying the engine (catalog introspection, Facts
profiling, physical-existence checks, real query execution). It does not exercise
the curator's core job (inferring meaning for cryptic names), so the moat
evaluation still needs the obfuscated `rename_decoy` variant + manifests from
BIRD-Obfuscation later.

## Adding another DB later

Keep the ~5 MB soft cap: under it, commit the `.sqlite` directly; over it, use
Git LFS or a fetch script. Add attribution for each new file to a `NOTICE`, and
commit only the DBs you actually use (not the full BIRD dev set).

## Generated corpus output

Profiling a DB (and, later, running the curator) writes corpus YAML into
[`generated/`](generated/) by convention, for example
`data/generated/beer_factory/tables/*.yaml`. That directory is a rebuildable
staging area and is gitignored; the curated, human-audited corpus lives in
`corpus/<db>/`. See [`generated/README.md`](generated/README.md).

## Using it

```python
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.curator.profile import profile_database

conn = SqliteConnector("data/bird/beer_factory.sqlite")   # opens read-only
facts = profile_database(conn, db="beer_factory")          # Facts-tier table assets
gw = Gateway(conn)
rows = gw.execute("SELECT COUNT(*) FROM customers", Identity(user="dev", all_access=True))
```

The connector, gateway, and profiler have unit tests that build their own
temporary SQLite (`tests/test_connector.py`); those same tests also run an
integration check against `beer_factory.sqlite` when it is present.
