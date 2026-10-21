# Usage

Install, configure, and run the current tree. Design background:
[architecture](architecture.md), [ADRs](adr/).

## Prerequisites

- [uv](https://docs.astral.sh/uv/)
- Python 3.13 (pinned in `.python-version`)

```bash
uv sync
```

CI runs `uv sync --frozen`. The app and the eval use OpenAI.

## Configuration

Settings are named defaults in [`registry/settings.py`](../src/governed_bi/registry/settings.py).
A `GOVERNED_BI_*` variable in the process environment overrides one for a run and enters the
config hash. Settings are not read from `.env`. A `.env` that sets anything other than a secret
stops startup and names the key.

Secrets — `OPENAI_API_KEY`, `GOVERNED_BI_PG_DSN`, and `LANGSMITH_*` —
are read from the environment and then `.env`. They never enter the config hash or the turn
record. LangSmith reads its own variables. Copy [`.env.example`](../.env.example) and keep only
those names.

The code defaults, matching the owner's environment on 2026-10-20:

| Setting | Default |
|---|---|
| `GOVERNED_BI_CORPUS_DIR` | `../BIRD-corpus`, resolved against the repo root |
| `GOVERNED_BI_MODEL` | `gpt-6-luna` |
| `GOVERNED_BI_MODEL_EFFORT` | `xhigh` |
| `GOVERNED_BI_UTILITY_MODEL` | `gpt-6-luna` |
| `GOVERNED_BI_UTILITY_MODEL_EFFORT` | `medium` |
| `GOVERNED_BI_EMBEDDING_MODEL` | `text-embedding-3-large` |
| `GOVERNED_BI_LLM_MAX_RETRIES` | `3` |
| `GOVERNED_BI_LLM_TIMEOUT_S` | `300` |
| `GOVERNED_BI_UTILITY_TIMEOUT_S` | `30` |

`src/` ships two TOMLs of its own (`govern/adversarial.toml`, `registry/configs.toml`), but those are
declared claims the test suite holds the code to, not configuration you edit.

### Environment

| Variable | Role |
|---|---|
| `GOVERNED_BI_PG_DSN` | Postgres DSN — required for LangGraph serve and for the eval driver. The one name is `credentials.PG_DSN_NAMES` in `src/governed_bi/` |
| `OPENAI_API_KEY` | Model access. The app and the eval use OpenAI |
| `GOVERNED_BI_CORPUS_DIR` | Corpus directory. Default `../BIRD-corpus`, resolved against the repo root |
| `GOVERNED_BI_SCHEMA` | Optional: seed / pin schema from the live database |
| `GOVERNED_BI_MODEL` | Main chat model. Default `gpt-6-luna`. The CLI still accepts `--model` and `--no-model` |
| `GOVERNED_BI_UTILITY_MODEL` | Model for retrieval channel rewrite and the scope check. Default `gpt-6-luna` |
| `GOVERNED_BI_UTILITY_MODEL_EFFORT` | Effort setting for the utility model |
| `GOVERNED_BI_EMBEDDING_MODEL` | Embedder id |
| `GOVERNED_BI_LLM_MAX_RETRIES` | Retries |
| `GOVERNED_BI_LLM_TIMEOUT_S` | Main-model timeout |
| `GOVERNED_BI_UTILITY_TIMEOUT_S` | Utility-model timeout |
| `GOVERNED_BI_MODEL_EFFORT` | Main-model effort |
| `GOVERNED_BI_SEED_DIR` | Where a seeded corpus is written when no curated one is given |
| `GOVERNED_BI_CONVERSATION_DB` | Served conversations' checkpoint database; defaults to `runs/conversations.sqlite`. A **file path**: `serve/checkpointer.py::assert_not_a_warehouse` refuses a value carrying `host=`, `dbname=`, `password=` or a `postgres://`-style URL at configuration time, because a checkpointer pointed at the analytics warehouse writes conversation state into it on the first turn |
| `GOVERNED_BI_HARNESS_DB` | The one-turn CLI's and the eval driver's checkpoint database; defaults to `runs/harness-checkpoints.sqlite`. Separate from the above so 131 benchmark questions do not become the conversation history. Same refusal applies |
| `GOVERNED_BI_VECTOR_CACHE` | Persistent vector cache directory — one LanceDB database per model, one table per vector width. Defaults to `runs/vectors/`. The server, the one-turn CLI and the eval driver share it |
| `GOVERNED_BI_CORPUS_RELEASE` | The corpus **tag** a configuration is measured against — a name a human pinned, not a directory and not the content hash, which moves for reasons that are not a setting under test. Overrides the `corpus_release` setting that decides whether two runs can be compared, so it participates in the config hash and `registry/config_profiles.py::recorded_corpus_release` refuses a profile claiming a different one. Set it when the setting under test *is* the corpus |
| `GOVERNED_BI_AGENT_NODE_TIMEOUT_S` | Wall clock for the whole `agent_core` loop, overriding the `agent_node_timeout_s` setting (default 1200.0). `0` means no wall; empty means unset |
| `GOVERNED_BI_AGENT_RECURSION_LIMIT` | Superstep ceiling for the nested `create_agent` graph, overriding the `agent_recursion_limit` setting (default 40) |
| `GOVERNED_BI_RAIL_NODE_TIMEOUT_S` | Wall clock for one cancellable utility rail — today only `guard` — overriding the `rail_node_timeout_s` setting (default 120.0) |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Tracing, off by default. **Read by LangChain itself — nothing in `src/` reads them and there is no callback to wire.** One trace per root invocation, so a whole agentic turn costs one and a 20-question debug run costs 20. When on, traces carry run inputs and outputs in full: tool messages, row previews, governed context. That is intended here and would need a masking layer at the tracer boundary in production; there is none |
| `OPENAI_BASE_URL` | **Do not set.** Read by the OpenAI SDK, so it redirects the chat client and the embedder together |

**Nothing in that table is a credential a caller presents.** `OPENAI_API_KEY` buys this process
access to the model; nothing is asked of a caller of this server. `GOVERNED_BI_API_KEY` was a row
here until 2026-08-13 and is now read by
nothing — delete it from an old `.env` rather than assuming it still checks something. What its
absence costs is in [Serve](#serve-langgraph-server).

Entry points call `boot.resolve_settings` once. The names and defaults live in
[`registry/settings.py`](../src/governed_bi/registry/settings.py). Secrets live in
[`credentials.py`](../src/governed_bi/credentials.py). Check those rather than this table.

### Configuration audit (M9, 2026-09)

Documentation only; M10 decides removals. Classes: **secret** (a credential or the name of one),
**location** (where something is), **model selection**, **operational** (a bound or switch an
operator tunes), **unmeasured** (a setting override no configuration has ever varied). "Read in" is relative to
`src/governed_bi/`.

| Variable | Read in | Class | Recommendation |
|---|---|---|---|
| `GOVERNED_BI_PG_DSN` | `credentials.py` | secret | keep |
| `GOVERNED_BI_API_KEY` | nothing; docstrings in `api/auth.py`, `api/routes.py` | none | remove the mentions |
| `GOVERNED_BI_CORPUS_DIR` | `api/graph_app.py` | location | keep |
| `GOVERNED_BI_SCHEMA` | `api/graph_app.py` | location | keep |
| `GOVERNED_BI_SEED_DIR` | `api/graph_app.py` | location | remove: the default under `runs/seeded-corpus/` has no known override |
| `GOVERNED_BI_CONVERSATION_DB` | `serve/checkpointer.py` | location | keep |
| `GOVERNED_BI_HARNESS_DB` | `serve/checkpointer.py` | location | keep |
| `GOVERNED_BI_VECTOR_CACHE` | `retrieve/vector_cache.py` | location | keep |
| `GOVERNED_BI_MODEL` | `api/graph_app.py`, `model/provider.py` | model selection | keep |
| `GOVERNED_BI_MODEL_EFFORT` | `api/graph_app.py` | model selection | keep |
| `GOVERNED_BI_UTILITY_MODEL` | `api/graph_app.py`, `model/provider.py` | model selection | keep |
| `GOVERNED_BI_UTILITY_MODEL_EFFORT` | `api/graph_app.py` | model selection | keep |
| `GOVERNED_BI_EMBEDDING_MODEL` | `model/provider.py` | model selection | keep |
| `GOVERNED_BI_LLM_MAX_RETRIES` | `api/graph_app.py` | operational | keep |
| `GOVERNED_BI_LLM_TIMEOUT_S` | `api/graph_app.py` | operational | keep |
| `GOVERNED_BI_UTILITY_TIMEOUT_S` | `api/graph_app.py` | operational | keep |
| `GOVERNED_BI_CORPUS_RELEASE` | `registry/settings.py` | operational | keep for configs; consider moving to `registry/configs.toml` |
| `GOVERNED_BI_AGENT_NODE_TIMEOUT_S` | `registry/settings.py`, `serve/nodes/agent_core.py` | unmeasured | keep as the one wall clock on a turn |
| `GOVERNED_BI_AGENT_RECURSION_LIMIT` | `registry/settings.py`, `serve/nodes/agent_core.py` | unmeasured | remove: the setting default is the only value ever run |
| `GOVERNED_BI_RAIL_NODE_TIMEOUT_S` | `registry/settings.py`, `serve/graph.py` | unmeasured | remove: the setting default is the only value ever run |

### Leak checks

| check | Protects | Runs |
|---|---|---|
| `check_no_benchmark_discriminators` | hand-written sibling discriminators staying out of the tree | per push |
| `check_train_only` | held-out question text, including a quoted question, staying out of a corpus | manual: needs a corpus, the question file, and a train-only control |

## A stack that will answer

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

Confirm the whole thing from one request, before opening a browser. `/capabilities` reports what the
process resolved, so it names the model, the gateway and the database it is pointed at:

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

**That re-opens two findings, and the accurate thing is to say which.**
A1 and A7 — every route unauthenticated, and every thread's SQL and full records readable — were
closed on 2026-08-12 by requiring a shared key. The key
was removed on 2026-08-13 and both findings are live again, in exactly the terms they were written
in: anything that can reach this port can post a turn, execute governed SQL against the configured
database, and read past turns out of the platform's own `/threads/{id}/state`, which returns the
thread's recent turns rather than the newest one.

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
keeps audit findings A2, A3 and A4 closed. Thread state carries `retrieved` — the bound the layer
stack enforces against — and `corpus_content_hash`, the identity of what was measured every quotability check
reads. `@auth.authenticate` remains and allows unconditionally, returning a constant identity that
nothing reads.

Route shapes are in [`openapi.json`](openapi.json).

Serving a turn is `POST /threads/{id}/runs/stream` with
`stream_mode: ["values", "messages", "custom"]` and `stream_subgraphs: true`
([ADR 0010](adr/0010-live-stage-events.md)), and **there is no second way**: `POST /chat` and
`POST /chat/resume` were deleted on 2026-08-18 ([ADR 0014](adr/0014-one-conversation-store.md)).
They kept their own `InMemorySaver`, so degrading to them silently lost the conversation they
were meant to rescue. Custom routes this app mounts are reads.
`GET /clarifications/pending` is unanswered `ask_user` prompts, oldest first, read from
interrupt state. Answering one is a resume of that thread, not a write on this route.

A clarification is answered by posting a run with `{"command": {"resume": …}}` on the same thread.
Resume maps: `{answer}` and `{choice_id}` proceed, `{declined: true}` fails closed,
`{deferred: true}` proceeds under the stated why. The shipped UI sends only those four.
`{cancelled: true}` is still accepted and folded into the same fail-closed branch as
`{declined: true}`, for older clients — nothing in this repo's client sends it.
The thread id is the only handle on a paused clarification.

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
`.langgraph_api/.langgraph_ops.pckl`, which has no config setting. `/capabilities` reports
`checkpoint_durable` from that same `langgraph.json` reading; both durability flags are
true on this deployment. They are a configuration reading, not a live handle
([`open-work.md`](open-work.md) §4.4).

Serve expects Postgres.

### CORS, and why LangGraph Studio is on the list

`langgraph.json`'s `http.cors.allow_origins` names five origins: the four the `ui/`
frontend runs on (`localhost` and `127.0.0.1`, on port 3000 and on port 3100), and
`https://smith.langchain.com`, which is where LangGraph Studio
is served from (`langgraph_api/cli.py` opens
`https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`). Declaring a
`cors` block **replaces** the CLI's default, which would have added the Studio origin
itself — so without that Studio entry its preflight answers `400 Disallowed CORS
origin`, the browser blocks the call, and Studio reports only "Connection failed".
The origin list is read at startup: restart the server after changing it.

The 3100 pair is necessary too, and not obviously so: it is the only reason
`.claude/launch.json`'s `ui-live-3100` can point a second `next dev` at an engine already
running on 2024 (`_why_ui_live_3100` says exactly that), so pruning it as dead config
breaks that entry with a CORS error rather than a missing-config one.

Studio needs nothing in its Custom Headers field: no route asks for a credential, so leave it
empty. `"*"` is still not an alternative for the *origin* list — `allow_credentials: true` makes a
wildcard origin invalid — and the list now carries more weight than it used to. **With the key
gone it is the only thing between a page you happen to be visiting and an engine you have
running**, and it is a weak thing: it stops a sixth origin, not the five it names, and not
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
but that only writes `Access-Control-Allow-Private-Network` onto a response that is
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

The model comes from the settings module (`chat_model`), the same default the server reads.
`GOVERNED_BI_MODEL` overrides it for the process, and `--model` overrides it for one run.
`--no-model` serves the stub path.

## Tests

```bash
uv run --frozen pytest -q -rs
```

That is what CI runs. `-rs` prints
every skip with its reason — the Postgres and OpenAI-backed contracts skip
without credentials, and a silent skip reads as a pass.

## Evaluation

To run a measured configuration over the BIRD data lake, see
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
renamed on the engine side passes lint and fails only in a browser.

`npm test` runs the component tests, and `npm run check:stream-messages` stays manual: it is a
red/green reproduction of one rendering bug.
