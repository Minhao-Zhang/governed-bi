# SPIKE-1 + SPIKE-2 verdicts (M2 entry gate)

_Recorded 2026-07-22. Gate for ADR 0004 L2/L3/L4._

## SPIKE-1: token state-write mechanism — **PASS**

**Question:** Does `_coerce_single_tool_call` drop `usage_metadata`, and can `after_model` land usage on a reducer channel?

**Findings:**
1. Pre-fix coercion rebuilds `AIMessage` from only `content` / `tool_calls[:1]` / `id` / `additional_kwargs`, dropping `usage_metadata` and `response_metadata` on any 2+ tool-call turn.
2. Installed LangChain exposes `AgentMiddleware.after_model(self, state, runtime) -> dict | None`.
3. Mechanism adopted: preserve `usage_metadata` + `response_metadata` through coercion; `after_model` returns `{"token_usage": [...]}` onto `GovState.token_usage: Annotated[list, operator.add]`.

**Regression lock:** `tests/test_token_capture.py` (L4).

## SPIKE-2: durable vs ephemeral persistence — **PASS (attach on standalone)**

**Question:** Does `langgraph dev` persist across restart? Where must L3 attach a durable saver?

**Findings:**
1. [`langgraph.json`](../../langgraph.json) + `langgraph-cli[inmem]` → `langgraph dev` injects an **ephemeral** in-memory saver (lost on restart). Expected: **no** cross-restart persistence in local CLI.
2. LangGraph Server / deploy targets inject durable Postgres at runtime when configured for that platform.
3. [`build_chat_graph`](../../src/governed_bi/api/graph_app.py) compiles checkpointer-less by default; [`make_graph`](../../src/governed_bi/api/graph_app.py) must stay bare so it does not collide with server injection.
4. Decision: L3 attaches the durable `conversation_checkpointer` on the **standalone** path (`build_chat_graph(stack, checkpointer=...)` / tests / local callers). `clarify_checkpointer` remains `InMemorySaver` until F7.
