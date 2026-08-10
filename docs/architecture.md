# Architecture

How a question becomes a stamped answer in this tree. Binding detail lives in
[ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) (retrieval /
memory) and [ADR 0006](adr/0006-execution-time-governance.md) (governance).

## Serve spine

Wired in [`serve/graph.py`](../src/governed_bi/serve/graph.py). Nodes live under
[`serve/nodes/`](../src/governed_bi/serve/nodes/).

```
[accept] → guard → rewrite → negative_gate
  → fanout ─┬─ facet_schema
            ├─ facet_term
            ├─ facet_metric
            ├─ facet_entity
            └─ facet_example
  → route → resolve → connect → assemble
  → agent_core → reflect → narrate → stamp → [record] → END

guard blocked ─────────────────────→ refuse  ─┐
negative hit / route / connect ────→ decline ─┼─→ stamp
any node raising (wrap_node) ─────────────────┘
```

`accept` and `record` are bracketed because both are optional arguments to
`build_graph`: `accept` (the client-facing path) also switches the graph to the
`ServeInput` / `ServeOutput` schemas, and without `record` the graph runs
`stamp → END`. Every terminal path funnels through `stamp`, including crashes —
`wrap_node` turns a node exception into `failure` + `path_kind: crashed` and
routes there rather than letting it escape the graph.

| Stage | Role |
|---|---|
| `guard` | Five deterministic rules first, then a model-backed BI-scope gate on the utility model |
| `rewrite` | Stub rail today; facet query rewriting lives inside `facet_*` |
| `negative_gate` | Negative-example refuse path |
| `facet_*` | Parallel retrieval channels (each may rewrite its query) |
| `route` / `resolve` / `connect` | Schema pick, budgets, Steiner join |
| `assemble` | Render retrieval context block |
| `agent_core` | Nested `create_agent` loop (read-only tools) |
| `reflect` | Post-hoc observer; never routes the turn |
| `narrate` | Short answer over the result table (must not crash an answered turn) |
| `stamp` | The turn record: `outcome`, `guardrail_errors`, the ledger, `latency_sec` |

`agent_core` tools: `read_body`, `inspect_schema`, `sample_rows`, `run_query`,
`ask_user`.

**Where governance actually runs.** The two executing tools call it themselves —
there is no tool-call interceptor, and `wrap_tool_call` appears nowhere in `src/`:

```
serve/tools.py  →  serve/fetch.py  →  govern/pipeline.py::prepare
                →  govern/check.py::check()  →  read-only connector
```

The ledger row comes back on the tool's own `Command(update=...)`, beside the
payload. What keeps this from being merely a convention is that the model holds no
connector handle — the connector is closed over inside `build_tools` — and that
`check()` raises `GovernanceUsageError` rather than defaulting permissive when a
security argument is unwired. Adding a new executor that skips `check()` is caught
by `govern/`'s G2 invariant and its tests, not by the topology. Layers, rules and
executor paths: [ADR 0006](adr/0006-execution-time-governance.md).

`AgentMiddleware` *is* used in `agent_core`, for two things that are **not**
governance: injecting the retrieval context block on every model call (via
`wrap_model_call`, so it never enters `messages`), and ending the turn at the
`run_query` attempt cap.

Server entry: [`api/graph_app.py:make_graph`](../src/governed_bi/api/graph_app.py)
(`uv run langgraph dev`). HTTP app: [`api/routes.py:app`](../src/governed_bi/api/routes.py)
([ADR 0007](adr/0007-http-surface-and-the-ui-contract.md),
[ADR 0010](adr/0010-live-stage-events.md)).

## Packages (`src/governed_bi/`)

| Package | Responsibility |
|---|---|
| `api` | FastAPI + LangGraph Server wiring |
| `corpus` | Typed assets, load, validate |
| `datasource` | Connectors |
| `eval` | Measurement harness |
| `govern` | Seven-layer check, ledger, tool bounds |
| `measure` | Population / stats helpers |
| `model` | Chat and embedder adapters |
| `register` | Knobs, prompts, turn records, citations |
| `retrieve` | BM25, semantic channel, join planner |
| `serve` | Rails graph + agent loop |

## Configuration

Live configuration is environment variables (`GOVERNED_BI_*`, secrets in `.env`)
plus defaults in [`register/knobs.py`](../src/governed_bi/register/knobs.py).
See [usage](usage.md).

## What the turn is stamped with

`stamp` projects the record `register/record.py` declares. The fields a reader reaches
for first:

- **`outcome`** — `answered` / `refused` / `capped` / `crashed` / `clarification`, from
  `register/stages.classify_outcome`, derived from the **ledger** rather than from
  whether a SQL string exists.
- **`guardrail_errors`** — how many attempts died of an exception *inside* `check()`.
  Derived from the attempts, never counted alongside them.
- **`terminal_reason`** — why a refusal or decline ended the way it did, so that
  "routing found nothing" and "the join graph is disconnected" are not one row.
- **`execution`** — every attempt, with its verdict layer, reason code and executor
  path.

> **There is no reliability stamp on any path.** A turn carries the fields above and
> nothing that summarises them. A single collapsed trust score is worse than none, and
> a verdict needs a definition of what measures it before it needs a field — so the
> record carries only what something observes. `tests/api/test_http_contract.py` fails
> if `safety_clearance` or `semantic_assurance` appears in `src/`.
