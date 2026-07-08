# data/generated/

Default target for machine-generated corpus output: profiled Facts assets today,
curator drafts later. Written by `governed_bi.corpus.write_corpus(...)`.

This is a staging area, not the source of truth. It is gitignored because it is
rebuildable from the database (`profile_database` is deterministic). The curated,
human-audited corpus that a person accepts lives in `corpus/<db>/` and is
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
write_corpus("data/generated", "beer_factory", profile_database(conn, "beer_factory"))
```
