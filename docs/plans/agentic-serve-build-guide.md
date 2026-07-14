# Build guide: governed agentic serve runtime

_Step-by-step implementation guide for [ADR 0002](../adr/0002-governed-agentic-serve-runtime.md).
Companion to the [impact map](agentic-serve-rework-plan.md) (which lists *what* is
touched); this is *how*, in order, with proven code skeletons and a
definition-of-done per step._

## Audience & honest scope

- **P0 and P1 are junior-ready from this doc** — concrete steps, verified
  skeletons, tests, and DoD. A junior can build them with normal PR review.
- **P2 (cutover: deletions + require-key) and P3 (HITL, durable audit) are
  senior-led.** They remove fallbacks and touch deployment; entry criteria are
  listed but not spelled out step-by-step here.

**Prerequisites:** read ADR 0002 §Decision + §Governance invariants; skim
`server/flow.py` (the logic being ported) and `gateway/guardrails.py` (`check`,
`column_allowlist` — reused unchanged). Stack is pinned: `langchain 1.3.12`,
`langgraph 1.2.8`.

## The proven mechanism (north-star pattern)

Three facts were verified end-to-end against the installed stack (spike,
2026-07-13) — **build to these, they are not hypothetical:**

1. `wrap_tool_call(request, handler)` can **read** `request.state[...]` and
   **write** custom state channels by returning `Command(update={...})`; it can
   **short-circuit** by returning a `ToolMessage` without calling `handler`.
2. A `@tool` can **grow shared per-turn state** by returning
   `Command(update={"licensed": [...]})`; a *later* tool call's middleware sees it
   via `request.state["licensed"]`.
3. Raising a custom exception inside `wrap_tool_call` **propagates out of
   `agent.invoke()`**, so the outer graph can catch it and route to a hard refuse
   (the L2 mechanism).

**Hard constraint that follows:** tool calls must run **sequentially** — custom
state commits *between* calls, not within a parallel batch. Force this when
building the agent (see Step P1.4). Otherwise `run_query` could execute before
`inspect_schema`'s licensing lands.

## State design

The agent runs on `AgentState` extended with two governed channels (reducers so
appends accumulate — the langgraph list-reducer rule):

```python
import operator
from typing import Annotated
from langchain.agents.middleware import AgentState

class GovState(AgentState):
    licensed: Annotated[list, operator.add]   # table-asset ids licensed this turn (Inv #4)
    ledger:   Annotated[list, operator.add]    # one record per governed action (Inv #10)
```

The outer chat graph state stays `{messages, answer}` (ADR 0001) — **`GovState`
is the agent subgraph's internal state and never enters the checkpointed chat
transcript** (see Gotcha G2).

---

## Phase P0 — groundwork (no behavior change)

### P0.1 Extract a shared governance module
**Goal:** one place both the (current) flow and the (future) middleware call, so
governance can't drift (ADR Q4).
**Do:** create `server/governance.py`; move from `flow.py` (keep thin re-exports
so nothing breaks yet): `_match_negative_example`, `_licensed_table_ids`,
`_finalize_success`, `_finish_unsuccessful`, `_answer_text`, `_result_table`,
`_suspect_in_scope`, the escalation blobs, `_HARD_REFUSE_LAYERS`,
`_NON_REPAIRABLE_LAYERS`.
**DoD:** full suite green; `flow.py` imports them from `server/governance.py`; no
call-site outside `server/` changes.

### P0.2 Fake-model agent harness
**Goal:** run an agent loop deterministically in CI (no key).
**Do:** add `llm/fake.py`:
```python
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

class FakeToolModel(FakeMessagesListChatModel):
    """Scripted chat model that tolerates create_agent's bind_tools call.
    Feed it a list of AIMessage turns (with .tool_calls) to script a trajectory."""
    def bind_tools(self, tools, **kwargs):
        return self
```
**DoD:** a smoke test builds `create_agent(model=FakeToolModel(responses=[...]))`
and invokes it offline.

### P0.3 Governance-invariant test contract
**Goal:** replace "same Answer" equivalence with "same governance invariants".
**Do:** add `tests/test_governance_invariants.py` asserting, for a fixed corpus:
negative-example questions refuse; an L2 SQL is blocked; `safety_clearance`
stamping matches `flow.py` today. (These are model-independent, so they run under
the fake model.)
**DoD:** passes against the current flow (it's the baseline the agent path must
also satisfy in P1).

---

## Phase P1 — the agent core (behind a flag)

### P1.1 Governed tools — `server/tools.py`
Factory closes over the deployment deps; tools are read-only and honor
`governance.excluded`. Reuse `retrieve`, `select_schema`,
`filter_corpus_for_retrieval`, `assemble_context`, `Gateway.execute`.

```python
from typing import Annotated
from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.types import Command

def make_tools(corpus, gateway, identity, *, embedder=None, multi_schema=False):
    @tool
    def search_corpus(query: str) -> str:
        """Find tables/terms/joins/metrics relevant to the question (read-only)."""
        r = retrieve(corpus, query, embedder=embedder)         # reuse
        # NOTE: filter excluded assets here (governance.excluded) before returning.
        return render_retrieval(r)                             # small helper you write

    @tool
    def inspect_schema(table_id: str,
                       tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        """Show a licensed/queryable table's columns+types. LICENSES it for this turn."""
        asset = corpus.by_id(table_id)
        if asset is None or getattr(asset, "governance", None) and asset.governance.excluded:
            return Command(update={"messages": [ToolMessage(
                content=f"{table_id}: not available", tool_call_id=tool_call_id)]})
        return Command(update={
            "licensed": [table_id],                            # Inv #4 — grow scope
            "messages": [ToolMessage(content=render_columns(asset),
                                     tool_call_id=tool_call_id)],
        })

    @tool
    def sample_rows(table_id: str, n: int = 5) -> str:
        """Preview rows of a licensed table (runs as the acting identity → RLS)."""
        # guard: table_id must already be in licensed; run bounded SELECT via gateway.
        ...

    @tool
    def run_query(sql: str) -> str:
        """Execute a read-only SELECT. Guardrailed + audited by middleware."""
        result = gateway.execute(sql, identity)                # reached only if guardrail passed
        return render_result(result)

    return [search_corpus, inspect_schema, sample_rows, run_query]
```

### P1.2 Governance middleware — implement `server/middleware.py`
Fill the existing stub against `AgentMiddleware`. This is the enforcement +
audit choke point (Inv #2, #3, #10). Reuse `check` and `column_allowlist`
**unchanged**.

```python
import operator
from typing import Annotated
import sqlglot
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from ..gateway import check, column_allowlist, GuardrailLayer

RUN_QUERY_CAP = 3                      # ADR Q6
_HARD = {GuardrailLayer.policy_blacklist}

class GovState(AgentState):
    licensed: Annotated[list, operator.add]
    ledger:   Annotated[list, operator.add]

class GovernanceHardStop(Exception):    # L2 — propagates out of agent.invoke (proven)
    def __init__(self, entry): self.entry = entry

class GovernanceMiddleware(AgentMiddleware):
    state_schema = GovState
    def __init__(self, corpus, *, dialect, multi_schema, default_schema, settings):
        super().__init__()
        self._allowlist = column_allowlist(corpus, multi_schema=multi_schema)
        self._dialect, self._multi, self._default = dialect, multi_schema, default_schema
        self._settings = settings

    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] != "run_query":
            return handler(request)                            # exploration passes through
        tcid = request.tool_call["id"]
        raw = request.tool_call["args"]["sql"]
        # 1) normalize (fixes the RA quoting bug deterministically)
        try:
            sql = sqlglot.transpile(raw, read=self._dialect, write=self._dialect,
                                    identify=True)[0]
        except Exception:
            sql = raw
        # 2) attempt cap (count prior run_query records in the ledger)
        prior = sum(1 for e in (request.state.get("ledger") or [])
                    if e.get("action") == "run_query")
        if prior >= RUN_QUERY_CAP:
            return Command(update={"messages": [ToolMessage(
                content="attempt cap reached", tool_call_id=tcid)],
                "ledger": [{"action": "run_query", "verdict": "cap", "sql": sql}]})
        # 3) guardrail over the CURRENT licensed set (Inv #4) — reuse check() unchanged
        licensed = set(request.state.get("licensed") or [])
        verdict = check(sql, allowed_columns=set(self._allowlist.allowed),
                        suspect_columns=self._allowlist.suspect,
                        allowed_tables=licensed,
                        hard_block_suspect=self._settings.hard_block_suspect_columns,
                        dialect=self._dialect, multi_schema=self._multi,
                        default_schema=self._default)
        if not verdict.passed:
            entry = {"action": "run_query", "verdict": "block",
                     "layer": verdict.failed_layer.value if verdict.failed_layer else None,
                     "reason": verdict.reason, "sql": sql, "allowed": sorted(licensed)}
            if verdict.failed_layer in _HARD:                  # L2 → hard stop (Inv #3)
                raise GovernanceHardStop(entry)
            return Command(update={"messages": [ToolMessage(       # repairable → coach
                content=f"BLOCKED ({entry['layer']}): {verdict.reason}", tool_call_id=tcid)],
                "ledger": [entry]})
        # 4) PASS — execute via handler, then audit (Inv #10)
        out = handler(request)
        return Command(update={"messages": [out], "ledger": [
            {"action": "run_query", "verdict": "pass", "sql": sql,
             "allowed": sorted(licensed)}]})

    def wrap_model_call(self, request, handler):
        # Inv #2 identity tool-scoping: filter request.tools by identity here if needed.
        return handler(request)
```

### P1.3 System prompt + tool descriptions
The prompt is where SQL quality lives — treat it as code. Draft (iterate against
eval):
> You answer questions over a governed data warehouse by writing **one read-only
> SELECT**. You cannot see a table until you `inspect_schema` it. Workflow:
> `search_corpus` to find relevant tables → `inspect_schema` each candidate to see
> exact columns/types → write SQL using **only** identifiers you have inspected →
> `run_query`. If `run_query` returns BLOCKED or an error, read it, fix the SQL,
> and try again (max 3). Never guess a column or table name. Call tools **one at a
> time**.

Tool descriptions must state read-only + "inspect before you query" (the
docstrings in P1.1 are the source of truth the model sees).

### P1.4 Assemble the agent core — `server/agent.py`
```python
from langchain.agents import create_agent

def build_agent_core(corpus, gateway, identity, model, *, settings,
                     dialect, multi_schema, default_schema, embedder=None):
    tools = make_tools(corpus, gateway, identity, embedder=embedder,
                       multi_schema=multi_schema)
    mw = GovernanceMiddleware(corpus, dialect=dialect, multi_schema=multi_schema,
                              default_schema=default_schema, settings=settings)
    return create_agent(model=model, tools=tools, middleware=[mw],
                        system_prompt=SYSTEM_PROMPT)
    # Force sequential tool calls (constraint): set parallel_tool_calls=False on the
    # model (e.g. ChatOpenAI(..., parallel_tool_calls=False)) OR bind it in wrap_model_call.
```

### P1.5 Outer rails — the serve StateGraph
Thin deterministic wrapper. Reuse `route_intent`, `bind_terms`,
`_match_negative_example` (refuse-gate, runs **before** the agent — Inv #1),
`_try_cache_hit`, and `_finalize_success` / `_finish_unsuccessful` from
`server/governance.py`.

```python
# nodes: ingest -> refuse_gate -> prepare -> cache -> agent_core -> finalize | refuse
def agent_core_node(state):
    try:
        final = agent.invoke({"messages": [HumanMessage(state["question"])],
                              "licensed": [], "ledger": []})
    except GovernanceHardStop as e:                 # L2 (proven to propagate)
        return {"answer": refusal(escalation=..., provenance={**base, **e.entry})}
    sql, tables_used = extract_final_sql(final)     # from the last passing ledger entry
    result = gateway.execute(sql, identity)         # or reuse the ledger's executed result
    generated = GeneratedSql(sql=sql, tables_used=frozenset(tables_used), metric_id=None)
    return {"answer": _finalize_success(question=state["question"], generated=generated,
            result=result, attempts=..., ledger=final["ledger"], ...)}
```
On exhaustion / no passing SQL → `_finish_unsuccessful` (§6 graded-delivery /
refuse) — unchanged behavior. Attach `final["ledger"]` to the answer provenance
(Inv #10, Q3-a).

### P1.6 Wire behind a flag
In `stack.py`, when a key is present *and* `settings.runtime.agent_serve` is on,
build `build_agent_core` and route the serve node to the new outer graph; else the
existing flow. **Do not delete anything yet** — that's P2.
**DoD (P1):** the agent path answers a known BIRD question end-to-end under a real
key; the invariant tests (P0.3) pass on the agent path under the fake model; the
ledger is populated on the `Answer`.

---

## Test scaffolding (copy this pattern)

```python
# script a trajectory: inspect then query then answer
from langchain_core.messages import AIMessage
turns = [
    AIMessage(content="", tool_calls=[{"name": "inspect_schema",
        "args": {"table_id": "RA"}, "id": "c1", "type": "tool_call"}]),
    AIMessage(content="", tool_calls=[{"name": "run_query",
        "args": {"sql": "SELECT count(*) FROM RA"}, "id": "c2", "type": "tool_call"}]),
    AIMessage(content="42"),
]
agent = build_agent_core(corpus, gateway, identity, FakeToolModel(responses=turns), ...)
final = agent.invoke({"messages": [], "licensed": [], "ledger": []})
assert "RA" in final["licensed"]                       # Inv #4
assert final["ledger"][-1]["verdict"] == "pass"        # Inv #10
```
Tests to write: `test_middleware_guardrail.py` (pass/block/L2-hardstop/cap),
`test_tool_scoping.py` (excluded never surfaces; licensing grows only via tools),
`test_governance_ledger.py` (a record per governed action, incl. deny).

## Gotchas (each cost real time if missed)

- **G1 Sequential tool calls.** Set `parallel_tool_calls=False`. Proven
  constraint — parallel calls race the licensed-set update.
- **G2 Chat-state pollution.** The agent's internal `messages`/`licensed`/`ledger`
  must NOT be merged into the outer `{messages, answer}` chat transcript or the
  checkpoint. Keep the agent invocation node-local (ADR 0001); append only the
  final clean `AIMessage` + the `answer` view to the outer state.
- **G3 `_finalize_success` needs `tables_used`.** The agent doesn't hand you a
  `GeneratedSql`. Reconstruct `tables_used` from the licensed set actually
  referenced by the final SQL (parse with sqlglot) or from the last passing
  ledger entry.
- **G4 Reducers required** on `licensed`/`ledger` — without `operator.add` the last
  write wins and the ledger loses history.
- **G5 Excluded filter in every tool** — `search_corpus` AND `inspect_schema` must
  drop `governance.excluded` assets, or dynamic licensing leaks them (Inv #2/#4).

## Acceptance checklist

**P0 done:** governance module extracted, suite green, fake-model harness runs,
invariant tests pass on the current flow.
**P1 done:** agent answers a BIRD question under a real key; invariant tests pass
on the agent path under the fake model; guardrail blocks an off-scope table and
records it; L2 SQL hard-stops; ledger rides on the `Answer`; flagged off by
default.
**P2 (senior):** entry = P1 A/B on BIRD acceptable. Then delete
`TemplateSqlGenerator`/monolith/`graph.py`, require key, flip eval arms, docs.
**P3 (senior):** HITL (`interrupt()` + durable checkpointer), durable audit sink,
egress governance.
