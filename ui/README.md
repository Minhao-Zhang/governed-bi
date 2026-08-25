# The governed-bi frontend

The web frontend for **[governed-bi](../README.md)** — an agentic BI /
Generative-BI engine that turns natural-language questions into **grounded,
governed, auditable** answers over relational data. The engine is `../src/`; this
directory is the client.

**Same repository, and still a pure client.** It holds no BI logic of its own, no
module shared with the engine and no import in either direction — it renders what
the engine serves over HTTP: the governed answer with what the engine observed
about it, the semantic layer (schema, relationships, corpus), and the per-answer
audit trail. Being in one tree does not make the boundary softer; it makes the
spec easier to open. Read the engine's docs first. The spec of record for this
client is
[ADR 0007 — the HTTP surface and the UI contract](../docs/adr/0007-http-surface-and-the-ui-contract.md);
[ADR 0009](../docs/adr/0009-browsing-and-filtering-api.md) enumerates the read
routes, and [`docs/usage.md`](../docs/usage.md) is how to run the engine this
talks to.

> **Status:** early, and honest about it — matching the engine's own maturity.
> By default the UI runs on neutral **mock data** so every surface renders with no
> backend attached; point it at a running engine to see live, governed answers.

## What it shows

- **Chat** — ask a question, watch the governed pipeline live (guard → the five
  retrieval facets → route → assemble → the agent loop → check → execute →
  narrate; the engine's `register/stages.py` is the vocabulary), and get an answer
  card. The card reports only what the engine **observed**: the `outcome`
  (`answered` / `refused` / `clarification` / `capped` / `crashed` / `no_sql`), the attempt
  ledger's terminal state and how many statements passed or were blocked by
  governance, the answer text, read-only (highlighted) SQL, and a provenance/audit
  drawer. There is no reliability tier and no `safety_clearance` /
  `semantic_assurance` stamp: the engine produces none, and a client that
  synthesized one would be putting a trust claim with nothing behind it on the most
  prominent badge in the interface (ADR 0007 §3). Refusals show the engine's own
  copy, never a fabricated number.
- **Schema** — the semantic layer, three ways:
  - **Relationships** — a column-level ER diagram (tables, columns, FK edges with
    cardinality).
  - **Semantic graph** — the full corpus as a typed, filterable knowledge graph
    (metrics, terms, joins, few-shots, negatives).
  - **Tables** — a plain, auditable table/column browser with governance flags.
- **Corpus** — every asset the engine loaded, two ways: by type (server-filtered,
  sorted and paginated by `GET /corpus/rows`) or by ranked search, with provenance and
  exclusion state. The ranking is a **client-side** Fuse index over a catalog assembled in the
  browser (`lib/asset-catalog.ts`), not a server query — the engine reports
  `can_search: false` and `GET /search` was deliberately never built, so this is the only
  ranking there is. Corpus health — servable, fatal problems, degradations — is the
  header control, from `GET /audit/corpus`.
- **History** — every conversation on the server; a row leads back into the chat.
- **Audit** — every turn the server has served, and one turn's record stage by
  stage: the governance ledger, the licensed set, and which required register
  fields are absent.
- **Pending** — clarifications the engine asked that nobody answered.
- **Review** — the data steward's half of the return path (ADR 0015): the queue of observations
  readers filed against served turns, weakly clustered; one row's evidence bundle beside the
  statement and the reference answer; and a corpus edit drafted against `{summary, body}` only,
  checked by the same validator the loader uses, then handed off as a diff. No string on this
  surface says a landed change fixed anything — `ui/lib/review-copy.ts` holds every one of them in
  a single module so that discipline is readable as a set.
- **Settings** — client display preferences.

Governance outcome is the one "loud" color channel (green/amber/red = what the
engine observed, not a score it assigned); everything else stays neutral, so color
always means *an observation about this turn*.

## Architecture

The UI is a **pure client** of the engine's **LangGraph Server** (see
[ADR 0001](../docs/adr/0001-langgraph-server-chat-runtime.md)):
chat streams over the LangChain **`useStream`** protocol; schema, corpus and the audit
surface are **custom REST routes** on that same server (seventeen reads and five writes,
inventoried in [`docs/openapi.json`](../docs/openapi.json) — three of the writes and one of the
reads are the steward's four verbs, unmounted unless `GOVERNED_BI_FEEDBACK_ADMIN` is set, and 404
rather than 403 when they are, because a 403 confirms the route exists); the UI adapts its
affordances to
`GET /capabilities`. It owns no database — conversation state is the runtime's
thread state. When no backend URL is configured, all reads resolve to mock
fixtures and chat uses a synthetic transport, so the app is fully explorable
offline.

**Stack:** Next.js 16 (App Router) · React 19 · TypeScript (strict) · Tailwind CSS
v4 · shadcn/ui · `@xyflow/react` + dagre (graphs) · TanStack Query · zod ·
`@langchain/langgraph-sdk` / `@langchain/react`.

## Getting started

```bash
npm install
npm run dev
```

Both need this directory as the working directory. From the repository root, use
`npm --prefix ui ci` and `npm --prefix ui run dev` instead. Note `ci`, not
`install`: `npm install` reads `package.json` from the working directory and
`--prefix` only moves where the tree is written, so `npm --prefix ui install`
exits ENOENT looking for a root `package.json`. `ci` and `run` resolve from the
prefix, which is how `../.claude/launch.json`'s `ui` entry starts the dev server.

Open [http://localhost:3000](http://localhost:3000) — it runs in **mock mode**
(neutral placeholder data) out of the box.

### Wire it to a live engine

1. Run the engine's LangGraph Server, from the repository root:

   ```bash
   uv run langgraph dev   # serves http://localhost:2024
   ```

   Set `OPENAI_API_KEY` for live NL answers. No engine route requires a credential
   from this client, so there is nothing to keep in step between the two processes.
   CORS already allows `http://localhost:3000`.

2. Copy [`.env.example`](.env.example) to `.env.local` (git-ignored):

   ```
   NEXT_PUBLIC_LANGGRAPH_URL=http://localhost:2024
   NEXT_PUBLIC_ASSISTANT_ID=serve
   ```

   The two files are not merged by sharing a repository: Next.js inlines a
   `NEXT_PUBLIC_` variable into the bundle at build time and never reads `../.env`.

3. Restart `npm run dev`. Leave the URL empty (or delete `.env.local`) to return
   to mock mode.

## Deployment

Config-driven, no code change: the UI deploys to Vercel and points
`NEXT_PUBLIC_LANGGRAPH_URL` at a hosted LangGraph Server.

There is no SQLite deployment. The engine's served surface builds a `PostgresConnector`
unconditionally (`api/graph_app.py`) and needs a live Postgres plus a curated semantic
layer.

## License

MIT © 2026 Minhao Zhang, under the repository's [LICENSE](../LICENSE).
