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
  → route → resolve → connect → assemble → abstain
  → agent_core → reflect → narrate → stamp → [record] → END

guard blocked ───────────────────────────────→ refuse  ─┐
negative hit / route / connect / abstain ────→ decline ─┼─→ stamp
any node raising (wrap_node) ───────────────────────────┘
```

`accept` and `record` are bracketed because both are optional arguments to
`build_graph`: `accept` (the client-facing path) also switches the graph to the
`ServeInput` / `ServeOutput` schemas, and without `record` the graph runs
`stamp → END`. Every terminal path funnels through `stamp`, including crashes —
`wrap_node` turns a node exception into `failure` + `path_kind: crashed` and
routes there rather than letting it escape the graph.

`record` writes nothing itself. It returns the finished turn onto `ServeState.turns`, an
accumulating channel, and the durable checkpointer `langgraph.json` mounts is what persists it —
which is why the audit surface reads thread state and there is no second store
([ADR 0014](adr/0014-one-conversation-store.md)). It is the only producer of a turn record; the
REST chat pair that was the second one is deleted.

| Stage | Role |
|---|---|
| `guard` | Five deterministic rules (`govern/guard.py::GUARD_RULES`), then a model-backed BI-scope gate on the utility model. **Enabled per rule id, and `guard_rules_enabled` ships `UNSET`** — the served app (`api/graph_app.py`) turns on `g_bi_scope` and nothing else; the eval driver, the one-turn CLI and `tools/` all pass `{}`, so no guard rule fires on any measured arm |
| `rewrite` | Stub rail today; facet query rewriting lives inside `facet_*` |
| `negative_gate` | Negative-example decline path. A stub today: `negative_tau` ships `UNSET` and the served corpus holds no `negative_example` asset, so the node writes `outcome: disabled` on every turn and the `decline` branch is unreachable |
| `facet_*` | Parallel retrieval channels (each may rewrite its query) |
| `route` / `resolve` / `connect` | Schema pick, budgets, Steiner join |
| `assemble` | Render retrieval context block |
| `abstain` | The declared abstention policy — deterministic predicates over recorded state, no score. `abstention_policy_enabled` ships `False`, so it writes a `disabled` verdict and routes on ([ADR 0013](adr/0013-the-declared-abstention-policy.md)) |
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

**Authorization is a wired seam that ships open.** `govern/access.py` holds an `AccessPolicy`
port with two adapters, and the TABLES and COLUMNS layers ask the resulting grant
(`r_table_not_authorized`, `r_column_not_authorized`, `r_row_predicate_unenforced`).
`api/graph_app.py::access_policy_from_environment` is the composition root and the only place in
`src/` that chooses one: `OpenAccessPolicy` unless `GOVERNED_BI_ACCESS_POLICY` names a
`StaticRoleAccessPolicy` TOML file, and a `RuntimeError` rather than a fallback if that file is
missing. `resolve_access_grant` asks it once for the principal
`api/auth.py::authenticated_principal` resolves — one principal, asserted rather than
authenticated, since no route asks a caller for anything ([usage](usage.md#serve-langgraph-server))
— and the grant rides on `GovernancePolicy`. The
default grant authorizes everything, so on a stock install those three rules never fire — what the
grant *does* narrow when one is configured, and what it deliberately does not, is
[ADR 0012 §8](adr/0012-access-seam-principal-and-authorization.md). What a fork implements, in
what order: [enterprise fork](enterprise-fork.md).

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
| `govern` | The layer stack (six that run, `COST` declared and off), ledger, tool bounds |
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

- **`outcome`** — `answered` / `refused` / `capped` / `crashed` / `clarification` /
  `no_sql`, from `register/stages.classify_outcome`, derived from the **ledger** rather
  than from whether a SQL string exists. `no_sql` is a turn that ended having executed no
  governed statement: it is not `answered`, because an answer with no auditable statement is
  not a governed answer, and not `crashed`, because nothing failed. Rows written before
  2026-08-18 record those turns as `answered`, so a rate spanning that date mixes two
  taxonomies (ADR 0006 §5).
- **`guardrail_errors`** — how many attempts died of an exception *inside* `check()`.
  Derived from the attempts, never counted alongside them.
- **`terminal_reason`** — why a refusal or decline ended the way it did, so that
  "routing found nothing" and "the join graph is disconnected" are not one row.
- **`execution`** — every attempt, with its verdict layer, reason code and executor
  path.

> **There is no reliability stamp on any path.** A turn carries the fields above and
> nothing that summarises them. A single collapsed trust score is worse than none, and
> a verdict needs a definition of what measures it before it needs a field — so the
> record carries only what something observes.
> `tests/api/test_http_contract_answer_and_stream.py` fails if `safety_clearance` or
> `semantic_assurance` appears in `src/`.
