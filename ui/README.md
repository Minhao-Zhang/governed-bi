# The governed-bi frontend

The web app for **[governed-bi](../README.md)**, a chat app that answers questions about a
database from a semantic layer you bring. The engine is `../src/`; this directory is the client.

**Same repository, and still a pure client.** It holds no BI logic of its own, no
module shared with the engine and no import in either direction — it renders what
the engine serves over HTTP. The spec of record for this client is
[ADR 0007 — the HTTP surface and the UI contract](../docs/adr/0007-http-surface-and-the-ui-contract.md),
and [`docs/usage.md`](../docs/usage.md) is how to run the engine this talks to.

With no backend URL set, the UI draws fixture conversations so it renders with no engine attached.

## What it shows

Three views ([ADR 0021](../docs/adr/0021-views-around-the-chat.md)):

- **Chat**, `/`, and `/?thread=<id>` for one conversation. An answer shows its text, its rows,
  the assumptions behind it, and Details with the SQL and each failed attempt
  ([ADR 0018](../docs/adr/0018-every-answer-states-its-assumptions.md),
  [ADR 0019](../docs/adr/0019-one-plain-chat-page.md)). The [root README](../README.md#an-example)
  has a real answer.
- **Conversations**, `/conversations`: every conversation with how its last turn ended.
- **Semantic layer**, `/layer`: the chosen schema's Ossie model in the embedded ossie-viewer build
  under `public/viewer/`.

## Architecture

The UI is a **pure client** of the engine's **LangGraph Server** (see
[ADR 0001](../docs/adr/0001-langgraph-server-chat-runtime.md)): chat streams over the LangChain
**`useStream`** protocol, and conversations are the server's threads. Besides chat it reads
`/capabilities`, `/conversations`, `/examples`, `/layer` and `/layer/{schema}`, inventoried in
[`docs/openapi.json`](../docs/openapi.json). It owns no database. When no backend URL is
configured, those routes resolve to fixtures and chat uses a synthetic transport.

**Stack:** Next.js 16 (App Router) · React 19 · TypeScript (strict) · Tailwind CSS
v4 · shadcn/ui · TanStack Query · zod · `@langchain/langgraph-sdk`.

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
