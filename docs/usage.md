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

**Nothing in that table is a credential a caller presents.** `OPENAI_API_KEY`, the AWS names and
the proxy's three buy *this process* access to a model provider; none of them is asked of anything
that calls this server. `GOVERNED_BI_API_KEY` was a row here until 2026-08-13 and is now read by
nothing — delete it from an old `.env` rather than assuming it still gates something. What its
absence costs is in [Serve](#serve-langgraph-server).

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

**No route asks for a credential.** Reaching the port is sufficient — there is nothing to
configure and nothing for a client to send:

```bash
curl localhost:2024/capabilities
```

**That re-opens two audit findings, and the honest thing is to say which.**
[A1 and A7](analysis/audit-2026-08-10.md) — "~82 routes with no authentication" and
"`/audit/turns` and `/audit/turns/{id}/trace` return every thread's SQL, full records, and an
absolute log path, unauthenticated" — were closed on 2026-08-12 by requiring a shared key. The key
was removed on 2026-08-13 and both findings are live again, in exactly the terms they were written
in: anything that can reach this port can post a turn, execute governed SQL against the configured
database, and read every past turn out of `/audit/turns`.

**Why, deliberately.** This is a single-operator engine on `127.0.0.1` under `langgraph dev`, and
LangGraph Studio cannot hold a credential on the calls it bootstraps with. Measured 2026-08-13:
`/info`, `/assistants/search` and `/assistants/{id}` arrive carrying no custom header at all — the
server answered *no credential presented* while the key in Studio's own connection dialog was
demonstrably correct — so a required key made Studio unusable rather than merely inconvenient. The
maintainer chose reachability over transport auth. The consequence is that **the port is the
boundary**: keep it on loopback, and put a proxy that does authenticate in front of it before it
is anything else.

`api/auth.py` still exists and is still wired by `langgraph.json`'s `auth.path`, because it does a
second job that has nothing to do with who is calling: `@auth.on.threads.update` and
`@auth.on.threads.create_run` refuse a client-supplied state-writing `command`, which is what
keeps audit findings A2, A3 and A4 closed. Thread state carries `licensed` — the bound the layer
stack enforces against — and `corpus_content_hash`, the treatment identity every quotability gate
reads. `@auth.authenticate` remains and now allows unconditionally; it returns the single
principal `authenticated_principal()` names, which is what the access seam
([ADR 0012](adr/0012-access-seam-principal-and-authorization.md)) is asked about. One principal
was already the model here — it is simply no longer proven by anything.

Route shapes are in [`openapi.json`](openapi.json).

Streaming (preferred): `POST /threads/{id}/runs/stream` with
`stream_mode: ["values", "messages", "custom"]` and `stream_subgraphs: true`
([ADR 0010](adr/0010-live-stage-events.md)). Blocking: `POST /chat`.

Serve expects Postgres.

### CORS, and why LangGraph Studio is on the list

`langgraph.json`'s `http.cors.allow_origins` names three origins: the two the `ui/`
frontend runs on, and `https://smith.langchain.com`, which is where LangGraph Studio
is served from (`langgraph_api/cli.py` opens
`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`). Declaring a
`cors` block **replaces** the CLI's default, which would have added the Studio origin
itself — so without that third entry Studio's preflight answers `400 Disallowed CORS
origin`, the browser blocks the call, and Studio reports only "Connection failed".
The origin list is read at startup: restart the server after changing it.

Studio needs nothing in its Custom Headers field: no route asks for a credential, so leave it
empty. `"*"` is still not an alternative for the *origin* list — `allow_credentials: true` makes a
wildcard origin invalid — and the list now carries more weight than it used to. **With the key
gone it is the only thing between a page you happen to be visiting and an engine you have
running**, and it is a weak thing: it stops a fourth origin, not the three it names, and not
anything that is not a browser.

> **A refusal a browser cannot read.** Until 2026-08-13 the custom routes refused an unkeyed
> caller with a 401 from a middleware that runs *outside* `CORSMiddleware` —
> `langgraph_api/server.py` orders `app.user_middleware` as
> `custom_middleware + global_middleware` — so the refusal bypassed CORS, carried no
> `Access-Control-Allow-Origin`, and reached the browser as `blocked by CORS policy` with the
> status dropped. Every cross-origin authentication failure therefore presented as a *connection*
> failure naming an origin that was in fact allowed; it cost half a day. The workaround
> (`_cors_headers`, heading those refusals by hand) went with the refusals themselves when the key
> was removed; with no early refusal left to head, every response from the custom app now passes
> back through `CORSMiddleware` normally. The lesson outlives both: **anything that short-circuits
> a response before `CORSMiddleware` is invisible to a browser**, which is worth remembering the
> next time something here refuses early. The 403s `api/auth.py` still raises are raised by the
> platform's own auth layer on its own routes, not by this app, and have not been read from a
> browser.

**Methods and headers are `"*"` on purpose; the origin list is the only narrowing here.** They
were an allowlist once (`GET, POST, PATCH, DELETE, OPTIONS` and four header names) and
that is what kept Studio out after the origin was added: Starlette answers a preflight
`400 Disallowed CORS headers` if the client requests one header the list omits, and the
browser then reports only "Connection failed". Narrowing them bought nothing anyway —
an origin on the list can call everything regardless. This
mirrors what `langgraph_api/server.py` passes when no `cors` block is declared.

`expose_headers` restores the three the server's own default exposes. Without them a
paginated list reads no `x-pagination-total`, which fails *after* connecting rather
than at it.

**Private-network preflights cannot be allowed from here, and it is not for want of
trying.** A preflight carrying `Access-Control-Request-Private-Network: true` — which is
what a browser sends when a public page reaches a loopback address — is answered
`400 Disallowed CORS private-network`. Starlette's `CORSMiddleware` fails it unless
constructed with `allow_private_network=True`
(`starlette/middleware/cors.py`), and nothing here can pass that: `server.py`
splats `CORSMiddleware(**config.CORS_CONFIG)`, but `CORS_CONFIG` has already been
through `TypeAdapter(HttpConfig)`, and `CorsConfig`
(`langgraph_api/config/schemas.py`) declares no such field, so pydantic drops the key
silently. Adding it to `langgraph.json` changes nothing — measured, not assumed.

`langgraph dev` sets `ALLOW_PRIVATE_NETWORK=true` and mounts `PrivateNetworkMiddleware`,
but that only stamps `Access-Control-Allow-Private-Network` onto a response that is
already a 400. The *default* CORS branch has the same gap, so this is not something a
`cors` block regressed.

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

That is what CI runs: 1,483 tests (1,466 plus 17 `xfail`) in around two minutes, peaking around
1.4 GB of working set — counted 2026-08-13, after the transport-auth test file was deleted. `-rs` prints
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

That serves http://localhost:3000. Two variables in `ui/.env.local` point it at
the engine, and both have to be right. Leave `NEXT_PUBLIC_LANGGRAPH_URL`
unset and `ui/lib/env.ts` puts the whole app on the mock fixtures in
`ui/lib/mock/fixtures.ts`, which renders a complete, plausible UI against no
engine at all:

| | |
|---|---|
| `NEXT_PUBLIC_LANGGRAPH_URL` | `http://localhost:2024`, the server from [Serve](#serve-langgraph-server) above |
| `NEXT_PUBLIC_ASSISTANT_ID` | `serve`, the graph id in `langgraph.json` |

There was a third, `NEXT_PUBLIC_GOVERNED_BI_API_KEY`, which had to equal
`GOVERNED_BI_API_KEY` in the engine's `.env` — two copies of one value, because the engine reads
`.env` at the repo root and Next.js only exposes a variable to browser code if it is prefixed
`NEXT_PUBLIC_` and present at build time. Being in one repository does not merge them, and when
they disagreed every request answered 401 and the UI rendered empty panels. Both ends went away
on 2026-08-13; a key left in an old `.env.local` is inert.

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
