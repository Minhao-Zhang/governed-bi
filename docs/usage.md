# Usage

Install, configure, and run the current tree. Design background:
[architecture](architecture.md), [ADRs](adr/).

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Python 3.13 (pinned in `.python-version`)

```bash
uv sync
uv sync --extra bedrock   # optional
```

## Configuration

There is **no** live `governed_bi.config.load_settings()` path. Runtime config is:

1. Secrets and DSN in `.env` / the process environment.
2. `GOVERNED_BI_*` variables (see below).
3. Defaults in [`register/knobs.py`](../src/governed_bi/register/knobs.py).

A `governed_bi.toml` may exist in the repo; it is not loaded by `src/`.

### Environment

| Variable | Role |
|---|---|
| `PG_DSN` (or other names accepted by `tools/credentials.py`) | Postgres DSN — required for LangGraph serve |
| `OPENAI_API_KEY` | Model access (or Bedrock credentials when using that extra) |
| `GOVERNED_BI_CORPUS_DIR` | Corpus directory (else one dir under `corpora/`, or seed via schema) |
| `GOVERNED_BI_SCHEMA` | Optional: seed / pin schema from the live database |
| `GOVERNED_BI_MODEL` | Main chat model; unset → `has_live_model: false` |
| `GOVERNED_BI_UTILITY_MODEL` | Small model for facet rewrite / scope gate |
| `GOVERNED_BI_UTILITY_MODEL_EFFORT` | Effort knob for the utility model |
| `GOVERNED_BI_EMBEDDING_MODEL` | Embedder id |
| `GOVERNED_BI_LLM_MAX_RETRIES` | Retries |
| `GOVERNED_BI_LLM_TIMEOUT_S` | Main-model timeout |
| `GOVERNED_BI_UTILITY_TIMEOUT_S` | Utility-model timeout |
| `GOVERNED_BI_MODEL_EFFORT` | Main-model effort |
| `GOVERNED_BI_SEED_DIR` | Seed directory |
| `GOVERNED_BI_TURN_LOG_DIR` | Optional turn log root |

## Serve (LangGraph Server)

```bash
uv run langgraph dev
```

`langgraph.json` maps graph `"serve"` to
`src/governed_bi/api/graph_app.py:make_graph` and HTTP to
`src/governed_bi/api/routes.py:app`.

Streaming (preferred): `POST /threads/{id}/runs/stream` with
`stream_mode: ["values", "messages", "custom"]` and `stream_subgraphs: true`
([ADR 0010](adr/0010-live-stage-events.md)). Blocking: `POST /chat`.

Serve expects Postgres. The SQLite file under `data/bird/` is for tests/CI, not
the default LangGraph serve datasource.

## One-turn CLI

```bash
uv run python -m governed_bi.serve --schema <schema> -q "…"
uv run python -m governed_bi.serve --corpus-dir <path> -q "…"
uv run python -m governed_bi.serve --schema <schema> -q "…" --no-model
```

## Tests

```bash
uv run pytest
```

## UI

[governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) — local checkout
typically at `../governed-bi-ui`.
