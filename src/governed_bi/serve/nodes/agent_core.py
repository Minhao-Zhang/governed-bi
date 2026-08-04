"""``agent_core`` — create_agent loop + tools (ADR 0005 §3.1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from governed_bi.register.quantity import Measured
from governed_bi.serve.agent_state import GovernedAgentState
from governed_bi.serve.delivery import DeliveryTracker
from governed_bi.serve.runtime import configurable, model_id
from governed_bi.serve.state import TERMINAL_PATH_KINDS
from governed_bi.serve.tools import (
    SYSTEM_PROMPT,
    build_tools,
    execution_from_attempts,
)

__all__ = ["agent_core_node", "STUB_ANSWER", "NO_TOKEN_USAGE"]

STUB_ANSWER = "STUB_ANSWER"

#: Why a usage row carries no token count. :meth:`Measured.unmeasured` requires a reason,
#: and this one has to reach the artifact: "not measured" with no explanation is
#: indistinguishable from a forgotten assignment.
NO_TOKEN_USAGE = "the provider returned no usage_metadata carrying both token counts"


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
    )

    # The delivered context is passed **into** the agent and never persisted to the
    # conversation. ``assemble`` used to append it to ``messages`` as a human turn, and that
    # one line cost three things: the whole context block of every prior turn was re-sent to
    # the provider on every later turn; the human-message count came out at 2n-1, so
    # ``turn_index`` was wrong for turn 2 onward on both the server and REST paths; and
    # ``messages`` stopped being the conversation and became the conversation plus its
    # scaffolding. The block is already recorded, hashed, in ``delivery``.
    history = list(state.get("messages") or [])
    context = _context_message(state, history)
    inbound = history + ([context] if context is not None else [])

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
    usage = [_usage_row(model, fresh, state.get("turn_index", 1))]

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


def _context_message(state: dict, history: list[Any]) -> HumanMessage | None:
    """The turn's delivered context, as one ephemeral message.

    The question is appended only when the history does not already carry it. On the server
    path the client's own human message *is* the question, so restating it inside the context
    block would send it twice; on the CLI path ``turn()`` starts with an empty ``messages``,
    so this message is the only place the question appears at all.
    """
    block = str((state.get("delivery") or {}).get("context_block") or "")
    question = str(state.get("question") or "").strip()
    asked = any(
        str(getattr(m, "type", "")) == "human"
        and str(getattr(m, "content", "")).strip() == question
        for m in history
    )
    parts = [p for p in (block, "" if asked or not question else f"Question: {question}") if p]
    return HumanMessage(content="\n\n".join(parts)) if parts else None


def _reported_tokens(messages: list[Any]) -> dict[str, int] | None:
    """This turn's provider-reported token counts, or ``None`` if none were reported.

    LangChain puts them on ``AIMessage.usage_metadata``; the agent loop can make several
    calls, so the row is the turn's total. A payload that does not carry **both** counts
    as integers is not a measurement, and reporting the part it did carry beside a zero
    for the rest would be the defect this function exists to remove.

    Cache counts are included only when the provider reported them: ``measure/price.py``
    reads an absent ``cache_read_tokens`` as nothing cached, which its docstring justifies
    from the artifacts, while a zero written here would be this code's claim rather than
    the provider's.
    """
    total = {"input_tokens": 0, "output_tokens": 0}
    #: Only the cache keys a provider actually reported. It was a two-key dict initialised to
    #: zero and emitted whole as soon as **either** key appeared, so a provider reporting a
    #: cache read also produced ``cache_write_tokens: 0`` — this code's claim wearing the
    #: provider's clothes. ``price.py`` reads an *absent* key as nothing cached, which its
    #: docstring justifies from the artifacts; a written zero is a measurement.
    cache: dict[str, int] = {}
    seen = False
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, Mapping):
            continue
        counts = {key: usage.get(key) for key in total}
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in counts.values()):
            return None
        seen = True
        for key, value in counts.items():
            total[key] += int(value)  # type: ignore[arg-type]
        details = usage.get("input_token_details")
        if isinstance(details, Mapping):
            for key, source in (("cache_read_tokens", "cache_read"),
                                ("cache_write_tokens", "cache_creation")):
                value = details.get(source)
                if isinstance(value, int) and not isinstance(value, bool):
                    cache[key] = cache.get(key, 0) + value
        # **Reasoning tokens, when the provider reports them.** A *subset* of
        # ``output_tokens``, not an addition — so the bill was never understated, and the
        # missing thing was attribution: at ``xhigh`` on this model 200 of 252 output tokens
        # were reasoning, and without the split "the effort knob changed the cost" and "the
        # answer got longer" are the same observation. That comparison is the whole reason
        # to record an effort setting at all. Same rule as the cache keys: present only when
        # reported, because a written zero would be this code's claim wearing the
        # provider's clothes.
        out_details = usage.get("output_token_details")
        if isinstance(out_details, Mapping):
            value = out_details.get("reasoning")
            if isinstance(value, int) and not isinstance(value, bool):
                cache["reasoning_tokens"] = cache.get("reasoning_tokens", 0) + value
    if not seen:
        return None
    return {**total, **cache}


def _usage_row(model: Any, messages: list[Any], turn_index: Any) -> dict[str, Any]:
    """One cost row for this turn's model calls, with the counts the provider reported.

    The literal ``input_tokens: 0`` this replaces was on the **real-model** path, beside a
    computed ``model`` field that made the two zeros read as observations —
    ``measure/price.py`` prices that shape as free, which is v1's two ladders that
    produced no USD while reporting successfully. A provider that reports nothing gets
    :meth:`Measured.unmeasured`, which the presence test and the price table both know how
    to refuse.
    """
    reported = _reported_tokens(messages)
    if reported is None:
        unmeasured: Measured[int] = Measured.unmeasured(NO_TOKEN_USAGE)
        counts: dict[str, Any] = {"input_tokens": unmeasured, "output_tokens": unmeasured}
    else:
        counts = dict(reported)
    return {
        "turn_index": turn_index,
        # `model_id` first, `_llm_type` only as the fallback. It was the other way round, so
        # every OpenAI turn recorded `model: "openai-chat"` — a LangChain class label — while
        # `knobs_resolved["llm_model"]` beside it held the real id. One turn, two answers, on
        # a comparability field.
        "model": model_id(model) or getattr(model, "_llm_type", None) or type(model).__name__,
        **counts,
    }


def _stub(state: dict) -> dict:
    return {
        "path_kind": "answered",
        "messages": [{"role": "assistant", "content": STUB_ANSWER}],
        "usage": [
            {
                "turn_index": state.get("turn_index", 1),
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
