# Usage (Quickstart)

_[English](usage.md) · [简体中文](usage.zh.md)_

> New here? The [Walkthrough](walkthrough.md) is a guided clone → first-question
> tour. This page is the reference quickstart.

The full question -> answer pipeline runs end to end today over the committed
`beer_factory` database. Corpus validation, the gateway, and retrieval need no
model or network (the embedder still falls back to a deterministic hashing
default); serve is **agent-only** (ADR 0002) and fails closed without a live
model. This page stays on the runnable surface; for the design behind it, see
the [design docs](README.md).

| Area | Status | Where |
|---|---|---|
| Corpus schemas, IDs, validator, loader, serializer, CLI | runnable | `src/governed_bi/corpus/` |
| Example corpus (`beer_factory`, real BIRD DB) | runnable | `corpus/beer_factory/` |
| SQLite connector + gateway (read-only, audit) + five-layer guardrails | runnable | `src/governed_bi/gateway/` |
| Curator: Facts profiling, heuristic + LLM proposer, adversary, curate loop | runnable | `src/governed_bi/curator/` |
| Graph projection + Steiner join planning | runnable | `src/governed_bi/graph/` |
| Retrieval (BM25 + grounding, + embedder-gated vector channel) | runnable | `src/governed_bi/retrieval/` |
| Serve (agentic core: route, context, governed tools, guardrails, self-repair, cache, stamp) | runnable, needs a live model | `src/governed_bi/analyst/` |
| Memory (working) + eval (EX, ladder, refuse-gate) + viz presenter (audit view models) | runnable | `src/governed_bi/{memory,eval,viz}/` |
| Model clients (raw OpenAI / LangChain) | runnable (installed by a plain `uv sync`, no extra) | `src/governed_bi/llm/` |
| Agent harnesses (LangGraph governed serve core, deepagents curator) | runnable (installed by a plain `uv sync`, no extra) | `analyst/agent.py`, `curator/deep_agent.py` |
| Postgres / Redshift connectors | implemented (psycopg-backed, plain `uv sync`); offline-tested, not run live | `src/governed_bi/gateway/connectors/` |

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
corpus = load_corpus(Path("corpus"), schema="beer_factory")
print(len(corpus.assets), "assets;", len(corpus.skills), "skills")

# Run the same checks the CLI runs.
findings = validate_corpus(corpus.assets)
assert is_green(findings), findings

# The Analyst-visible view: Audit tier stripped, governance.excluded removed.
analyst_view = corpus.for_analyst()

# Parse a single asset from a dict (raises pydantic.ValidationError if invalid).
table = parse_asset({
    "asset_type": "table",
    "id": "tbl_demo_orders",
    "schema": "demo",
    "physical_name": "t_1",
})
print(table.id, table.asset_type)
```

`Corpus.for_analyst()` is the consumption contract in code: it is what the Analyst
is allowed to see (Facts + Inference, never Audit, and never an excluded asset).

## Connect to a database

The gateway wraps a per-dialect connector. SQLite is proven (read-only, with an
audit log and a forced row cap); Postgres (`information_schema`) is exercised live
by the eval harness (`eval/run_experiment.py`, against a local BIRD-Obfuscation
Postgres) and unit-tested offline; Redshift (`svv_*`) reuses the Postgres path but
is not yet run against a live cluster (both ride psycopg, installed by a plain `uv
sync`). Point it at a SQLite
file and you can introspect the catalog, profile the Facts tier, and run guarded
queries:

```python
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.curator.profile import profile_database

conn = SqliteConnector("data/bird/mydb.sqlite")     # opens read-only
tables = profile_database(conn, schema="mydb")       # Facts-tier table assets
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

Serve is agent-only (ADR 0002): `create_agent` + `GovernanceMiddleware` +
governed read-only tools, wrapped by an outer LangGraph rails graph that
routes the question, checks the semantic cache, runs the agent core, and
stamps the answer. There is no deterministic fallback for answering: it needs
a live model, and fails closed rather than guessing without one:

```python
from pathlib import Path
from governed_bi.config import load_settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import SqliteConnector, Gateway, Identity
from governed_bi.llm import LangChainChatClient
from governed_bi.analyst.agent import answer_question_agent

settings = load_settings()
corpus = load_corpus(Path(settings.corpus_root), schema="beer_factory").for_analyst()
conn = SqliteConnector(settings.datasource.sqlite_path)
chat = LangChainChatClient.from_config(settings.models)  # needs OPENAI_API_KEY
ans = answer_question_agent(
    "What is the total revenue?",
    Identity(user="dev", all_access=True),
    corpus=corpus,
    gateway=Gateway(conn),
    settings=settings,
    session_id="s",
    model=chat.model,  # the raw LangChain model the agent core drives
)
print(ans.tier, ans.sql, ans.text)  # -> ReliabilityTier.governed  SELECT ...  total_revenue = ...
```

This needs a real key and the client injected as shown above — the agent
harnesses (curator and serve) are installed by a plain `uv sync`, no extra
required; see the **Models & configuration** section of the
[README](../README.md) for the full setup. The API key is read from the env
var named by `[models].api_key_env` (default `OPENAI_API_KEY`), never stored.
The deepagents curator needs the same key.

## Audit surface (viz presenter + API)

This repo ships **no bundled UI**. The read-only audit/review surface is two
UI-agnostic pieces: the `governed_bi.viz.presenter` view models (corpus health,
the table/tier view, the relationship/knowledge graph, the asset listing,
skills, and an answer's two-axis reliability stamp — no UI dependency), and the
`governed_bi.api` FastAPI HTTP/JSON API that serves those view models plus the
governed agent core at `POST /chat`. The view-model endpoints (`/health`,
`/schema`, `/graph`, `/corpus/assets`, `/skills`, …) need no model; `/chat`
does, and returns `503` without one. To run the API:

```bash
uv run uvicorn --factory governed_bi.api:create_app
```

Then open the interactive docs at http://localhost:8000/docs, or POST a
question (needs `OPENAI_API_KEY` set — the agent harness is installed by a
plain `uv sync`):

```bash
curl -s localhost:8000/chat -H 'content-type: application/json' \
  -d '{"question":"What is the total revenue?"}'
```

Policy comes from [`governed_bi.toml`](../governed_bi.toml) (corpus path,
datasource, serve flags). Local overrides go in git-ignored
`governed_bi.local.toml`. Because the display logic lives in the UI-agnostic
`governed_bi.viz.presenter` (no UI dependency), a separate frontend can consume
the same view models — the interactive UI is a separate project, see
[docs/ui-frontend-design.md](ui-frontend-design.md).

## Run the tests

```bash
uv run pytest -q
```

## Next

- To write or edit corpus assets, see [Corpus authoring](corpus-authoring.md).
- For the field-by-field asset spec, see [Asset schemas](asset-schemas.md).
- For the design behind all of this, start at [docs/README.md](README.md).
