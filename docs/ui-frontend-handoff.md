# Frontend handoff: governed-bi UI

_[English](ui-frontend-handoff.md) · [简体中文](ui-frontend-handoff.zh.md)_

The build brief + contract for the **governed-bi** frontend. Pair with the
architecture rationale in [ui-frontend-design.md](ui-frontend-design.md) and the
runtime decision in [ADR 0001](adr/0001-langgraph-server-chat-runtime.md).

> **Status: the backend rework has landed; this contract is live.** Chat is served
> by a **LangGraph Server** (graph id `serve`) consumed by the **`useStream`** SDK,
> with corpus/schema/audit as **custom routes** on that same server, a dev **edit**
> endpoint, and a **full knowledge graph**. Boot it with `langgraph dev` (§2) and
> build against it directly. Runtime rationale is in
> [ADR 0001](adr/0001-langgraph-server-chat-runtime.md) and
> [ADR 0002](adr/0002-governed-agentic-serve-runtime.md).
>
> **Incoming design change — read §13 before touching the answer card.** The new
> [pipeline-design.md](pipeline-design.md) reworks how low-confidence answers
> are delivered: most refusals become **delivered-but-graded** answers the UI must
> render with a reliability treatment, not hide. Some of that contract is already
> live (the two-axis stamp, `graded_delivery`); some is gated on backend work. §13
> is the single source of truth for it.

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
- Tracing (optional; see `.env.example`):
  - LangSmith: `LANGSMITH_API_KEY` + `LANGSMITH_TRACING=true` (or legacy `LANGCHAIN_TRACING_V2=true`)
  - Langfuse: `uv sync --extra tracing`, then `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY`
- CORS: set `[serve].cors_origins` in `governed_bi.toml` (default includes `http://localhost:3000`).
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
- **Live steps:** the graph emits typed governance events, delivered to the
  `onCustomEvent(data, { mutate })` option (the run must include `custom` in
  `streamMode`). The current shape is `{seq, kind: "rail"|"tool"|"final", step,
  status, id?, detail, serve_path?}` from `GovEventStream` — a **dynamic rail +
  agent tool-loop** (`search_corpus` / `inspect_schema` / `sample_rows` /
  `run_query`), **not** a fixed 6-stage list. See
  **[agent-step-visualization.md](plans/agent-step-visualization.md)** for the
  authoritative event contract and the `buildStepsFromLedger` mapping. This
  reflects real backend progress, not a timer.
- **Final answer** is a custom **`answer` state channel**: read `stream.values.answer`
  (the `AnswerResponse` shape). Render the **answer card**:
  - Two badges, never one score: `safety_clearance` (bool) + `semantic_assurance`
    (`grounded|heuristic|unverified|none`); tier chip green/amber/red.
  - English answer text; collapsible **result table** (`columns`/`rows`,
    truncated note); read-only **SQL**; **provenance/audit drawer** (route,
    tables_used, join_ids, min_join_confidence, attempts, uncertainty_flags, …).
  - **Three answer states, not two — see §13.** A hard **refusal** (`sql == null`)
    shows escalation, no SQL/number. A **graded delivery** (`sql != null` +
    `semantic_assurance ∈ {unverified, none}`) shows the SQL/result **with a
    reliability warning treatment**. A clean answer renders normally. Do not gate
    the alternate branch on `tier === "refused"` alone.

Package note: `@langchain/langgraph-sdk/react` gives `onCustomEvent` +
`stream.values`; the newer `@langchain/react` superset adds selector hooks
(`useChannel`) and `stream.respond`. Either works against this server.

(`POST /chat` is a non-streaming REST alternative to the LangGraph stream — but
**not an offline fallback**: per ADR 0002 serve is agent-only, so `/chat` also
requires a live model and returns `503` without one.)

---

## 4. Custom routes (REST, on the same server)

`fetch` these from `NEXT_PUBLIC_LANGGRAPH_URL`. Shapes mirror
`governed_bi.viz.presenter`; a machine-readable schema will be re-exported after
the rework.

| Method + path | Purpose |
|---|---|
| `GET /capabilities` | `{ environment, dialect, can_edit, edit_mode, can_stream, can_scope, can_search, can_clarify, has_live_model, model }`; gate UI features on this |
| `GET /health` | corpus health: counts, `ci_green`, findings, `n_suspect_columns`, `n_excluded`, `n_low_confidence_joins` |
| `GET /schema` | tables + columns (types, roles, `reliability`, `excluded`, provenance). Namespace field is **`schema`**. Optional `?schema=&limit=&offset=` (param-less = the full dump). **`?db=` is not accepted.** |
| `GET /schema/summary?schema=&limit=&offset=` | **lean catalog** `{ total, items }` for the virtualized list + client search index; each item `{ id, physical_name, schema, row_count, n_columns, excluded, has_suspect, provenance_status, columns:[{physical_name, physical_type, role, reliability, excluded}] }` (heavy fields dropped; `total` is pre-pagination) |
| `GET /schema/{table_id}` | one table's **full** `TableResponse` (includes `schema`), fetched lazily on detail-open; `404` on unknown id |
| `GET /graph` | **ER graph** `{ nodes, edges, boundary?, meta? }` of tables + join edges (nodes carry `schema` / `row_count` / `n_columns` / `has_suspect`; edges carry `on`/`cardinality`/`confidence`/`low_confidence`). Optional D15 scope: `?schema=&focus=&radius=&node_budget=` — scoped responses include `boundary` + `meta` (echoed `scope` for `engineScopeMatches`). Param-less = full graph |
| `GET /knowledge-graph` | **full knowledge graph** `{ nodes, edges, boundary?, meta? }` over every asset kind (table/join/metric/term/rule/few_shot/negative_example); table nodes carry `schema`; edges typed `join`/`measures`/`grounds`/`related:*`/`scopes`/`exemplifies`. Same scope params as `/graph`, plus `?kinds=` (comma-separated) |
| `GET /columns/{column_id}/related` | every semantic-layer item that touches one physical column: `terms`, `rules`, `fk_out`, `fk_in`, `joins` (resolved server-side), `metrics` (table-grain). `column_id` = `col_<table>_<physical_name>`. Full contract in **§14** |
| `GET /corpus/assets?type=` | non-table assets (metric/term/join/rule/few_shot/negative) |
| `GET /skills` | skills (markdown); each skill carries **`schema`** (wire). Empty on some corpora — shape is in OpenAPI / zod regardless |
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
  earlier `presenter` view models, REST reads, `stack` factory, and the
  non-streaming `/chat` REST endpoint (also requires a live model).
- **Shipped since (server side):** human-gate **clarification interrupts** —
  `interrupt()` in `analyst/tools.py::ask_user`, resume via `submit(command.resume)`,
  gated by `capabilities.can_clarify` (contract:
  [hitl-clarification-contract.md](plans/hitl-clarification-contract.md)). For the
  frontend's build status, see [`governed-bi-ui`](https://github.com/Minhao-Zhang/governed-bi-ui)
  — it is not tracked here. Durable (Postgres) checkpointing of an interrupt is
  deferred.
- **Deferred:** prod PR editing (dev is file-write today), public-demo cost
  strategy, auth/RLS, durable HITL persistence.

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

## 10. Multi-schema serving (D15 — wire rename + graph scoping shipped)

The engine connects to **one database holding many schemas** with **executable
cross-schema joins** ([design-decisions.md](design-decisions.md) D15). Multi-schema
serve (qualified SQL + guardrails + missing-edge refusal), the **API wire
rename**, and **server-side graph scoping** are shipped; [openapi.json](openapi.json)
matches.

> **Shipped (wire + serve + graph scope):**
> - Namespace field is **`schema`** on `TableResponse`, `TableSummary`,
>   `SkillResponse`, and graph nodes. Filters use **`?schema=` only** — no `?db=`
>   alias (hard cut).
> - `GET /schema/summary`, `GET /schema/{table_id}`, `can_scope` / `can_search`.
> - Postgres/Redshift default to multi-schema; SQLite stays single-schema (BIRD).
> - Cross-schema missing curated join → refuse (`refused_by: "missing_edge"`) with
>   a D12 `clarification_hint`.
> - **`GET /graph` / `GET /knowledge-graph`** accept `?schema=` / `focus` /
>   `radius` / `node_budget` (KG also `kinds=`). Scoped responses include
>   `boundary` (cross-schema stubs) + `meta` (truncation + echoed `scope` for
>   `engineScopeMatches`). Param-less = full graph (back-compat). Defaults:
>   ER budget 60, KG 150, focus radius 1; hard ceilings match.
> - **On-disk corpus:** YAML / `TableAsset.schema` (hard cut; was `db`). Load/write
>   APIs take `schema=` on assets; serve loads every corpus subtree (no env pin).
> - **Schema router:** multi-schema serve shortlists schemas then expands along
>   curated cross-schema joins before RVGD (`routed_schemas` in provenance).
>
> **Still deferred:**
> - Server `/search` (client Fuse remains default per Q6).
> - `DataSourceConfig.corpus_pin` (BIRD db_id / default write subtree) still
>   distinct from the Postgres pin field `schema`.

Contract notes for the UI:

- **Single `schema` rail** — no two-level `db → schema` tree (database is a
  server-config constant).
- **Cross-schema joins are navigable**, not warnings (one engine; Q7 flipped).
- **Refusal** when no curated cross-schema join — surface like other refusals.
- Prefer engine-scoped graphs when `meta.scope` matches (`engineScopeMatches`);
  client re-scope remains the fallback for older engines.

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

## 12. Where to start (build now vs. gated)

**Live now — build against this:**

- Wire namespace is **`schema` only** (§4 / [openapi.json](openapi.json)). UI
  sends `?schema=`; zod requires `schema` (no `db` dual-accept).
- `/schema`, `/schema/summary`, `/schema/{id}` filter/page correctly.
- Graph endpoints accept scope params and return `boundary` / `meta` (§10).
- Chat, refusals (including `missing_edge`), editing when `can_edit`.

**Still deferred:**

- Server `/search` (client Fuse remains default).

**First move for a new engineer:** ship Schema-tab UX against the live
`schema` wire; trust engine `meta.scope` on graphs when it matches the request.

---

## 13. Reliability & deliver-and-grade (new design)

Source: [D5 (two-axis stamp + graded delivery)](design-decisions.md#d5-refusal--best-effort);
pinned-corpus context in [pipeline-design.md §1](pipeline-design.md). Motivation:
the reliability treatment is the product surface for the engine's
**deliver-and-grade** decision — a coverage / L3–L5 / execution failure delivers
the SQL with an `unverified` stamp instead of refusing, so the curated arm isn't
penalized for answerable questions it can only answer with a caveat. (A missing
cross-schema **join** still hard-refuses per D15; only that coverage/repair class
is graded.)

This section is the **single source of truth** for the reliability contract. It
**supersedes** the "surface like other refusals" guidance for `missing_edge` in
§10/§12 — that case is being reclassified (below).

### 13.1 The model: safety is binary, assurance is graded

Two independent axes (both already on `AnswerResponse`):

- **`safety_clearance: boolean` — hard, never graded away.** False ⇒ the query
  failed a safety gate (**L2 policy**: DDL/DML/injection, or the curated
  **negative-example** refuse-gate). A safety failure is **never delivered** at any
  reliability score. There is no "lower the number and run it anyway" for safety.
- **`semantic_assurance: grounded | heuristic | unverified | none` — graded.**
  This is the reliability indicator to color on. Driven by
  `provenance.uncertainty_flags` (fired signals): `low_confidence_join`
  (join-plan confidence < 0.7), `suspect_in_scope` (a curator-flagged decoy/suspect
  column was used), `repaired` (took >1 generate attempt), `fenced_raw_fallback`.
  No flags → `grounded`; `fenced_raw_fallback` → `unverified`; any other flag →
  `heuristic`.

`tier` (`governed | lineage | fenced_raw | refused`) is a legacy, **display-only
1:1 projection** of `semantic_assurance` — keep rendering it as a chip, but branch
logic on the two axes above, not on `tier`.

### 13.2 The three render states (exact rules)

| State | How to detect | Render |
|---|---|---|
| **Clean answer** | `sql != null` and `semantic_assurance ∈ {grounded, heuristic}` | Normal answer card; green/neutral tier chip. `heuristic` = mild caution note. |
| **Graded delivery** | `sql != null` and (`semantic_assurance ∈ {unverified, none}` **or** `provenance.graded_delivery === true`) | **Show the SQL + result table**, wrapped in a distinct **warning treatment** (amber/red border + banner): *"We produced this answer but could not fully verify it."* Plus the **why line** (§13.4). This is the new state most UIs get wrong by hiding it. |
| **Hard refusal** | `sql == null` (always `tier=refused`, `safety_clearance=false`, `result=null`) | Current refusal box: escalation text, no SQL/number. |

Key invariant to rely on: **a hard refusal always has `sql == null`/`result == null`;
a graded delivery always carries real `sql` + `result`.** So `sql == null` is the
reliable discriminator between "refused" and "delivered (at any assurance)". Do not
use `tier === "refused"` as the gate — a graded delivery is `tier = fenced_raw`, not
`refused`, but it must still look cautionary.

### 13.3 What's live vs. what the backend still owes you

- **Live in the contract today:** both axes; `provenance.graded_delivery` marker;
  `provenance.uncertainty_flags`; the `graded_delivery` **stream event**; and the
  whole deliver-and-grade code path (`analyst/answer.py::graded_delivery`,
  `_finish_unsuccessful`). You can build 13.1–13.4 against the current shapes now.
- **Inert until a flag flips:** deliver-and-grade is behind the engine setting
  **`grade_semantic_failures`**, which **defaults `false` in serving** (it's on only
  in the eval harness). Until the backend turns it on for serve, every semantic
  failure still arrives as a **hard refusal** (`sql=null`) — so the graded-delivery
  branch is correct but simply won't fire yet. **Rollout coupling: the §13.2 UI must
  land before/with that flag flip**, or users will suddenly get `fenced_raw` answers
  with only a faint badge to warn them.
- **Refused_by reasons** (in `provenance.refused_by`): `refuse_gate`, `no_coverage`,
  `guardrail`, `execution`, `missing_edge`. When `grade_semantic_failures` is on,
  only `refuse_gate` and the **policy_blacklist** guardrail stay hard refusals; the
  rest (`no_coverage`, repairable `guardrail`, `execution`, `missing_edge`) become
  graded deliveries. **`missing_edge` reclassified:** it is no longer a hard refusal
  (superseding §10/§12) — it becomes a single-schema answer or a graded delivery.

### 13.4 The "why" line (turn flags into plain language)

A graded delivery must tell the user *why* it's flagged, on the card (not only in the
drawer). Map `provenance.uncertainty_flags` → text:

- `low_confidence_join` → "Joined tables on a relationship we're not fully sure of."
- `suspect_in_scope` → "Used a column that may be unreliable (flagged during curation)."
- `repaired` → "Needed multiple attempts to produce valid SQL."
- `fenced_raw_fallback` → "Fell back to a raw query without the governed layer."

`min_join_confidence` and `attempts` are already in `provenance` (and already render
in the drawer). If `suspect_columns` is added to `provenance` (see 13.6) name the
specific column.

### 13.5 Contract additions to request from the backend (not live yet)

Small, additive; none break existing shapes:

1. **`delivery: "governed" | "graded" | "refused"`** on `AnswerResponse` — a
   first-class field so the UI branches on it instead of inferring from
   `sql == null` + `tier` + rummaging `provenance.graded_delivery`. Recommended.
2. **`provenance.suspect_columns: string[]`** — so the why-line can name the column.
3. **`provenance.selected_schema` (+ `candidate_schemas`)** — see 13.6.
4. **`provenance.corpus_version` (git hash)** — see 13.7.

Until (1) lands, derive `delivery` client-side per §13.2. Fields (2)–(4) render for
free once present (provenance is an open `Record`; add to the drawer's
`PREFERRED_ORDER` for placement).

### 13.6 Schema selection display (gated on backend)

Design target (§5.1): retrieval shortlists ~3 schemas → an **LLM node picks one** →
downstream uses only that schema; the UI shows which schema answered.

- **Not built yet.** The engine today does a deterministic **BM25 shortlist +
  curated-join expansion into a set** (`schema_router.route_schemas`), exposed as
  `provenance.routed_schemas` (an unordered set) and a `schema_route` stream event.
  There is **no single "selected" schema, no candidate ranking/scores, and no LLM
  pick**.
- **UI now (interim):** you may show `provenance.routed_schemas` as "schemas
  considered", and add a **"Selecting schema"** step to the stage stepper (one
  `STAGE_ALIASES` entry mapping the existing `schema_route` event — no component
  change).
- **UI later (after backend adds the LLM-pick + `selected_schema`):** a small chip
  on the answer card — "answered using schema `X`" — and the candidate list in the
  drawer. Single-schema DBs (SQLite/BIRD) never show this.

### 13.7 Corpus version indicator (gated on backend)

Design (§1): production inference reads a **pinned corpus git hash**, never the live
working copy. For reproducibility/trust the answer should show which corpus version
produced it.

- **Nothing exists today** — no corpus hash/version field anywhere in the contract.
  Needs backend wiring (corpus loader → `provenance.corpus_version` → presenter)
  before any UI.
- **UI (once present):** a quiet "corpus @ `abc1234`" indicator in the provenance
  drawer or chat header. Low priority; trivial once the field ships.

### 13.8 SME clarification surface (scope decision — may be out of scope here)

Design (§4): an async round-trip where a **human SME answers the curator's open
clarification questions**, folded back via `accept_answer`.

- **Nothing exists** in the UI (the corpus "Edit" button is a `toast()` stub, though
  `POST /corpus/edit` + `EditResponse` plumbing is real).
- **Open decision, do not assume:** per [scope boundaries](design-decisions.md)
  corpus-edit + save-to-PR is owned by the enterprise app / git+CI, **not** this
  repo. So the SME-answering UI may belong elsewhere. If it *is* in scope for
  `governed-bi-ui`, it is the largest net-new surface: a list of open clarifications
  (question, target asset, context) → an answer form → submit → `accept_answer`
  → show the resulting corpus diff. Confirm ownership before building.

### 13.9 Build order for §13

1. **Answer card three-state rendering + reliability treatment + why-line** (13.2,
   13.4) — pure UI against the live contract; the highest-value change. Reuses the
   existing `ReliabilityStamp` and open `provenance`.
2. Request the **`delivery`** field (13.5#1); switch the branch to it when it lands.
3. Add the **"Selecting schema"** stepper alias and interim `routed_schemas` display
   (13.6); the answer-time chip waits on the backend LLM-pick.
4. Corpus-version indicator (13.7) and SME surface (13.8) — gated / decision-pending.

---

## 14. Column → related semantic items

Lets the UI do **"click a column → see every semantic-layer asset that touches it."**
The corpus already holds all these links at column granularity; `presenter.knowledge_graph`
**collapses** them to table grain (a column-targeted binding/scope is redirected up to
its owning table via `col_to_table`, so `/knowledge-graph` never exposes the column).
This endpoint surfaces the column grain **without** disturbing that graph.

> **Status: live.** `GET /columns/{column_id}/related` is implemented
> (`presenter.related_to_column`, `ColumnRelatedResponse`) and in
> [openapi.json](openapi.json). **Phase 1 of the UI still needs nothing from the
> backend** — FK in/out is already on `ColumnResponse.references`, and joins can be
> shown at table grain from `/schema` + `/graph`. The **rich per-column view**
> (terms, rules, rule scope, precise join-touches-this-column) uses this endpoint.

### 14.1 Endpoint

`GET /columns/{column_id}/related`

- `column_id` is the **derived** column id: `col_<table_id without the 'tbl_' prefix>_<physical_name>`
  — e.g. `col_beer_factory_customers_CustomerID` (see `corpus.ids.derive_column_id`).
  This is the **same id** used by `Column.references`, `TermBinding.asset_id`, and
  `RuleAsset.scope` entries.
- `404` when the id does not resolve to a known column.

### 14.2 Response (`ColumnRelatedResponse`)

```jsonc
{
  "column": {
    "id": "col_beer_factory_customers_CustomerID",
    "table_id": "tbl_beer_factory_customers",
    "table_physical_name": "customers",
    "schema": "beer_factory",          // namespace field, same convention as elsewhere
    "physical_name": "CustomerID"
  },
  "terms": [                            // TermAsset.binding targets this column (KG relation: "grounds")
    { "id": "term_customer_id", "name": "customer id", "synonyms": ["cust id"],
      "confidence": 0.9, "provenance_status": "draft" }
  ],
  "rules": [                            // RuleAsset with this column id in `scope` (KG relation: "scopes")
    { "id": "rule_active_customer", "kind": "business_rule",
      "statement": "…", "confidence": 0.8, "provenance_status": "draft" }
  ],
  "fk_out": {                           // this column's own Column.references, resolved; null if not an FK
    "column_id": "col_beer_factory_orders_CustomerID",
    "table_id": "tbl_beer_factory_orders", "physical_name": "CustomerID"
  },
  "fk_in": [                            // columns elsewhere whose `references` == this column
    { "column_id": "col_beer_factory_orders_CustomerID",
      "table_id": "tbl_beer_factory_orders", "physical_name": "CustomerID" }
  ],
  "joins": [                            // JoinAsset whose ON predicate touches this column (resolved server-side)
    { "id": "join_customers_orders", "left_table": "tbl_beer_factory_customers",
      "right_table": "tbl_beer_factory_orders", "other_table_id": "tbl_beer_factory_orders",
      "on": "customers.CustomerID = orders.CustomerID",
      "cardinality": "one_to_many", "confidence": 0.95, "low_confidence": false }
  ],
  "metrics": [                          // table-grain ONLY — see 14.4
    { "id": "metric_customer_count", "name": "customer count", "granularity": "table" }
  ],
  "meta": { "column_resolvable": true }
}
```

All list fields are `[]` (never `null`) when empty; `fk_out` is the only nullable
field. Each item carries its `provenance_status` / `confidence` so the UI can flag
`draft`/low-confidence links the same way it does elsewhere.

### 14.3 Id-scheme rule (the one gotcha)

Two different column identifier schemes coexist — get this right or joins won't line up:

- **Asset-id scheme** — `col_<table>_<physical_name>` (from `derive_column_id`). Used
  by `TermBinding.asset_id`, `RuleAsset.scope`, and `Column.references`. `terms`,
  `rules`, `fk_out`, and `fk_in` key on this directly.
- **Physical-predicate scheme** — `JoinAsset.on` is a raw SQL equality string over
  **physical** names (`"customers.CustomerID = orders.CustomerID"`), **not** col ids.
  So `joins` are resolved **server-side**: each `JoinAsset` already carries
  `left_table` / `right_table` as **asset ids**, so the server parses `on` into
  `(physical_table, physical_column)` pairs and maps them back to col ids via
  `derive_column_id` against those two endpoint tables. **The frontend must not match
  a `col_` id against `on` strings itself** — the strings are physical, and physical
  names can collide across schemas.

### 14.4 Metrics are table-grain only

`MetricAsset` has `base_table` (a table id) + `expression` (semantic prose, not SQL);
there is **no structured physical column**. So `metrics` returns metrics whose
`base_table` is this column's table, tagged `"granularity": "table"`. The UI must
label these **"metrics on this table,"** not "metrics using this column." Column-precise
metric resolution is out of scope until SQL-gen-level expression resolution exists.

### 14.5 Why an endpoint, not column nodes in `/knowledge-graph`

Rejected: adding column nodes to the global KG. The KG is node-budget capped (KG
default/ceiling **150**; §10) and **"columns are not nodes"** is an invariant that
`viz/scope.py`, boundary detection, and the ER-view filter all rely on. A real schema
has far more than 150 columns, so column nodes would blow the budget and truncation
would start dropping real assets. A **focused per-column endpoint** is cheaper and
leaves the graph invariant intact. (If a graph rendering is ever wanted, prefer
`/knowledge-graph?focus=<col_id>` semantics over globally materializing column nodes.)

### 14.6 Build order for §14

1. **Phase 1 (no backend work):** column-detail panel showing FK in/out from
   `ColumnResponse.references` + joins at table grain from `/graph`. Ship now.
2. **Phase 2 (this endpoint — live):** wire `GET /columns/{column_id}/related`; render
   terms, rules, server-resolved joins, and table-grain metrics. `zod`-validate the
   response against `ColumnRelatedResponse` in [openapi.json](openapi.json).

---

## 15. Governance ledger on the answer (contract clarification)

Raised during the column discussion; recorded here because it's a live contract point,
not a column feature.

- **Agent serve path:** `answer.provenance.governance_ledger` **is** populated — a list
  of `{action, verdict, sql, allowed, licensed_ids, layer, reason, result, attempt}`
  records — and flows through `presenter.answer_view` (which copies the whole
  provenance dict) into `AnswerResponse.provenance`. `analyst/agent.py` even has a
  belt-and-suspenders fallback that attaches it when missing. So on the agent path the
  frontend's `buildStepsFromLedger` has its durable source: the trace survives a page
  reload and a non-streaming answer, independent of the live event stream.
- **There is only one serve path now.** The ADR 0002 P2 cutover deleted the
  deterministic flow, so **every** served answer carries a governance ledger; a
  ledger that looks missing means an older build, not a second path.

`provenance` is an open `Record`; `governance_ledger` renders for free once the drawer
knows to look for it (add it to the drawer's `PREFERRED_ORDER` for deterministic
placement).
