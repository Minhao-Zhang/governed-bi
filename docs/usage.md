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
| `GOVERNED_BI_MODEL` | Main chat model, read by the server and the one-turn CLI both. Unset → `has_live_model: false` on the server, and exit 2 from the CLI unless `--model` or `--no-model` is passed |
| `GOVERNED_BI_UTILITY_MODEL` | Small model for facet rewrite / scope gate |
| `GOVERNED_BI_UTILITY_MODEL_EFFORT` | Effort knob for the utility model |
| `GOVERNED_BI_EMBEDDING_MODEL` | Embedder id |
| `GOVERNED_BI_LLM_MAX_RETRIES` | Retries |
| `GOVERNED_BI_LLM_TIMEOUT_S` | Main-model timeout |
| `GOVERNED_BI_UTILITY_TIMEOUT_S` | Utility-model timeout |
| `GOVERNED_BI_MODEL_EFFORT` | Main-model effort |
| `GOVERNED_BI_SEED_DIR` | Where a seeded corpus is written when no curated one is given |
| `GOVERNED_BI_CONVERSATION_DB` | Served conversations' checkpoint database; defaults to `runs/conversations.sqlite`. A **file path**: `serve/checkpointer.py::assert_not_a_warehouse` refuses a value carrying `host=`, `dbname=`, `password=` or a `postgres://`-style URL at configuration time, because a checkpointer pointed at the analytics warehouse writes conversation state into it on the first turn |
| `GOVERNED_BI_HARNESS_DB` | The one-turn CLI's and the eval driver's checkpoint database; defaults to `runs/harness-checkpoints.sqlite`. Separate from the above so 131 benchmark questions do not become the conversation history. Same refusal applies |
| `GOVERNED_BI_VECTOR_CACHE` | Persistent vector cache directory — one LanceDB database per model, one table per vector width. Defaults to `runs/vectors/`. The server, the one-turn CLI and the eval driver share it |
| `GOVERNED_BI_AGENT_NODE_TIMEOUT_S` | Wall clock for the whole `agent_core` loop, overriding the `agent_node_timeout_s` knob (default 1200.0). `0` means no wall; empty means unset |
| `GOVERNED_BI_AGENT_RECURSION_LIMIT` | Superstep ceiling for the nested `create_agent` graph, overriding the `agent_recursion_limit` knob (default 40) |
| `GOVERNED_BI_RAIL_NODE_TIMEOUT_S` | Wall clock for one cancellable utility rail — today only `guard` — overriding the `rail_node_timeout_s` knob (default 120.0) |

**Nothing in that table is a credential a caller presents.** `OPENAI_API_KEY`, the AWS names and
the proxy's three buy *this process* access to a model provider; none of them is asked of anything
that calls this server. `GOVERNED_BI_API_KEY` was a row here until 2026-08-13 and is now read by
nothing — delete it from an old `.env` rather than assuming it still gates something. What its
absence costs is in [Serve](#serve-langgraph-server).

The provider, the per-surface model ids, the AWS region and the credential names are read in
exactly one module, [`model/provider.py`](../src/governed_bi/model/provider.py) (`PROVIDER_VAR`,
`SURFACE_PROVIDER_VARS`, `SURFACE_MODEL_VARS`, `AWS_REGION_VARS`, `_CREDENTIAL_NAMES`). The proxy's own
three are read in
[`model/proxy_gateway.py`](../src/governed_bi/model/proxy_gateway.py)
(`PROXY_SECRET_NAME_VAR`, `PROXY_REGION_VAR`, `PROXY_CA_BUNDLE_VAR`). Check them
there rather than against this table.

## A stack that will actually answer

Every variable above can be set correctly and the engine still answer nothing, because three of the
things it needs are not in the repository. This section is the shortest path to a running stack that
answers a real question, written down on 2026-08-19 after it took longer than the work it was for.

**`.env` is one developer's file, not a checked-in configuration.** It is gitignored, so the DSN in
it names a database on whoever's machine wrote it. On a machine where port 5432 already belongs to
another project, that DSN reaches the wrong server and Postgres answers
`FATAL: password authentication failed`, which reads like a wrong password and is a wrong *server*.
Check what holds the port before believing the message:

```bash
docker ps --format '{{.Names}}\t{{.Ports}}'
```

A Postgres of this repository's own, on a port nothing else wants:

```bash
docker run -d --name governed-bi-pg -e POSTGRES_USER=gbi -e POSTGRES_PASSWORD=gbi -e POSTGRES_DB=gbi -p 5433:5432 postgres:18
```

**A corpus is not data.** `corpora/` is gitignored and starts empty, and a curated corpus is
semantic-layer YAML: table, column, join and term assets, with no rows behind them. Point
`GOVERNED_BI_CORPUS_DIR` at one and every question refuses, correctly, because the tables it
describes are not in the warehouse. The two have to match, and the corpus does not carry its half.

`tools/load_demo_schema.py` exists for this. It builds seven tables with foreign keys, a self-join
and enough rows to aggregate, which is the smallest thing you can ask a real question of:

```bash
uv run python tools/load_demo_schema.py
```

Then leave `GOVERNED_BI_CORPUS_DIR` unset and set `GOVERNED_BI_SCHEMA=gbi_demo_sales`. The engine
seeds a corpus from the live schema at startup, so the two halves cannot disagree.

**`uv sync` alone does not cover a Bedrock `.env`.** `GOVERNED_BI_PROVIDER=bedrock` needs the extra,
and the failure is a `ModuleNotFoundError` for `langchain_aws` at the first model call rather than at
startup:

```bash
uv sync --extra bedrock
```

Confirm the whole thing from one request, before opening a browser. `/capabilities` reports what the
process resolved, so it names the model, the gateway and the database it is actually pointed at:

```bash
curl -s http://127.0.0.1:2024/capabilities
```

`has_live_model: false` there means `GOVERNED_BI_MODEL` is unset. A `connection.port` you did not
expect means `.env` won the argument.

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

**That re-opens two findings, and the honest thing is to say which.**
A1 and A7 — every route unauthenticated, and `/audit/turns` plus
`/audit/turns/{id}/trace` return every thread's SQL, full records, and an
absolute path to the conversation database — were closed on 2026-08-12 by requiring a shared key. The key
was removed on 2026-08-13 and both findings are live again, in exactly the terms they were written
in: anything that can reach this port can post a turn, execute governed SQL against the configured
database, and read every past turn out of `/audit/turns` — or out of the platform's own
`/threads/{id}/state`, which since 2026-08-18 returns every turn of that thread rather than the
newest one.

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

Serving a turn is `POST /threads/{id}/runs/stream` with
`stream_mode: ["values", "messages", "custom"]` and `stream_subgraphs: true`
([ADR 0010](adr/0010-live-stage-events.md)), and **there is no second way**: `POST /chat` and
`POST /chat/resume` were deleted on 2026-08-18 ([ADR 0014](adr/0014-one-conversation-store.md)).
They kept their own `InMemorySaver`, so degrading to them silently lost the conversation they
were meant to rescue. Every custom route this app mounts is now a read, including
`GET /clarifications/pending` — unanswered `ask_user` prompts, oldest first, out of interrupt
state.

A clarification is answered by posting a run with `{"command": {"resume": …}}` on the same thread.
`serve/resume.py::authorise_resume` refuses one whose caller is not the caller that was asked
(ADR 0006 §10 B9); with one principal and no transport credential that gate cannot tell two
callers apart, which is the state [`enterprise-fork.md`](enterprise-fork.md) says to fix first.

Conversations are durable as of 2026-08-18: `langgraph.json` mounts an `AsyncSqliteSaver` over
`runs/conversations.sqlite`, the server logs *"Using custom checkpointer: AsyncSqliteSaver"* at
startup, and a thread survives a restart. Two limits come with it. `keep_latest` retention is not
available — `AsyncSqliteSaver` does not implement `aprune`, and the server names the missing
methods at startup — so `checkpointer.ttl` **deletes** a thread 90 days after its last update,
with no "keep the summary, expire the trace" middle state.

**Inert under `langgraph dev`:** the in-memory runtime's `sweep_ttl` returns `(0, 0)`
("Not implemented for inmem server") and nothing calls it, so nothing is deleted locally and a
long-lived thread grows without bound. The setting takes effect on a deployed runtime only.

And `checkpointer.path` replaces the *checkpoint* store only: under `langgraph dev` the thread, run and assistant index still lives in
`.langgraph_api/.langgraph_ops.pckl`, which has no config knob. `/capabilities` reports
`checkpoint_durable` from that same `langgraph.json` reading; both durability flags are
true on this deployment. They are a configuration reading, not a live handle
([`open-work.md`](open-work.md) §4.4).

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
uv run python -m governed_bi.serve --schema <schema> -q "…" --thread-id t-1
```

### `--thread-id`, and what happens without it

The CLI checkpoints to a durable SQLite file, so a turn that pauses on a clarification survives
the process that raised it. `--thread-id` is what makes that reachable: it names the thread, and
the checkpoint is kept for a later invocation to resume.

Omit it and the thread id is the run id — a fresh random value per process, which no later
command could ask for. Those checkpoints are **deleted** after the turn prints, because a
checkpoint nobody can name is not durability, it is a leak. Measured 2026-08-20:
`runs/harness-checkpoints.sqlite` had grown to 4.6 MB holding two such orphans, roughly 1.8 MB
per question asked. Nothing about the answer, the record or the exit code changes.

The model comes from `GOVERNED_BI_MODEL`, the same variable the server reads, and `--model`
overrides it for one run. There is no default: with neither set the command exits 2 naming the
variable, rather than picking a model for you.

That default used to be `gpt-4o-mini`, and it overrode the variable. Under
`GOVERNED_BI_PROVIDER=bedrock` it sent an OpenAI id to Bedrock and the turn came back
`outcome: crashed`, naming nothing that pointed at the model, while the same question answered
normally through the server. Fixed 2026-08-19.

## Tests

```bash
uv run --frozen pytest -q -rs
```

That is what CI runs. `-rs` prints
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
