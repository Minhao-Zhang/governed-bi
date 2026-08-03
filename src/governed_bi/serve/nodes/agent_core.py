"""``agent_core`` — create_agent loop + tools (ADR 0005 §3.1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from governed_bi.register.quantity import Measured
from governed_bi.serve.delivery import DeliveryTracker
from governed_bi.serve.runtime import configurable
from governed_bi.serve.state import TERMINAL_PATH_KINDS
from governed_bi.serve.tools import (
    SYSTEM_PROMPT,
    attempts_from_tools,
    build_tools,
    clarifications_from_tools,
    execution_from_attempts,
)

__all__ = ["agent_core_node", "STUB_ANSWER", "NO_TOKEN_USAGE"]

STUB_ANSWER = "STUB_ANSWER"

#: Why a usage row carries no token count. :meth:`Measured.unmeasured` requires a reason,
#: and this one has to reach the artifact: "not measured" with no explanation is
#: indistinguishable from a forgotten assignment.
NO_TOKEN_USAGE = "the provider returned no usage_metadata carrying both token counts"


def agent_core_node(
    state: dict,
    config: RunnableConfig,
    *,
    checkpointer: Any = None,
) -> dict:
    """Run the main model + tools, or the F1 stub when no ``agent_model`` is set."""
    if state.get("path_kind") in TERMINAL_PATH_KINDS:
        return {}

    cfg = configurable(config)
    model = cfg.get("agent_model")
    if model is None:
        return _stub(state)

    tracker = DeliveryTracker((state.get("delivery") or {}).get("tool_delivered"))
    tools = build_tools(state, config, tracker)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

    messages = list(state.get("messages") or [])
    result = agent.invoke({"messages": messages}, config)
    out_messages = list(result.get("messages") or [])

    clarifications = clarifications_from_tools(tools)
    # Also recover from message pairs if the tool box was rebuilt empty on resume.
    if not clarifications:
        clarifications = _clarifications_from_messages(
            out_messages, turn_id=str(state.get("turn_id") or "")
        )

    human_msgs = [
        HumanMessage(content=f"[clarification] {c['question']}\nAnswer: {c['answer']}")
        for c in clarifications
    ]

    attempts = attempts_from_tools(tools)
    generated_sql = _last_run_query_sql(out_messages)
    delivery = tracker.merge_into(state.get("delivery"))
    usage = [_usage_row(model, out_messages[len(messages) :], state.get("turn_index", 1))]

    update: dict[str, Any] = {
        "path_kind": "answered",
        "messages": out_messages[len(messages) :] + human_msgs,
        "usage": usage,
        "delivery": delivery,
        "clarification_requested": False,
        "execution": execution_from_attempts(attempts),
    }
    if clarifications:
        update["clarifications"] = clarifications
    if generated_sql:
        update["generated_sql"] = generated_sql
    return update


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
    cache = {"cache_read_tokens": 0, "cache_write_tokens": 0}
    seen = False
    reported_cache = False
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
                    reported_cache = True
                    cache[key] += value
    if not seen:
        return None
    return {**total, **(cache if reported_cache else {})}


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
        "model": getattr(model, "_llm_type", None) or type(model).__name__,
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


def _clarifications_from_messages(messages: list[Any], *, turn_id: str) -> list[dict]:
    pending: str | None = None
    out: list[dict] = []
    for m in messages:
        tool_calls = getattr(m, "tool_calls", None) or ()
        for tc in tool_calls:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            if name == "ask_user":
                args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                pending = str((args or {}).get("question") or "")
        if getattr(m, "type", None) == "tool" or m.__class__.__name__ == "ToolMessage":
            if pending is not None:
                out.append(
                    {
                        "question": pending,
                        "answer": str(getattr(m, "content", "")),
                        "turn_id": turn_id,
                    }
                )
                pending = None
    return out


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
