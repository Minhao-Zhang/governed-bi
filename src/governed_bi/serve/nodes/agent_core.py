"""``agent_core`` — create_agent loop + tools (ADR 0005 §3.1)."""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

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

__all__ = ["agent_core_node", "STUB_ANSWER"]

STUB_ANSWER = "STUB_ANSWER"


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
    usage = [
        {
            "turn_index": state.get("turn_index", 1),
            "model": getattr(model, "_llm_type", None) or type(model).__name__,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    ]

    update: dict[str, Any] = {
        "path_kind": "answered",
        "messages": out_messages[len(messages) :] + human_msgs,
        "usage": usage,
        "delivery": delivery,
        "clarification_requested": False,
        "execution": execution_from_attempts(attempts, has_sql=bool(generated_sql)),
    }
    if clarifications:
        update["clarifications"] = clarifications
    if generated_sql:
        update["generated_sql"] = generated_sql
    return update


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
