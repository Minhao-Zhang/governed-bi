# Usage (Quickstart)

This covers what you can actually run today. The project is design-first, but the
corpus layer, the SQLite connector and gateway, and catalog-driven Facts profiling
all work now. The rest (the curator's proposer/adversary loop, the server, graph,
retrieval, memory, eval, viz) are documented stubs that raise
`NotImplementedError` until they are built. For what those will do, see the
[design docs](README.md); this page stays on the runnable surface.

| Area | Status | Where |
|---|---|---|
| Corpus schemas, IDs, validator, loader, CLI | runnable | `src/governed_bi/corpus/` |
| Example corpus (`beer_factory`, real BIRD DB) | runnable | `corpus/beer_factory/` |
| SQLite connector + gateway (read-only, audit) | runnable | `src/governed_bi/gateway/` |
| Facts profiling + physical-existence check | runnable | `src/governed_bi/curator/profile.py` |
| Dev workflow (install, validate, test) | runnable | this page |
| Postgres / Redshift connectors | seam (optional extras) | `src/governed_bi/gateway/connectors/` |
| curator proposer/adversary, server, graph, retrieval, memory, eval, viz | design only | `docs/`, stubs |

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Python 3.13 (uv will fetch it if needed; the version is pinned in `.python-version`)

## Install

```bash
uv sync
```

This creates `.venv`, installs the dependencies, and installs `governed_bi` in
editable mode. Check it worked:

```bash
uv run python -c "import governed_bi; print(governed_bi.__version__)"
```

## Validate a corpus

The corpus CLI checks ID conventions and reference integrity. A green run is the
"done-enough" signal for a corpus (D9).

```bash
# validate the bundled example (defaults to the corpus/ directory)
uv run python -m governed_bi.corpus.cli corpus/beer_factory

# validate everything under corpus/
uv run python -m governed_bi.corpus.cli

# see all options
uv run python -m governed_bi.corpus.cli --help
```

Output on success:

```
CI green: 16 assets, 1 skills, 0 findings.
```

On failure it lists each finding (for example `dangling-ref [metric_revenue]:
metric.base_table -> 'tbl_missing' does not resolve`) and exits non-zero.

Exit codes: `0` green, `1` findings, `2` bad usage or path not found. That makes
it usable as a CI gate. The physical-existence check (columns exist in the live
DB) and the few-shot leakage guard are not run here; they need a database
connection or the eval split, so they belong to the eval harness.

## Use the corpus from Python

The same loader, schema, and validator are a small public API. Everything is
parsed into typed Pydantic models, so a malformed asset fails loudly.

```python
from pathlib import Path
from governed_bi.corpus import load_corpus, validate_corpus, is_green, parse_asset

# Load a DB's corpus (YAML assets + Markdown skills) into typed models.
corpus = load_corpus(Path("corpus"), db="beer_factory")
print(len(corpus.assets), "assets;", len(corpus.skills), "skills")

# Run the same checks the CLI runs.
findings = validate_corpus(corpus.assets)
assert is_green(findings), findings

# The server-visible view: Audit tier stripped, governance.excluded removed.
server_view = corpus.for_server()

# Parse a single asset from a dict (raises pydantic.ValidationError if invalid).
table = parse_asset({
    "asset_type": "table",
    "id": "tbl_demo_orders",
    "db": "demo",
    "physical_name": "t_1",
})
print(table.id, table.asset_type)
```

`Corpus.for_server()` is the consumption contract in code: it is what the server
is allowed to see (Facts + Inference, never Audit, and never an excluded asset).

## Connect to a database

The gateway wraps a per-dialect connector. SQLite is implemented (read-only, with
an audit log and a forced row cap); Postgres and Redshift are seams behind the
`postgres` / `redshift` optional extras. Point it at a SQLite file and you can
introspect the catalog, profile the Facts tier, and run guarded queries:

```python
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.curator.profile import profile_database

conn = SqliteConnector("data/bird/mydb.sqlite")     # opens read-only
tables = profile_database(conn, db="mydb")           # Facts-tier table assets
gw = Gateway(conn)
result = gw.execute(
    "SELECT COUNT(*) FROM some_table",
    Identity(user="dev", all_access=True),
)
print(result.rows, gw.audit_log)
```

See [`data/README.md`](../data/README.md) for how to vendor a small BIRD SQLite
file. Once you have one, `validate_corpus(assets, connector=conn)` also runs the
physical-existence check (every `physical_name` exists in the live catalog).

## Audit cockpit (viz)

A read-only Streamlit cockpit renders the full corpus (Facts + Inference + Audit
+ excluded assets): corpus health, the table/tier view, the asset listing,
skills, and an "ask" panel that runs the server flow and shows the reliability
stamp. Streamlit is the optional `viz` extra:

```bash
uv run --extra viz streamlit run src/governed_bi/viz/app.py
```

Set `GOVERNED_BI_CORPUS`, `GOVERNED_BI_DB`, and `GOVERNED_BI_SQLITE` to point it
at a different corpus / database. The display logic lives in the UI-agnostic
`governed_bi.viz.presenter` (no UI dependency), so a different frontend swaps in
`app.py` alone.

## Run the tests

```bash
uv run pytest -q
```

## Next

- To write or edit corpus assets, see [Corpus authoring](corpus-authoring.md).
- For the field-by-field asset spec, see [Asset schemas](asset-schemas.md).
- For the design behind all of this, start at [docs/README.md](README.md).
