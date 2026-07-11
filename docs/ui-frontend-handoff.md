# Frontend handoff: governed-bi UI

_[English](ui-frontend-handoff.md) · [简体中文](ui-frontend-handoff.zh.md)_

The build brief + contract for the **governed-bi** frontend. Pair with the
architecture rationale in [ui-frontend-design.md](ui-frontend-design.md) and the
runtime decision in [ADR 0001](adr/0001-langgraph-server-chat-runtime.md).

> **Status: the backend rework has landed; this contract is live.** Chat is served
> by a **LangGraph Server** (graph id `serve`) consumed by the **`useStream`** SDK,
> with corpus/schema/audit as **custom routes** on that same server, a dev **edit**
> endpoint, and a **full knowledge graph**. Boot it with `langgraph dev` (§2) and
> build against it directly. Implementation detail is in
> [langgraph-rework-plan.md](langgraph-rework-plan.md).

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
  `can_edit`, `can_history`, `environment`, `dialect`.

---

## 3. Chat via `useStream` (LangGraph protocol)

```tsx
type ChatState = { messages: Message[]; answer: GovernedAnswer | null };

const [threadId, setThreadId] = useState<string | null>(null);
const stream = useStream<ChatState>({
  apiUrl: process.env.NEXT_PUBLIC_LANGGRAPH_URL!,
  assistantId: process.env.NEXT_PUBLIC_ASSISTANT_ID!, // "serve"
  threadId, onThreadId: setThreadId,                  // persist the thread id (history)
  onCustomEvent: (data, { mutate }) => mutate((p) => ({ ...p, stage: data })), // live stages
});
stream.submit(
  { messages: [{ type: "human", content: q }] },
  { streamMode: ["values", "messages", "custom"] },  // "custom" is required for stages
);
```
- **Messages / history:** `stream.messages` (thread-backed; reload/rejoin by
  `threadId`). Threads are the persistence; the frontend owns no conversation DB.
- **Live steps:** the graph emits one **custom event** per stage, delivered to the
  `onCustomEvent(data, { mutate })` option (the run must include `custom` in
  `streamMode`). Render the labeled rail **Route → Retrieve → Generate SQL →
  Guardrails → Execute → Compose**; repairs appear as `generate`/`guardrail`
  re-firing with a higher `attempt`, and `guardrail` events carry `passed` +
  `failed_layer`. This reflects real backend progress, not a timer.
- **Final answer** is a custom **`answer` state channel**: read `stream.values.answer`
  (the `AnswerResponse` shape). Render the **answer card**:
  - Two badges, never one score: `safety_clearance` (bool) + `semantic_assurance`
    (`certified|heuristic|unverified|none`); tier chip green/amber/red.
  - English answer text; collapsible **result table** (`columns`/`rows`,
    truncated note); read-only **SQL**; **provenance/audit drawer** (route,
    tables_used, join_ids, min_join_confidence, attempts, uncertainty_flags, …).
  - Refusal → escalation shown, no SQL/number.

Package note: `@langchain/langgraph-sdk/react` gives `onCustomEvent` +
`stream.values`; the newer `@langchain/react` superset adds selector hooks
(`useChannel`) and `stream.respond`. Either works against this server.

(The engine also keeps a non-streaming `POST /chat` fallback for a no-`agents`
offline profile; `capabilities.can_stream=false` selects it.)

---

## 4. Custom routes (REST, on the same server)

`fetch` these from `NEXT_PUBLIC_LANGGRAPH_URL`. Shapes mirror
`governed_bi.viz.presenter`; a machine-readable schema will be re-exported after
the rework.

| Method + path | Purpose |
|---|---|
| `GET /capabilities` | `{ environment, dialect, can_edit, edit_mode, can_stream, can_history, has_live_model, model }`; gate UI features on this |
| `GET /health` | corpus health: counts, `ci_green`, findings, `n_suspect_columns`, `n_excluded`, `n_low_confidence_joins` |
| `GET /schema` | tables + columns (types, roles, `reliability`, `excluded`, provenance) |
| `GET /graph` | **ER graph** `{ nodes, edges }` of tables + join edges (nodes carry `row_count`/`n_columns`/`has_suspect`; edges carry `on`/`cardinality`/`confidence`/`low_confidence`) |
| `GET /knowledge-graph` | **full knowledge graph** `{ nodes, edges }` over every asset kind (table/join/metric/term/rule/few_shot/negative_example); edges typed `join`/`measures`/`grounds`/`related:*`/`scopes`/`exemplifies`; filter/layer by `node.kind` (tables + joins reproduces the ER view). Columns are in `/schema`, not nodes here |
| `GET /corpus/assets?type=` | non-table assets (metric/term/join/rule/few_shot/negative) |
| `GET /skills` | skills (markdown) |
| `POST /corpus/edit` *(dev only; gated on `can_edit`)* | validate the submitted asset → write YAML (dev) / PR (prod); returns validation + diff |
| `GET /corpus/history?db=&asset_id=&limit=&skip=` *(gated on `can_history`)* | the corpus repo's git log as `{ commits: [{ sha, author, date, subject, changed_paths }], … }`; scope with `db` (growth timeline) or `asset_id` (one asset's evolution). Empty when the mounted corpus is not a git checkout |
| `GET /corpus/history/{sha}` *(gated on `can_history`)* | one commit's detail + unified diff `{ sha, author, date, subject, diff }` |

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

- **Built (this rework, offline-tested + `langgraph dev`-verified):** LangGraph
  Server chat graph (`serve`) + `langgraph.json`; a thin `{messages, answer}` chat
  state (no `ServeState` serialization needed); stage streaming via
  `get_stream_writer()`; custom routes mounted (`http.app`); `GET /knowledge-graph`
  (full graph) alongside `GET /graph` (ER); `POST /corpus/edit` (dev); LangSmith +
  Langfuse tracing (opt-in); re-exported [openapi.json](openapi.json). Plus the
  earlier `presenter` view models, REST reads, `stack` factory, non-streaming
  `/chat` fallback.
- **Deferred:** prod PR editing (dev is file-write today), public-demo cost
  strategy, auth/RLS, human-gate interrupts (the runtime supports them via
  `stream.interrupt` + `submit(command.resume)`).

Everything above is live behind `langgraph dev`; build against it now.

---

## 9. Suggested build order

1. Scaffold Next.js + Tailwind v4 + shadcn; wire `useStream` to a local
   `langgraph dev`; read `/capabilities`.
2. **Chat**: live stages + answer card + provenance drawer (threads = history).
3. **Schema & knowledge graph**: React Flow + detail.
4. **Corpus + health**; **editing** (dev, gated on `can_edit`).
5. Deploy: Vercel UI + hosted LangGraph Server; resolve the open items in the
   design doc §13.
