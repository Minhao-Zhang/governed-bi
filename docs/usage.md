# Usage

Install, configure, and run the current tree. Design background:
[architecture](architecture.md), [ADRs](adr/).

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Python 3.13 (pinned in `.python-version`)

```bash
uv sync                  # OpenAI arm
uv sync --extra bedrock  # + langchain-aws / boto3, for the Bedrock and proxy arms
```

One extra exists, `bedrock`. It brings the `langchain-aws` and `boto3` trees, and
three things need it:

- **The Bedrock chat and embedding path.** `model/provider.py` imports
  `botocore.config.Config` to carry timeout and retry onto the client, and
  `model/bedrock_embedder.py` raises naming this command when `langchain-aws` is
  absent.
- **The proxy gateway.** `model/proxy_gateway.py` reads its API key and base URL
  from AWS Secrets Manager, so `_require_boto3` raises naming this command when
  `boto3` is absent. The extra is where `boto3` comes from; the proxy arm does not
  run on a base install.
- **The provider-translation tests** in `tests/model/test_provider_selection.py`,
  which skip without it.

CI runs `uv sync --frozen --extra bedrock`.

## Configuration

There is **no** live `governed_bi.config.load_settings()` path. Runtime config is:

1. Secrets and DSN in `.env` / the process environment.
2. `GOVERNED_BI_*` variables (see below).
3. Defaults in [`register/knobs.py`](../src/governed_bi/register/knobs.py).

A `governed_bi.toml` may exist in the repo; it is not loaded by `src/`.

### Environment

| Variable | Role |
|---|---|
| `GOVERNED_BI_PG_DSN`, else `PG_RENAME_DECOY_DSN` | Postgres DSN — required for LangGraph serve and for the eval driver. Those two names in that precedence, from `tools/credentials.PG_DSN_NAMES` |
| `OPENAI_API_KEY` | Model access on the `openai` gateway — the default one |
| `GOVERNED_BI_PROVIDER` | Gateway for every surface: `openai` (default), `bedrock`, `proxy` |
| `GOVERNED_BI_MODEL_PROVIDER`, `GOVERNED_BI_UTILITY_PROVIDER`, `GOVERNED_BI_EMBEDDING_PROVIDER` | Per-surface override of the above. The three surfaces resolve independently |
| `GOVERNED_BI_AWS_REGION`, else `AWS_REGION`, else `AWS_DEFAULT_REGION` | Bedrock region, in that precedence — the engine's own name wins over whatever the shell exports for other tooling |
| `AWS_ACCESS_KEY_ID` / `AWS_PROFILE` / `AWS_ROLE_ARN` / `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` | Bedrock credentials. None is required by name: `provider.credentials_present` asks botocore's resolver, so an instance or task role with nothing set here authenticates |
| `GOVERNED_BI_PROXY_SECRET` | Name of the Secrets Manager secret holding the `proxy` gateway's API key and base URL — the name, never the value. Unset means the proxy provider refuses |
| `GOVERNED_BI_PROXY_REGION` | AWS region for that secret lookup. Unset falls through to boto3's own resolution chain |
| `GOVERNED_BI_PROXY_CA_BUNDLE` | Path to a CA bundle for the proxy's TLS chain. Unset disables verification |
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
| `GOVERNED_BI_SEED_DIR` | Where a seeded corpus is written when no curated one is given |
| `GOVERNED_BI_TURN_LOG_DIR` | Turn log root; defaults to `runs/serve/` |
| `GOVERNED_BI_VECTOR_CACHE` | Persistent vector cache directory — one LanceDB database per model, one table per vector width. Defaults to `runs/vectors/`. The server, the one-turn CLI and the eval driver share it |
| `GOVERNED_BI_AGENT_NODE_TIMEOUT_S` | Wall clock for the whole `agent_core` loop, overriding the `agent_node_timeout_s` knob (default 1200.0). `0` means no wall; empty means unset |
| `GOVERNED_BI_AGENT_RECURSION_LIMIT` | Superstep ceiling for the nested `create_agent` graph, overriding the `agent_recursion_limit` knob (default 40) |
| `GOVERNED_BI_RAIL_NODE_TIMEOUT_S` | Wall clock for one cancellable utility rail — today only `guard` — overriding the `rail_node_timeout_s` knob (default 120.0) |

The provider, AWS region and credential names are read in exactly one module —
[`model/provider.py`](../src/governed_bi/model/provider.py) (`PROVIDER_VAR`,
`SURFACE_PROVIDER_VARS`, `AWS_REGION_VARS`, `_CREDENTIAL_NAMES`). The proxy's own
three are read in
[`model/proxy_gateway.py`](../src/governed_bi/model/proxy_gateway.py)
(`PROXY_SECRET_NAME_VAR`, `PROXY_REGION_VAR`, `PROXY_CA_BUNDLE_VAR`). Check them
there rather than against this table.

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

## Evaluation

To run a measured arm over the BIRD data lake, see
[measurement](measurement.md). It covers the driver's flags, the prompt
registry, the fields a measurement row carries, and what makes a number
quotable.

## UI

[governed-bi-ui](https://github.com/Minhao-Zhang/governed-bi-ui) — local checkout
typically at `../governed-bi-ui`.
