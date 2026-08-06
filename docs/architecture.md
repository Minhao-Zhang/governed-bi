# Architecture

How a question becomes a stamped answer in this tree. Binding detail lives in
[ADR 0005](adr/0005-v2-memory-layer-and-faceted-retrieval.md) (retrieval /
memory) and [ADR 0006](adr/0006-execution-time-governance.md) (governance).

## Serve spine

Wired in [`serve/graph.py`](../src/governed_bi/serve/graph.py). Nodes live under
[`serve/nodes/`](../src/governed_bi/serve/nodes/).

```
accept → guard → rewrite → negative_gate
  → fanout ─┬─ facet_schema
            ├─ facet_term
            ├─ facet_metric
            ├─ facet_entity
            └─ facet_example
  → route → resolve → connect → assemble
  → agent_core → narrate → stamp → record
```

| Stage | Role |
|---|---|
| `guard` | LLM / structured scope gate |
| `rewrite` | Utility-model rewrite for most facets |
| `negative_gate` | Negative-example refuse path |
| `facet_*` | Parallel retrieval channels |
| `route` / `resolve` / `connect` | Schema pick, budgets, Steiner join |
| `assemble` | Render retrieval context block |
| `agent_core` | Nested `create_agent` loop (read-only tools) |
| `narrate` | Short answer over the result table |
| `stamp` | `safety_clearance` + `semantic_assurance` |

`agent_core` tools: `read_body`, `inspect_schema`, `sample_rows`, `run_query`,
`ask_user`. Governance wraps `run_query` / `sample_rows` ([ADR 0006](adr/0006-execution-time-governance.md)).
The retrieval context block is injected per model call via middleware; it is not
appended as an ordinary user message.

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

## Two stamps, not one trust score

- **`safety_clearance`** — did the delivery path clear the guardrails / authorization surface.
- **`semantic_assurance`** — whether uncertainty flags fired (`unflagged` / `heuristic` / `unverified`). `unflagged` means no flag fired; it is not “verified correct.”
