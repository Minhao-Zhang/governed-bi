# Frontend handoff: governed-bi UI

_[English](ui-frontend-handoff.md) · [简体中文](ui-frontend-handoff.zh.md)_

The build brief + contract for the **governed-bi** frontend. Pair with the
architecture rationale in [ui-frontend-design.md](ui-frontend-design.md) and the
runtime decision in [ADR 0001](adr/0001-langgraph-server-chat-runtime.md).

> **Status: contract is the TARGET; backend rework in progress, not ready to
> build against yet.** The chat runtime moves to a **LangGraph Server** consumed
> by the **`useStream`** SDK, with corpus/schema/audit as **custom routes** on
> that server, plus a dev **edit** endpoint and a **full knowledge graph**. See
> "Built today vs planned" (§8). Start scaffolding (stack, layout) now; wire live
> data once the backend rework lands.

---

## 1. Stack (decided)

- **Next.js (App Router) + React 19 + TypeScript (strict)** · **Tailwind CSS v4**
  (CSS-first `@theme`) · **shadcn/ui**.
- **Chat: `@langchain/react` `useStream`** against a **LangGraph Server**: gives
  reactive messages, **live node/stage events**, durable **thread** state
  (history), tool-call lifecycle, and reconnection.
- **React Flow** for the knowledge graph · **TanStack Query** for the custom REST
  reads · **zod** to validate custom-route responses.
- The UI is a **pure client**: `useStream` for chat, `fetch` for the custom
  routes; it adapts to `GET /capabilities`.

Env the frontend needs:
```
NEXT_PUBLIC_LANGGRAPH_URL=http://localhost:2024   # LangGraph Server (chat + custom routes)
NEXT_PUBLIC_ASSISTANT_ID=serve                    # graph name in langgraph.json
```

---

## 2. Run the backend (once the rework lands)

From the engine repo:
```bash
uv sync --extra agents --extra api                # agents = LangGraph/LangChain; api = custom routes
uv run --extra agents --extra api langgraph dev   # LangGraph Server at :2024 (chat + custom routes)
```
- Live model (NL answers + free-form SQL): set `OPENAI_API_KEY` (env or repo `.env`).
- Tracing (optional): set Langsmith (`LANGSMITH_API_KEY`, `LANGCHAIN_TRACING_V2=true`)
  and/or Langfuse (`LANGFUSE_*`) keys, no-op if unset.
- CORS: allow the UI origin (`http://localhost:3000`).
- **Local threads are ephemeral** under `langgraph dev` (durable persistence is the
  deployed Postgres). `/capabilities` reports `has_live_model`, `can_stream`,
  `can_edit`, `environment`, `dialect`.

---

## 3. Chat via `useStream` (LangGraph protocol)

```tsx
const stream = useStream<ServeState>({
  apiUrl: process.env.NEXT_PUBLIC_LANGGRAPH_URL!,
  assistantId: process.env.NEXT_PUBLIC_ASSISTANT_ID!,
});
```
- **Messages / history:** `stream.messages` (thread-backed; reload/rejoin by thread id).
- **Live steps:** consume node/stage events from the stream and render labeled
  stages: **Route → Retrieve → Generate SQL → Guardrails → Execute → Compose**
  (repairs appear as `generate`/`guardrail` re-firing). This reflects actual
  backend progress, not a timer.
- **Final answer** arrives as the terminal state; render the **answer card**:
  - Two badges, never one score: `safety_clearance` (bool) + `semantic_assurance`
    (`certified|heuristic|unverified|none`); tier chip green/amber/red.
  - English answer text; collapsible **result table** (`columns`/`rows`,
    truncated note); read-only **SQL**; **provenance/audit drawer** (route,
    tables_used, join_ids, min_join_confidence, attempts, uncertainty_flags, …).
  - Refusal → escalation shown, no SQL/number.

(The engine also keeps a non-streaming `POST /chat` fallback for a no-`agents`
offline profile; `capabilities.can_stream=false` selects it.)

---

## 4. Custom routes (REST, on the same server)

`fetch` these from `NEXT_PUBLIC_LANGGRAPH_URL`. Shapes mirror
`governed_bi.viz.presenter`; a machine-readable schema will be re-exported after
the rework.

| Method + path | Purpose |
|---|---|
| `GET /capabilities` | `{ environment, dialect, can_edit, edit_mode, can_stream, has_live_model, model }`; gate UI features on this |
| `GET /health` | corpus health: counts, `ci_green`, findings, `n_suspect_columns`, `n_excluded`, `n_low_confidence_joins` |
| `GET /schema` | tables + columns (types, roles, `reliability`, `excluded`, provenance) |
| `GET /graph` | **full knowledge graph** `{ nodes, edges }` over all asset types (table/column/metric/term/join/rule/few_shot/negative) + references; filter/layer by `node.kind`; joins carry `confidence`/`cardinality`/`low_confidence` |
| `GET /corpus/assets?type=` | non-table assets (metric/term/join/rule/few_shot/negative) |
| `GET /skills` | skills (markdown) |
| `POST /corpus/edit` *(dev only; gated on `can_edit`)* | validate the submitted asset → write YAML (dev) / PR (prod); returns validation + diff |

---

## 5. Knowledge-graph view (React Flow)

- Nodes typed by `kind`; custom node cards; **per-type filters/layers**.
- Edge styling by relation + `low_confidence` (dashed/red) + `cardinality`.
- Badges for `excluded` / `has_suspect`. Click → detail from `/schema` or
  `/corpus/assets`.

---

## 6. Persistence

Handled by the **LangGraph runtime** (threads/checkpoints); the frontend does
**not** own a conversation DB. `useStream` loads/rejoins threads by id. Ephemeral
locally; durable Postgres when deployed. (A thin app-metadata DB is only added if
thread metadata is insufficient.)

---

## 7. Editing (dev)

When `capabilities.can_edit`, show edit forms for corpus assets; submit to
`POST /corpus/edit`. Backend validates then writes the file (dev); surface the
returned validation findings + diff. In prod this becomes a PR (deferred); the UI
path is the same.

---

## 8. Built today vs planned

- **Built (earlier phase, offline-tested):** `presenter` view models, REST read
  endpoints (as a standalone FastAPI), the `stack` factory, a **tables+joins**
  graph, a non-streaming `/chat`, 8 API tests.
- **Planned (this rework, needed before handoff):** LangGraph Server +
  `langgraph.json`, `ServeState` serializability, node→stage streaming,
  **full knowledge graph** `/graph`, `POST /corpus/edit` (dev), custom-route
  mounting, Langfuse/Langsmith tracing, re-exported OpenAPI.
- **Deferred:** prod PR editing, public-demo cost strategy, auth/RLS.

Scaffold the UI (stack, routing, `useStream` skeleton, component shells) now; bind
to live endpoints as the planned items land.

---

## 9. Suggested build order

1. Scaffold Next.js + Tailwind v4 + shadcn; wire `useStream` to a local
   `langgraph dev`; read `/capabilities`.
2. **Chat**: live stages + answer card + provenance drawer (threads = history).
3. **Schema & knowledge graph**: React Flow + detail.
4. **Corpus + health**; **editing** (dev, gated on `can_edit`).
5. Deploy: Vercel UI + hosted LangGraph Server; resolve the open items in the
   design doc §13.
