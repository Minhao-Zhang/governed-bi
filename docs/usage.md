# Usage (Quickstart)

_[English](usage.md) · [简体中文](usage.zh.md)_

The full ask -> answer pipeline runs end to end today over the committed
`beer_factory` database, and needs no model or network: it falls back to
deterministic offline defaults (a template SQL generator, a hashing embedder).
This page stays on the runnable surface; for the design behind it, see the
[design docs](README.md).

| Area | Status | Where |
|---|---|---|
| Corpus schemas, IDs, validator, loader, serializer, CLI | runnable | `src/governed_bi/corpus/` |
| Example corpus (`beer_factory`, real BIRD DB) | runnable | `corpus/beer_factory/` |
| SQLite connector + gateway (read-only, audit) + five-layer guardrails | runnable | `src/governed_bi/gateway/` |
| Curator: Facts profiling, heuristic + LLM proposer, adversary, curate loop | runnable | `src/governed_bi/curator/` |
| Graph projection + Steiner join planning | runnable | `src/governed_bi/graph/` |
| Retrieval (BM25 + grounding, + embedder-gated vector channel) | runnable | `src/governed_bi/retrieval/` |
| Serve flow (route, context, SQL gen, guardrails, self-repair, cache, stamp) | runnable | `src/governed_bi/server/` |
| Memory (working) + eval (EX, arms, refuse-gate) + viz cockpit | runnable | `src/governed_bi/{memory,eval,viz}/` |
| Model clients (raw OpenAI / LangChain) | runnable behind `openai` / `agents` extras | `src/governed_bi/llm/` |
| Agent harnesses (LangGraph serve DAG, deepagents curator) | runnable behind `agents` extra | `server/graph.py`, `curator/deep_agent.py` |
| Postgres / Redshift connectors | seam (optional extras) | `src/governed_bi/gateway/connectors/` |

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

## Ask a question (serve pipeline)

The serve flow routes a question, retrieves + assembles context, generates SQL,
runs the five guardrail layers, executes as-user, and stamps the answer. With no
model it uses the deterministic template generator (metric / KPI questions):

```python
from pathlib import Path
from governed_bi.config import Settings, Environment
from governed_bi.corpus import load_corpus
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.server import answer_question

corpus = load_corpus(Path("corpus"), db="beer_factory").for_server()
conn = SqliteConnector("data/bird/beer_factory.sqlite")
ans = answer_question(
    "What is the total revenue?",
    Identity(user="dev", all_access=True),
    corpus=corpus,
    gateway=Gateway(conn),
    settings=Settings.for_env(Environment.dev),
    session_id="s",
)
print(ans.tier, ans.sql, ans.text)  # -> ReliabilityTier.governed  SELECT ...  total_revenue = ...
```

To use a real OpenAI model (LLM generator, embeddings, SQL cache) or the LangGraph
serve harness (`answer_question_graph`) and the deepagents curator, install the
`agents` extra and inject the clients - see the **Models & configuration** section
of the [README](../README.md). The API key is read from `OPENAI_API_KEY`, never
stored.

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
