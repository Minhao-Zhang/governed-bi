"""``agent_core`` — create_agent loop + tools (ADR 0005 §3.1)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    wrap_model_call,
)
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from governed_bi.serve.agent_state import GovernedAgentState
from governed_bi.serve.delivery import DeliveryTracker
from governed_bi.serve.runtime import configurable
from governed_bi.serve.state import TERMINAL_PATH_KINDS
from governed_bi.serve.tools import (
    SYSTEM_PROMPT,
    build_tools,
    execution_from_attempts,
)
from governed_bi.serve.usage import NO_TOKEN_USAGE, usage_row

__all__ = ["agent_core_node", "STUB_ANSWER", "NO_TOKEN_USAGE"]

STUB_ANSWER = "STUB_ANSWER"

# ``NO_TOKEN_USAGE`` is re-exported from ``serve/usage.py``, where the row builder now lives.
# Kept on this module's ``__all__`` because it is what tests assert against and this used to be
# its home; defining it twice would be two strings that must stay equal for no reason.


def agent_core_node(state: dict, config: RunnableConfig) -> dict:
    """Run the main model + tools, or the F1 stub when no ``agent_model`` is set.

    **No ``checkpointer`` parameter.** There was one, passed down from ``build_graph`` and
    into ``create_agent``, under comments in three files claiming the nested agent needed a
    saver of its own and that "two savers is worse than none". All three were wrong, and a
    probe settles it: inside a node, ``CONFIG_KEY_CHECKPOINTER`` is the **outer** saver, the
    agent's own saver ends the run with **zero** checkpoints, and the outer one has three.
    LangGraph propagates the checkpointer through ``config`` to a graph invoked inside a node
    and namespaces it, so the nested agent has always been checkpointed — by the graph's
    saver, which is why ``ask_user`` resume worked at all. The parameter was dead code
    documented as load-bearing.
    """
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    cfg = configurable(config)
    model = cfg.get("agent_model")
    if model is None:
        return _stub(state)

    tools = build_tools(state, config)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        # The ledger channels the tools write. Without the schema LangGraph drops updates
        # naming keys the graph does not declare, silently — see ``serve/agent_state.py``.
        state_schema=GovernedAgentState,
        # The delivered context, injected at model-call time rather than put in ``messages``.
        # See :func:`_context_middleware` for the leak that forced the move.
        middleware=_context_middleware(state),
    )

    # ``messages`` is the conversation and nothing else. ``assemble`` used to append the
    # context block to it as a human turn, and that one line cost three things: the whole
    # context block of every prior turn was re-sent to the provider on every later turn; the
    # human-message count came out at 2n-1, so ``turn_index`` was wrong for turn 2 onward on
    # both the server and REST paths; and ``messages`` stopped being the conversation and
    # became the conversation plus its scaffolding. The block is already recorded, hashed, in
    # ``delivery``.
    history = list(state.get("messages") or [])
    question = _question_message(state, history)
    inbound = history + ([question] if question is not None else [])

    result = agent.invoke({"messages": inbound}, config)
    out_messages = list(result.get("messages") or [])
    fresh = out_messages[len(inbound) :]

    # The ledger, read from the agent's own checkpointed channels rather than from closures
    # on the tool objects. Ordered by insertion, which is chronological (``merge_by_call``
    # keeps the accumulated map first).
    attempts = list((result.get("attempts_by_call") or {}).values())
    clarifications = list((result.get("clarifications_by_call") or {}).values())
    delivered = dict(result.get("tool_delivered") or {})

    generated_sql = _last_executed_sql(attempts) or _last_run_query_sql(out_messages)
    delivery = DeliveryTracker(delivered).merge_into(state.get("delivery"))
    usage = [usage_row(stage="agent_core", model=model, messages=fresh,
                       turn_index=state.get("turn_index", 1))]

    update: dict[str, Any] = {
        "path_kind": "answered",
        "messages": fresh,
        "usage": usage,
        "delivery": delivery,
        "clarification_requested": False,
        "execution": execution_from_attempts(attempts),
    }
    if clarifications:
        update["clarifications"] = clarifications
    if generated_sql:
        update["generated_sql"] = generated_sql
    # Lifted out of the nested agent's channel, like the ledger above it. The table is what the
    # answer was missing: the agent narrates on its own, but its rows only ever existed inside a
    # `ToolMessage`'s JSON, so a client could show the explanation and not the data.
    result_table = result.get("result_table")
    if result_table:
        update["result_table"] = result_table
    return update


def _question_message(state: dict, history: list[Any]) -> HumanMessage | None:
    """The turn's question as a human turn, or ``None`` if the history already carries it.

    On the server path the client's own human message *is* the question, so restating it would
    send it twice; on the CLI and eval paths ``Session.turn`` seeds an empty ``messages``, so
    this is the only place the question appears at all.

    **It used to carry the context block too, concatenated ahead of the question**, which is
    why the question is now visibly its own message on the CLI and eval paths instead of being
    buried inside 8 KB of delivered context. The block moved to :func:`_context_middleware`, so
    on the server path — where this returns ``None`` — the first model call is byte-identical to
    what it was; :func:`_context_middleware` records how later calls in a turn differ.
    """
    question = str(state.get("question") or "").strip()
    if not question:
        return None
    asked = any(
        str(getattr(m, "type", "")) == "human"
        and str(getattr(m, "content", "")).strip() == question
        for m in history
    )
    return None if asked else HumanMessage(content=f"Question: {question}")


def _context_middleware(state: dict) -> list[AgentMiddleware]:
    """Deliver ``delivery.context_block`` on every model call, without it entering ``messages``.

    Empty list when the turn rendered no block, so a turn with nothing to deliver builds
    exactly the agent it built before.

    **The block used to be a ``HumanMessage`` in the agent's inbound ``messages``, and it
    rendered in the live chat as the user's own bubble.** LangGraph streams a nested graph's
    whole state under ``values|agent_core:<task_id>``, and the JS SDK applies the values of any
    namespace it does not recognise as a subagent straight onto *root* state
    (``@langchain/langgraph-sdk`` ``dist/ui/manager.js:413``; the test for "subagent" is a
    ``tools:`` segment, and ``agent_core:<task_id>`` has none). So mid-run ``stream.messages``
    became the nested agent's list, whose index 1 was this block — 8.6 KB shown as a user
    message for the duration of every turn, disappearing at the end when the next root frame
    clobbered it back. Measured in one capture: 4–10 such frames per turn, ~60 KB. No SDK
    option suppresses it, and ``stream_subgraphs`` cannot be turned off because the timeline
    and the token stream both depend on it (ADR 0010 M2).

    Injecting per model call removes the class of bug rather than this instance, and it is the
    only version under which "the context is never persisted to the conversation" is true at
    *every* level: ``fresh = out_messages[len(inbound):]`` keeps the block out of the outer
    channel — and still must, since it is what drops the replayed history — but the nested
    agent's own checkpoint is written by the graph's saver (see :func:`agent_core_node`) and
    held the block regardless.

    Not appended to ``system_prompt`` instead: ``prompt_set_hash`` is a ``Role.comparability``
    field digesting the prompt registry, so per-turn text on the analyst's system message would
    make the published hash describe something that was never sent
    (``tests/serve/test_model_inputs.py`` asserts the delivered system prompt is exactly
    ``prompt_text("analyst")``).

    The block lands **last**, after any tool results, rather than at its old position ahead of
    them. Consecutive user messages are legal on the one provider family this engine talks to
    (``langchain-openai``; nothing here imports an Anthropic client), and last is where the
    turn's governing constraints are most likely to be honoured.
    """
    block = str((state.get("delivery") or {}).get("context_block") or "")
    if not block:
        return []

    @wrap_model_call
    def deliver_context(
        request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(request.override(messages=[*request.messages, HumanMessage(block)]))

    return [deliver_context]


def _stub(state: dict) -> dict:
    return {
        "path_kind": "answered",
        "messages": [{"role": "assistant", "content": STUB_ANSWER}],
        "usage": [
            {
                "turn_index": state.get("turn_index", 1),
                "stage": "agent_core",
                "model": "stub",
                "input_tokens": 0,
                "output_tokens": 0,
            }
        ],
        "clarification_requested": False,
    }


def _last_executed_sql(attempts: Any) -> str | None:
    """The last statement the engine actually **sent**, from the ledger.

    Preferred over the model's ``run_query`` argument, which is what ``generated_sql`` used
    to hold — so a turn that succeeded reported a statement the database never saw.
    ``canonicalise`` rewrites identifiers to the corpus's declared spelling and quotes them
    (ADR 0008 D2) and ``apply_row_limit`` appends the cap, so the two strings differ on
    every mixed-case identifier in the lake. The ledger hashed the executed one, so the
    record carried the hash of one statement beside the text of another — and an eval that
    re-executes ``generated_sql`` fails on exactly those, understating EX.

    Falls back to the tool argument when nothing executed: a refused attempt still produced
    SQL, and "the model wrote this and it was refused" is worth recording.
    """
    last: str | None = None
    for attempt in attempts or ():
        if not isinstance(attempt, Mapping):
            continue
        sql = attempt.get("executed_sql")
        if sql:
            last = str(sql)
    return last


def _last_run_query_sql(messages: list[Any]) -> str | None:
    last: str | None = None
    for m in messages:
        if not isinstance(m, AIMessage):
            continue
        for tc in m.tool_calls or ():
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name != "run_query":
                continue
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            sql = (args or {}).get("sql")
            if sql:
                last = str(sql)
    return last
