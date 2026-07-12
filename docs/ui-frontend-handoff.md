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
  `can_edit`, `environment`, `dialect`.

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
| `GET /capabilities` | `{ environment, dialect, can_edit, edit_mode, can_stream, can_scope, can_search, has_live_model, model }`; gate UI features on this |
| `GET /health` | corpus health: counts, `ci_green`, findings, `n_suspect_columns`, `n_excluded`, `n_low_confidence_joins` |
| `GET /schema` | tables + columns (types, roles, `reliability`, `excluded`, provenance). Optional `?db=&limit=&offset=` (param-less = the full dump) |
| `GET /schema/summary?db=&limit=&offset=` | **lean catalog** `{ total, items }` for the virtualized list + client search index; each item `{ id, physical_name, db, row_count, n_columns, excluded, has_suspect, provenance_status, columns:[{physical_name, physical_type, role, reliability, excluded}] }` (heavy fields dropped; `total` is pre-pagination) |
| `GET /schema/{table_id}` | one table's **full** `TableResponse`, fetched lazily on detail-open; `404` on unknown id |
| `GET /graph` | **ER graph** `{ nodes, edges }` of tables + join edges (nodes carry `row_count`/`n_columns`/`has_suspect`; edges carry `on`/`cardinality`/`confidence`/`low_confidence`) |
| `GET /knowledge-graph` | **full knowledge graph** `{ nodes, edges }` over every asset kind (table/join/metric/term/rule/few_shot/negative_example); edges typed `join`/`measures`/`grounds`/`related:*`/`scopes`/`exemplifies`; filter/layer by `node.kind` (tables + joins reproduces the ER view). Columns are in `/schema`, not nodes here |
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

---

## 10. Multi-schema serving (decided — D15, not yet shipped)

The engine is moving to **one database holding many schemas** with **executable
cross-schema joins** ([design-decisions.md](design-decisions.md) D15). This is a
**decided direction, not yet shipped**: today's contract above — and
[openapi.json](openapi.json) — still uses the flat `db` field and serves a single
schema. Build against the current contract now; treat this section as the backend
answers to the navigation proposal in the frontend's own `DESIGN_QUESTIONS.md`.

> **Shipped since (see §4):** the additive read layer is live — `GET /schema/summary`,
> `GET /schema/{table_id}`, optional `?db=&limit=&offset=` on `/schema`, and the
> `can_scope`/`can_search` flags. It still uses the flat `db` field. **Still gated
> (the D15 backend build):** the `db → schema` rename, and the `focus`/`radius`/`node_budget`
> bounded graph + `meta`/`boundary` envelope (Phase 2).

Contract changes to expect (coordinate the release in lockstep):

- **`db` → `schema` field rename.** `TableResponse.db` and `SkillResponse.db`
  become `schema`, and the ER / knowledge-graph nodes carry `schema`. This is the
  **one externally-visible OpenAPI break** — rename it in the UI in lockstep with
  the engine release. There is **no** separate `db` / connection level (the
  database is a server-config constant), so the navigation backbone is a **single
  schema rail**, not a two-level `db → schema` tree.
- **Scope-on-demand instead of whole-corpus dumps.** The lean, scopeable,
  paginated endpoints the frontend proposed (`/schema/summary?schema=`,
  `/schema/{id}`, and `?schema=&focus=&radius=&node_budget=` on the graphs) are
  accepted as the target and gated on new capability flags. A search-first landing
  with a client-side Fuse index is the default; a server `/search` stays deferred.
- **Cross-schema joins are navigable, executable relationships — not warnings.**
  With exactly one database, a cross-schema join *does* run, so the frontend's Q7
  flips: render it as a normal boundary you can traverse into, not a governance
  warning. The old cross-*database* warning case does not exist here.
- **Refusal is a first-class answer state.** When no curated relationship connects
  two schemas for a question, the engine **refuses** rather than guessing a join
  (D15). Surface it like the existing refusal (escalation, no SQL / no number),
  and optionally as a prompt to request the relationship via the clarification
  loop.
- **New capability flags** (`can_scope`, `can_search`) let the UI light up the new
  flow and fall back to today's flat behavior against a pre-D15 engine.

None of this touches the chat transport or the answer card; it reshapes the
**Schema tab** navigation and renames a single field.

---

## 11. Resolved: the frontend's open questions (`DESIGN_QUESTIONS.md` §9)

The backend owner's answers to the eight questions in the frontend's
`DESIGN_QUESTIONS.md`. Where an answer changes the contract, §10 already carries it.

| # | Question | Answer |
|---|---|---|
| Q1 | Two-level `db → schema` tree, or flat? | **Flat.** One database holds many schemas; the corpus models `schema → table` and there is no `db` / connection level (the database is a server-config constant). Navigate by the single `schema` rail; do **not** build a two-level tree. |
| Q2 | Do real deployments put hundreds of tables in one schema? | Not in BIRD (~11 tables/schema; beer_factory = 9), but **yes** in real enterprise schemas. So the schema rail alone nearly covers BIRD scale; **Phase 2** (focus/radius + within-namespace sub-grouping) is mandatory only for large single schemas — build it when a target corpus needs it. Phase 1 is worth doing regardless. |
| Q3 | Wire field `schema` or `schema_name`? | **`schema`** (domain-accurate; `/schema` is a route path, and a zod `schema` key is fine). Fall back to `schema_name` only if the zod ergonomics bite, and never split the token. |
| Q4 | `node_budget` sizing; who enforces? | **Server-enforced hard ceiling; the client may request a lower value.** Start near **50–60 ER cards** and **~150 semantic-graph glyphs** — DOM-weight guesses to measure on target hardware, not final numbers. |
| Q5 | Within-namespace sub-grouping key? | **Connected component** (join-reachability = query-relevant clusters) is most meaningful to auditors; **table-name prefix** is the cheap deterministic default; **grain** needs curator input. Default to connected-component with a name-prefix fallback. |
| Q6 | Is server `/search` worth building? | **Not at expected sizes.** A client Fuse index over `/schema/summary` is sufficient; server FTS is real, unspecified work and stays deferred (interesting only at tens-of-thousands-of-tables scale). |
| Q7 | Cross-db boundary as a governance warning? | **No — it flips.** With one database a cross-*schema* join executes, so render it as a normal navigable relationship (§10). Cross-*database* (federation) is out of scope and does not occur here. |
| Q8 | Can the engine return a stable truncation order? | **Yes.** When `node_budget` truncates a neighborhood the survivors are deterministic: **BFS from the focus node, ordered by edge confidence desc, then id asc**. Cached scopes and "expand" never reshuffle. |

---

## 12. Where to start (build now vs. gated on the backend)

The D15 multi-schema work (§10) is **decided but not yet built**, so split the work in two.

**Build now — client-side, backward-compatible against today's backend and mock mode:**

- The whole **Phase 1** of `DESIGN_QUESTIONS.md`: a search-first landing with a
  client **Fuse** index; a **lazy detail sheet** (fetch a table's full
  columns/samples only when opened); a **virtualized** table browser; group the
  landing by the existing `db` field; and the render hot-path fixes (the O(E·N)
  `resolveEndpoints` map, memoizing dagre by a stable scope key, and replacing
  fitView-to-everything with a sane default + jump-to-focus). None of this needs a
  backend change — it runs against the current `/schema`, `/graph`,
  `/knowledge-graph`, and in mock mode.
- **Now also live on the backend (§4):** `/schema/summary` (lean, with
  `?db=&limit=&offset=`), `/schema/{table_id}` (lazy full detail), and the
  `can_scope`/`can_search` flags. So Phase 1 can page the real server catalog and
  lazy-load detail directly, not only a client-derived summary. Gate on
  `capabilities.can_scope`.
- Everything already live behind `langgraph dev` (§8): chat with live stages, the
  answer card, the provenance drawer, and the current schema/graph views.

**Gated on the D15 backend build (see §10; not yet shipped):**

- The `db → schema` field rename (the one breaking OpenAPI change; the shipped
  read layer still uses `db`).
- The **bounded-graph** layer — `focus`/`radius`/`node_budget` scoping on `/graph`
  and `/knowledge-graph` with the `meta` / `boundary` envelope, and the schema rail
  as server-scoped navigation (**Phase 2**).
- Server-side `/search` (the client Fuse index stays the default).

Gate every gated item on `capabilities.can_scope` / `can_search` and fall back to
today's flat behavior when the flags are absent, so the UI runs unchanged against
both the current engine and the D15 engine.

**Rename coordination (the one breaking change).** `db → schema` on
`TableResponse` / `SkillResponse` and the graph nodes ships in a single backend
release with a version bump; the UI renames the zod field in that same release and
reads `db` until then. The UI's fail-loud zod `.parse()` surfaces any mismatch
immediately, so the two repos cannot silently drift.

**First move for a new engineer:** do Phase 1 now — it is client-only, fixes the
payload/render problem immediately, and needs nothing from the backend. Hold
Phase 2 and the field rename until the D15 backend build lands.
