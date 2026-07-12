# governed-bi UI: Design

_[English](ui-frontend-design.md) · [简体中文](ui-frontend-design.zh.md)_

A separate **Next.js + React + Tailwind CSS v4 + TypeScript** frontend for the
`governed-bi` engine, built on the **LangChain frontend SDK (`useStream`)** over a
**LangGraph Server**, plus the corpus/schema/audit routes the server exposes.

> **Status: the backend rework has landed; the contract is live.** The design
> decisions below are locked (§2). The LangGraph-Server chat runtime, the custom
> corpus/schema/audit routes, the full knowledge-graph serializer, the dev edit
> endpoint, and tracing have all shipped (this doc's §5 and phase list §14 predate
> that and now read as rationale/history). **A new frontend engineer should start
> from the handoff — [ui-frontend-handoff.md](ui-frontend-handoff.md):** §9 (build
> order), §11 (resolved open questions), and §12 (build now vs. gated on the D15
> multi-schema backend build). The runtime pivot is recorded in
> [ADR 0001](adr/0001-langgraph-server-chat-runtime.md).

---

## 1. Goals & non-goals

**Goals**
- A UI over the governed serve flow: chat with **live per-step
  progress** (the governed pipeline, streamed from the backend), the two-axis
  reliability stamp, the result table, and provenance/audit drill-down.
- **Visualize the whole semantic layer** ("every memory piece") as a filterable
  **knowledge graph**: tables, columns, metrics, terms, joins, rules, few-shots,
  and negatives, plus table/column schema detail with governance flags.
- **Load + validate + edit** the corpus: editing sends an API call; in dev the
  backend writes the YAML file, in prod it opens a PR.
- **Trace the agent** with Langfuse + Langsmith.
- One codebase that runs local, as a public demo (bundled SQLite), and (later)
  internal against real databases, by configuration.

**Non-goals (near-term)**
- Multi-tenant auth / real RLS personas (engine RLS is an unimplemented seam).
- Durable local conversation storage as a hard requirement (local threads are
  ephemeral; durable persistence arrives with the deployed Postgres; see §7).
- Public-demo cost hardening (deferred; see §12).

---

## 2. Decision log

| Decision | Resolution | Supersedes |
|---|---|---|
| **Chat runtime** | **LangGraph Server + `useStream` SDK** | custom FastAPI `/chat` |
| **Live progress** | LangGraph **node streaming** via the SDK, mapped to labeled stages | client-side simulated loader |
| **Persistence** | **LangGraph threads/checkpoints**: ephemeral local (`langgraph dev`), durable Postgres when deployed | frontend-owned Neon/Drizzle |
| **Non-graph endpoints** | **Custom routes on the LangGraph server** (`/schema`, `/graph`, `/corpus`, `/health`, `/corpus/edit`) | standalone FastAPI as sole backend |
| **Editing** | **Dev file-write now** (validate → write via existing primitives), PR later | "deferred / read-only" |
| **Visualization** | **Full-corpus knowledge graph**, filterable by asset type | tables+joins-only ER graph |
| **Observability** | **Langfuse + Langsmith**, env-gated, no-op when unset (new `tracing` extra) | (new) |
| **Identity** | Single demo identity; API identity-aware | n/a |
| **Frontend language** | **English only**; all repo docs bilingual | n/a |

This has two consequences: with LangGraph Server the API is no
longer "one request → one response" (it streams and holds durable thread state),
and it is no longer read-only (dev editing writes files). Both are intended.

---

## 3. System architecture

```
┌──────────────────────────────┐   useStream (LangGraph protocol)   ┌───────────────────────────┐
│  Next.js UI (pure client)    │ ─────────────────────────────────▶ │  LangGraph Server         │
│  @langchain/react useStream  │   threads · node stream · state     │   assistant = serve graph │
│  React Flow · shadcn · TW v4 │ ◀───────────────────────────────── │   (langgraph.json)        │
│                              │                                     │   ── custom routes ──     │
│                              │   GET /schema /graph /corpus /health│   presenter view models   │
│                              │   POST /corpus/edit                 │   POST /corpus/edit       │
└──────────────────────────────┘                                     │   ── graph nodes ──       │
                                                                     │   route→retrieve→gen→     │
                                                                     │   guardrail→execute→stamp │
                                                                     └────────────┬──────────────┘
                                                                        threads/checkpoints
                                                                     ┌────────────▼──────────────┐
                                                                     │ ephemeral (langgraph dev) /│
                                                                     │ Postgres (deployed)        │
                                                                     └────────────────────────────┘
                                     data source (per profile): SQLite | Postgres | Redshift
```

- **One backend, one base URL:** the LangGraph Server hosts the serve graph
  (chat) *and* the custom read/edit routes. The frontend points `useStream` at it
  for chat and `fetch`es the custom routes for schema/graph/corpus/edit/health.
- **Threads = persistence.** Conversation history is the runtime's durable thread
  state; there is no separate chat DB near-term. Ephemeral under `langgraph dev`; durable
  Postgres when self-hosted/deployed. (Durable-local, if wanted, is a checkpointer
  config; see §7.)
- **The engine stays config-driven**: connector (data source), corpus, model, and
  environment are injected, so the same server runs the three profiles (§11).

---

## 4. Repositories

Two repos:

- **`governed-bi` (this repo)** is the engine, the LangGraph app, and custom routes:
  ```
  langgraph.json                 # points the server at the serve-graph factory
  src/governed_bi/api/
    graph_app.py                 # graph factory for the LangGraph runtime (checkpoint-safe state)
    routes.py                    # custom routes (schema/graph/corpus/edit/health) mounted on the server
    stack.py                     # config-driven serve stack (exists; feeds the factory)
    schemas.py                   # pydantic response models (exists; extend for graph/edit)
  src/governed_bi/viz/presenter.py   # UI-agnostic view models (exists; add corpus_graph)
  pyproject: agents (langgraph/langchain), api (custom routes), tracing (langfuse)
  ```
- **`governed-bi-ui` (new)** is the Next.js app (App Router; `@langchain/react`,
  React Flow, shadcn, Tailwind v4). See the handoff doc for its layout.

---

## 5. Backend (what this repo must build)

Current built state (from the earlier phase): `presenter` view models, read
endpoints, the `stack` factory, pydantic schemas, and offline tests. Rework to the
above:

1. **LangGraph app + `langgraph.json`.** A graph-factory the server instantiates
   (deployment deps from `stack`). Chat is served by the runtime; `useStream`
   consumes node updates.
2. **`ServeState` serializability refactor** *(the real work).* Today the graph
   state stashes live objects (the `networkx` graph, the gateway allowlist,
   pydantic `retrieval`/`context`/`generated`). LangGraph Server checkpoints state,
   so persisted state must be serializable. Keep heavy objects out of the
   checkpointed channels (hold them as deployment deps, or rebuild per node), and persist
   only messages plus lightweight results. Must preserve the graph↔`answer_question`
   equivalence (the tests assert it).
3. **Stage labeling.** Map node names → labeled stages (`route`→"Routing",
   `retrieve`→"Retrieving", `generate`→"Generating SQL", `guardrail`→"Checking
   guardrails", `execute`→"Executing", stamp/narrate→"Composing") for a stable UI
   contract. Repair loop = `generate`/`guardrail` re-firing. (Richer per-guardrail
   detail later via LangGraph `stream_mode="custom"`.)
4. **Custom routes** (mounted on the server): `GET /capabilities`, `/health`,
   `/schema`, `/graph` (full knowledge graph), `/corpus/assets`, `/skills`;
   `POST /corpus/edit`. These serialize `presenter` view models (mostly built).
5. **`presenter.corpus_graph()`**: extend beyond tables+joins to a filterable
   knowledge graph over all asset types and their references (tables, columns,
   metrics, terms, joins, rules, few-shots, negatives).
6. **`POST /corpus/edit`**: parse → `validate_corpus` (reject on findings) → in
   `dev`, write YAML via `corpus.serialize.dump_asset`/`write_corpus`, return
   validation and diff; `can_edit` is true only in dev (or an explicit flag). Prod PR
   path is deferred.
7. **Tracing**: Langsmith via env (native to LangGraph); Langfuse via a LangChain
   `CallbackHandler` on the model/graph, behind a new `tracing` extra. Both
   activate only when their keys are set.
8. **Fallback**: keep a non-streaming `POST /chat` (plain `answer_question`) for
   an offline/no-`agents` profile; `/capabilities.can_stream` tells the UI which
   to use.

---

## 6. Frontend

- **Next.js (App Router) + React 19 + TypeScript (strict)**, **Tailwind v4**
  (CSS-first `@theme`), **shadcn/ui**, **React Flow** (knowledge graph), **zod**.
- **Chat** via **`@langchain/react` `useStream`** (`apiUrl` = LangGraph Server,
  `assistantId` = graph name). The hook gives reactive messages, **node/stage
  events** (live progress), thread state (history), and reconnection for free.
- **Route map:** `/` Chat · `/schema` Schema & knowledge graph · `/corpus` Assets
  + skills (+ inline editing when `can_edit`) · `/health` Audit/health.
- Config: `NEXT_PUBLIC_LANGGRAPH_URL` (server), `NEXT_PUBLIC_ASSISTANT_ID`. Non-chat
  reads/edit hit the same origin's custom routes. No secrets in the client.

---

## 7. Persistence (threads)

- Conversation history **is** LangGraph thread/checkpoint state; `useStream`
  loads/rejoins a thread by id. No separate conversation DB near-term.
- **Local (`langgraph dev`) is ephemeral**; durable local, if wanted, is a
  checkpointer config (e.g. a Postgres/SQLite saver in a self-hosted run).
- **Deployed** = Postgres (self-host `langgraph up`, or managed LangGraph Platform).
- A thin app-metadata DB (thread titles, tags) is only needed if the runtime's
  thread metadata proves insufficient; revisit later.

---

## 8. Chat UX

- `useStream` drives the transcript. On submit, render the user turn + an assistant
  turn that shows **live labeled stages** as node events arrive (Route → Retrieve →
  Generate SQL → Guardrails → Execute → Compose; repairs show as re-fires), then
  the final answer.
- **Answer card:** two-axis stamp as two badges (`safety_clearance` +
  `semantic_assurance`; tier chip green/amber/red), the English answer, a
  collapsible **result table**, read-only **SQL**, and a **provenance/audit
  drawer**. Refusals show the escalation but no SQL or number.

---

## 9. Schema & knowledge-graph view

- **Knowledge graph** (React Flow) over `GET /graph`: nodes typed by asset
  (schema/table/column/metric/term/join/rule/few-shot/negative), edges = references;
  **per-type filters/layers** to manage density; low-confidence joins and
  suspect/excluded assets styled distinctly. Click → detail from `GET /schema` /
  `GET /corpus/assets`.
- **Table browser**: columns with types, roles, `suspect`/`excluded` badges,
  sample values, provenance.

> **D15 (Multi-Schema Serving).** D15 adds a schema namespace level (`schema` →
> `table`; `corpus/<schema>/`), so the knowledge graph gains a **schema
> grouping/layer** and curated **cross-schema joins** get a distinct style/filter
> — cross-schema joins are curated-only and Postgres-only per D15.

---

## 10. Editing

UI shows edit affordances only when `capabilities.can_edit`. The backend performs
the change per `Environment`: **dev → write the YAML file** (validate first);
**prod → open a PR** (deferred). The UI never writes files itself: "Git is the
source of truth" holds.

---

## 11. Deployment: three profiles (config only)

| Profile | UI | Runtime | Data | Persistence | Editing |
|---|---|---|---|---|---|
| **local-dev** | `next dev` | `langgraph dev` (local) | SQLite (repo) | ephemeral threads | file-write |
| **public-demo** | Vercel | hosted LangGraph Server | bundled SQLite | Postgres threads | off |
| **internal** | their host | LangGraph Server (self-host / Platform) | Postgres/Redshift | Postgres threads | PR |

---

## 12. Observability, security & cost

- **Tracing:** Langfuse + Langsmith, env-gated (no traces without keys).
- **Secrets:** model key + Langfuse/Langsmith keys on the server; never in the
  client. CORS allow-lists the UI origin.
- **Deferred:** public-demo LLM cost/abuse strategy (budgeted-live, offline, or
  gated); decide before the public deploy.

---

## 13. Open decisions

1. Public-demo model strategy (cost/abuse): deferred.
2. Hosting target for the LangGraph Server (self-host `langgraph up` vs managed
   LangGraph Platform) + whether durable-local threads are wanted in dev.
3. Aesthetic direction (recommend dark-first technical instrument).
4. Public read/chat access (open vs shared token vs rate-limit + bot check).
5. App-metadata DB (only if thread metadata is insufficient).

---

## 14. Build phases

1. **Backend rework** *(this repo, next):* `langgraph.json` + graph factory +
   `ServeState` refactor; custom routes; `presenter.corpus_graph()`;
   `/corpus/edit` (dev); tracing. Keep offline tests green; regenerate OpenAPI for
   the custom routes.
2. **UI shell**: Next.js + Tailwind v4 + shadcn; `useStream` wired to the server;
   `/capabilities` gating.
3. **Chat**: live stages + answer card + provenance drawer (threads = history).
4. **Schema & knowledge graph**: React Flow + detail.
5. **Corpus + health + editing** (dev).
6. **Public deploy**: Vercel UI + hosted LangGraph Server; resolve §13.

---

## Appendix: ADRs

- [0001: Chat via LangGraph Server + `useStream`](adr/0001-langgraph-server-chat-runtime.md)
  (threads = persistence; non-graph endpoints as custom routes).
- Candidates to capture next: config-driven run profiles; dev-file-write editing
  (vs UI-owned writes).
