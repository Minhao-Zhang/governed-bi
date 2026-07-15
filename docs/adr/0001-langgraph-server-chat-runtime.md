# 0001: Chat serves via LangGraph Server + `useStream`

_[English](0001-langgraph-server-chat-runtime.md) · [简体中文](0001-langgraph-server-chat-runtime.zh.md)_

- **Status:** Accepted (2026-07-10); superseded in part by [ADR 0002](0002-governed-agentic-serve-runtime.md) (2026-07-14) — the runtime choice (LangGraph Server + `useStream`) still stands; the single-node `answer_question` framing was replaced.
- **Deciders:** project owner + design session
- **Related:** [ui-frontend-design.md](../ui-frontend-design.md), [ui-frontend-handoff.md](../ui-frontend-handoff.md)

## Context

The governed-bi UI needs, for the chat surface: **live per-step progress** from
the backend (which governed stage is running, including guardrail/repair events),
**stored conversation history**, and a full-featured, resumable agent UI, while keeping
the answer itself un-streamed (answers are short). The serve path is already a
**LangGraph `StateGraph`** (`server.graph.build_serve_graph`), Answer-equivalent
to the plain `answer_question`.

Two viable ways to deliver live progress + persistence to a Next.js frontend:

1. **LangGraph Server + the LangChain `useStream` SDK.** `useStream` speaks the
   LangGraph Server protocol; the compiled serve graph is exposed via
   `langgraph.json`. This provides node streaming, durable threads/checkpoints,
   interrupts, time-travel, and native LangSmith tracing out of the box.
2. **Custom FastAPI + hand-rolled SSE.** This wraps `graph.stream(stream_mode="updates")`
   in an SSE endpoint; the frontend consumes it manually (not `useStream`), and we
   build thread persistence, reconnection, and state semantics ourselves.

## Decision

Adopt **option 1**: chat is served by a **LangGraph Server** and consumed by the
frontend via **`useStream`**.

- **Threads = persistence.** Conversation history is the runtime's durable thread
  state; there is no separate conversation DB near-term (this supersedes the
  earlier "frontend-owned Neon/Drizzle, stateless API" decision).
- **Non-graph endpoints as custom routes.** `/schema`, `/graph`, `/corpus`,
  `/health`, and `POST /corpus/edit` are mounted as custom routes on the *same*
  LangGraph server, so the frontend has one base URL. The prior standalone
  FastAPI work becomes these routes.
- **Live progress = LangGraph node updates**, mapped at the server to labeled
  stages for a stable UI contract.

## Consequences

**Positive**
- Live node/stage streaming, durable + resumable threads, interrupts (a future
  path to the human gate, D6), and checkpoints/time-travel: the last is a strong
  fit for a *governed, auditable* system.
- Native **LangSmith** tracing; much less bespoke frontend plumbing (the SDK owns
  thread state, streaming, reconnection).
- Reuses the existing LangGraph serve harness rather than a parallel serve path.

**Negative / costs**
- ~~**`ServeState` must become checkpoint-serializable.**~~ **(Resolved by the
  implementation, differently.)** The rework does *not* serialize the multi-node
  `ServeState`. Instead the server graph is a thin **chat** graph whose persisted
  state is only `{messages, answer}` (both JSON-serializable), and the whole
  governed pipeline runs inside one node that calls `answer_question` and streams
  stages via `get_stream_writer()`. The heavy objects (`networkx` graph, allowlist,
  pydantic `retrieval`/`context`) stay node-local and are never checkpointed, so
  `server/flow.py` and `server/graph.py` are untouched and the
  `answer_question`↔graph equivalence still holds.
  _(Forward pointer: this deterministic single-node framing was the runtime at
  the time of this decision; serve has since been cut over to the
  [ADR 0002](0002-governed-agentic-serve-runtime.md) governed agentic core,
  which replaced `answer_question`/`server/flow.py` and the stale
  `server/graph.py` DAG.)_
- **Heavier deployment.** Local `langgraph dev` is easy but **ephemeral**; durable
  persistence needs Postgres (self-host `langgraph up` → Postgres + Redis, or a
  managed LangGraph Platform). A plain FastAPI box would have been lighter for the
  public demo.
- **Runtime/vendor coupling** to LangGraph Platform conventions.
- Supersedes "stateless API + frontend-owned persistence"; the offline/no-`agents`
  profile keeps a non-streaming `/chat` fallback.

## Alternatives considered

- **Custom FastAPI + hand-rolled SSE** (rejected): re-implements threads,
  persistence, reconnection, and streaming semantics, and forgoes the official
  SDK, interrupts, and time-travel. Lighter to deploy, but more bespoke UI/runtime
  code and a weaker agent-UI story.
