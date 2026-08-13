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

There is no config file. Runtime configuration is, in precedence order:

1. Secrets and the DSN in `.env` or the process environment.
2. `GOVERNED_BI_*` variables (see below).
3. Defaults in [`register/knobs.py`](../src/governed_bi/register/knobs.py).

Copy [`.env.example`](../.env.example) to `.env` and fill in what you need.

### Environment

| Variable | Role |
|---|---|
| `GOVERNED_BI_PG_DSN`, else `PG_RENAME_DECOY_DSN` | Postgres DSN — required for LangGraph serve and for the eval driver. Those two names in that precedence, from `credentials.PG_DSN_NAMES` in `src/governed_bi/` |
| `OPENAI_API_KEY` | Model access on the `openai` gateway — the default one |
| `GOVERNED_BI_API_KEY` | **Required to serve.** Transport auth for every route but `GET /livez` (`api/auth.py`, wired by `langgraph.json`'s `auth.path`; the custom routes enforce it in `api/routes.py`'s own middleware, which reuses that comparison). Unset means the server refuses every request and names this variable in the 401 — it does not mean "open". Clients send it as `x-api-key` or `Authorization: Bearer`; the UI in `ui/` reads it from `NEXT_PUBLIC_GOVERNED_BI_API_KEY` and both values must match. One key is one principal, so it is transport auth and not per-user authorization |
| `GOVERNED_BI_PROVIDER` | Gateway for every surface: `openai` (default), `bedrock`, `proxy` |
| `GOVERNED_BI_MODEL_PROVIDER`, `GOVERNED_BI_UTILITY_PROVIDER`, `GOVERNED_BI_EMBEDDING_PROVIDER` | Per-surface override of the above. The three surfaces resolve independently |
| `GOVERNED_BI_AWS_REGION`, else `AWS_REGION`, else `AWS_DEFAULT_REGION` | Bedrock region, in that precedence — the engine's own name wins over whatever the shell exports for other tooling |
| `AWS_ACCESS_KEY_ID` / `AWS_PROFILE` / `AWS_ROLE_ARN` / `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` | Bedrock credentials. None is required by name: `provider.credentials_present` asks botocore's resolver, so an instance or task role with nothing set here authenticates |
| `GOVERNED_BI_PROXY_SECRET` | Name of the Secrets Manager secret holding the `proxy` gateway's API key and base URL — the name, never the value. Unset means the proxy provider refuses |
| `GOVERNED_BI_PROXY_REGION` | AWS region for that secret lookup. Unset falls through to boto3's own resolution chain |
| `GOVERNED_BI_PROXY_CA_BUNDLE` | Path to a CA bundle for the proxy's TLS chain. Unset disables verification |
| `GOVERNED_BI_CORPUS_DIR` | Corpus directory (else one dir under `corpora/`, or seed via schema) |
| `GOVERNED_BI_ACCESS_POLICY` | Path to a `StaticRoleAccessPolicy` TOML file ([ADR 0012](adr/0012-access-seam-principal-and-authorization.md) §2), resolved against the repo root. Unset means `OpenAccessPolicy`, which authorizes every table and is what this repository ships. Set to a path that is not a file and the server **refuses to start** rather than serving open under a policy the operator believes is in force |
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

Set `GOVERNED_BI_API_KEY` first, or every call answers 401 — including the UI's,
which cannot render past `/capabilities`. Every request but `GET /livez` carries
it:

```bash
curl -H "x-api-key: $GOVERNED_BI_API_KEY" localhost:2024/capabilities
curl -H "Authorization: Bearer $GOVERNED_BI_API_KEY" localhost:2024/capabilities
```

Route shapes and the 401 body are in [`openapi.json`](openapi.json).

Streaming (preferred): `POST /threads/{id}/runs/stream` with
`stream_mode: ["values", "messages", "custom"]` and `stream_subgraphs: true`
([ADR 0010](adr/0010-live-stage-events.md)). Blocking: `POST /chat`.

Serve expects Postgres.

## One-turn CLI

```bash
uv run python -m governed_bi.serve --schema <schema> -q "…"
uv run python -m governed_bi.serve --corpus-dir <path> -q "…"
uv run python -m governed_bi.serve --schema <schema> -q "…" --no-model
```

## Tests

```bash
uv run --frozen pytest -q -rs
```

That is what CI runs: 1,448 tests in ~100 s, peaking around 1.4 GB of working set. `-rs` prints
every skip with its reason — the Postgres and OpenAI-backed contracts skip
without credentials, and a silent skip reads as a pass.

## Evaluation

To run a measured arm over the BIRD data lake, see
[measurement](measurement.md). It covers the driver's flags, the prompt
registry, the fields a measurement row carries, and what makes a number
quotable.

## UI

The frontend is `ui/` in this repository. It needs its own install once, and a
running engine to show anything but mock fixtures:

```bash
npm --prefix ui ci
```

```bash
npm --prefix ui run dev
```

`ci` and not `install`: `npm install` reads `package.json` from the working
directory, and `--prefix` only redirects where the tree is written — so
`npm --prefix ui install` looks for a root `package.json` that does not exist and
exits ENOENT. `ci` and `run` both resolve from the prefix. Inside `ui/`, plain
`npm install` works.

That serves http://localhost:3000. Three variables in `ui/.env.local` point it at
the engine, and all three have to be right. Leave `NEXT_PUBLIC_LANGGRAPH_URL`
unset and `ui/lib/env.ts` puts the whole app on the mock fixtures in
`ui/lib/mock/fixtures.ts`, which renders a complete, plausible UI against no
engine at all:

| | |
|---|---|
| `NEXT_PUBLIC_LANGGRAPH_URL` | `http://localhost:2024`, the server from [Serve](#serve-langgraph-server) above |
| `NEXT_PUBLIC_ASSISTANT_ID` | `serve`, the graph id in `langgraph.json` |
| `NEXT_PUBLIC_GOVERNED_BI_API_KEY` | must equal `GOVERNED_BI_API_KEY` in `.env` |

The key is duplicated rather than shared because the two processes read
configuration by different rules: the engine reads `.env` at the repo root, and
Next.js only exposes a variable to browser code if it is prefixed
`NEXT_PUBLIC_` and present at build time. Being in one repository does not merge
them. If they disagree, every request answers 401 and the UI renders empty
panels — it cannot get past `/capabilities`.

`.env.local` is gitignored, so a fresh clone starts from
[`ui/.env.example`](../ui/.env.example).

Frontend checks, run from `ui/`:

```bash
npm run lint
npx tsc --noEmit
npm run build
```

`npx tsc --noEmit` is not redundant with the linter, which does not type-check: a payload field
renamed on the engine side passes lint and fails only in a browser. Two more need a live engine and
a loaded corpus, so they stay manual: `npm run check:api` validates every route against the
client's zod schemas, and `npm run check:stream-messages` is a red/green reproduction of one
rendering bug.
