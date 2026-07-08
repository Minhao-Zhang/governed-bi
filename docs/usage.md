# Usage (Quickstart)

This covers what you can actually run today. The project is design-first: the
**corpus layer** is implemented and usable; the harnesses (curator, server,
gateway, graph, retrieval, memory, eval, viz) are documented stubs that raise
`NotImplementedError` until they are built. For what those will do, see the
[design docs](README.md); this page stays strictly to the runnable surface.

| Area | Status | Where |
|---|---|---|
| Corpus schemas, IDs, validator, loader, CLI | **runnable** | `src/governed_bi/corpus/` |
| Example corpus (`california_schools`) | **runnable** | `corpus/california_schools/` |
| Dev workflow (install, validate, test) | **runnable** | this page |
| curator / server / gateway / graph / retrieval / memory / eval / viz | design only | `docs/`, stubs in `src/governed_bi/` |

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
uv run python -m governed_bi.corpus.cli corpus/california_schools

# validate everything under corpus/
uv run python -m governed_bi.corpus.cli

# see all options
uv run python -m governed_bi.corpus.cli --help
```

Output on success:

```
CI green: 9 assets, 1 skills, 0 findings.
```

On failure it lists each finding (for example `dangling-ref [metric_frpm_rate]:
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
corpus = load_corpus(Path("corpus"), db="california_schools")
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

## Run the tests

```bash
uv run pytest -q
```

## Next

- To write or edit corpus assets, see [Corpus authoring](corpus-authoring.md).
- For the field-by-field asset spec, see [Asset schemas](asset-schemas.md).
- For the design behind all of this, start at [docs/README.md](README.md).
