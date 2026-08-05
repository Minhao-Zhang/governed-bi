"""A crash in the agent loop must not erase the ledger of work that already happened.

**The defect, reproduced.** A model call that dies *after* a governed statement executed — which
is exactly what an exhausted retry looks like — left the node raising, ``wrap_node`` returning
``{failure, path_kind}`` and nothing else, and the audit record showing ``attempts: []`` for a
turn whose SQL had reached the database. ``usage`` and ``result_table`` went with it, so the
turn was also priced at zero.

The cause is structural rather than a bug: LangGraph's subgraph documentation says *"the parent
graph treats the entire subgraph execution as a single step"*, so an exception discards the whole
super-step's writes. Two candidate fixes were measured **not** to work — reading the nested state
back after the raise, and ``create_agent(checkpointer=True)`` — before ``agent.stream`` was.

For a system whose thesis is that the ledger *is* the artifact, "executed but unrecorded" is the
one direction it must not fail in. These tests hold that.
"""

from __future__ import annotations

from typing import Any

import httpx
import openai
from langchain_core.messages import AIMessage

from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.wrap import wrap_node

ASK = AIMessage(
    "",
    tool_calls=[
        {
            "name": "run_query",
            "args": {"sql": "SELECT COUNT(*) AS n FROM beer_factory.customers"},
            "id": "c1",
        }
    ],
)


class _DiesAfterOneToolCall(ScriptedChatModel):
    """Answers once with a tool call, then raises — an exhausted provider retry's shape."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        if any(getattr(m, "type", "") == "tool" for m in messages):
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _run_turn(model: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "turn_index": 1,
        "turn_id": "t-partial",
        "messages": [],
        "usage": [],
    }
    config = {
        "configurable": {
            "thread_id": "t-partial",
            "policy": GovernancePolicy(),
            "agent_model": model,
        }
    }
    # Through `wrap_node`, because that is the boundary that used to be the whole story: it
    # turned the exception into `{failure, path_kind}` and the ledger died with the frame.
    return wrap_node("agent_core", agent_core_node)(state, config)


def test_a_crash_after_a_governed_statement_keeps_the_attempt() -> None:
    """The turn crashed **and** one statement was governed. Both are recorded.

    `attempts: []` beside a populated `generated_sql` is the artifact contradicting itself, and
    it is what `serve/agent_state.py` was written to stop happening on a resume. A crash is the
    other way to reach it.
    """
    out = _run_turn(_DiesAfterOneToolCall(responses=[ASK]))

    assert out["path_kind"] == "crashed"
    assert out["failure"] == {"stage": "agent_core", "error_type": "APITimeoutError"}

    execution = out.get("execution") or {}
    attempts = execution.get("attempts") or []
    assert len(attempts) == 1, (
        "the statement reached governance before the model died; a record with no attempt says "
        f"the turn touched nothing, got {attempts!r}"
    )
    assert out.get("generated_sql"), "the statement is named, not just counted"


def test_a_crash_still_bills_the_tokens_it_spent() -> None:
    """The successful calls cost money whatever happened to the last one.

    `usage` was `None` on this path, so `measure/price.py` priced a turn that had really run a
    model at zero — the same shape as the missing guard and rewriter rows, reached by crashing
    instead of by never being written.
    """
    out = _run_turn(_DiesAfterOneToolCall(responses=[ASK]))
    assert [row.get("stage") for row in (out.get("usage") or [])] == ["agent_core"]


def test_the_messages_the_loop_did_produce_survive() -> None:
    """The AI turn that asked for the query and the tool result it got back.

    Without them the conversation loses a turn it really had, and the next turn's model sees a
    history that never happened.
    """
    out = _run_turn(_DiesAfterOneToolCall(responses=[ASK]))
    assert [getattr(m, "type", None) for m in (out.get("messages") or [])] == ["ai", "tool"]


def test_a_clean_turn_is_unchanged_and_carries_no_failure() -> None:
    """The guard against fixing the crash path by making every turn look crashed."""
    out = _run_turn(ScriptedChatModel(responses=[AIMessage("three customers")]))

    assert out["path_kind"] == "answered"
    assert "failure" not in out, "a turn that did not fail must not carry a failure marker"
