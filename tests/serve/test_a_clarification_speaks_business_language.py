"""``ask_user`` may not show a raw schema identifier to the human it is asking.

Shape rules ported from the downstream fork ``utkuai/detentai-fork``
(``tests/serve/test_schema_term_guard.py``). What is ours is the integration, and it is the
half a port can get expensively wrong: the rejection is returned **before** the latch is taken
and before ``interrupt()``, so the rejected ``tool_use`` gets its ``ToolMessage`` on the same
pass. A thread carrying a dangling ``tool_use`` is permanently unreplayable on Bedrock.

Scoped to ``ask_user``. That a *refusal* may name a table is a recorded owner decision
(``docs/analysis/adopting-the-downstream-fork-2026-08-19.md``); a question written for a reader
to answer is the other surface.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.schema_term_guard import find_schema_leak
from governed_bi.serve.tools import build_tools

# ── the shapes ─────────────────────────────────────────────────────────────────


def test_the_actual_leaked_example_is_caught() -> None:
    """The exact leak the fork found in its own ``ask_user`` docstring's worked example.

    Which is the finding worth keeping: the tool's prompt instructions were the only guard
    against this, and the example beside them did it.
    """
    leak = find_schema_leak("does revenue mean payments.amount or line_items.unit_price?")
    assert leak in ("payments.amount", "line_items.unit_price")


def test_a_snake_case_column_name_is_caught() -> None:
    assert find_schema_leak("should we exclude pct_delivered from the total?") == "pct_delivered"


def test_a_camel_case_column_name_is_caught() -> None:
    assert find_schema_leak("is CaneSugar recorded as TRUE/FALSE or 1/0?")
    assert find_schema_leak("does price mean CurrentRetailPrice or PurchasePrice?")


def test_plain_business_language_is_not_flagged() -> None:
    """The negative half, and the reason the guard is shape-based rather than corpus-based.

    A column named ``status`` or ``name`` is an ordinary English word, so a guard comparing
    against known physical names would refuse questions like these — which are exactly the
    questions ``ask_user`` exists to ask.
    """
    for text in (
        "Should cancelled orders be excluded from total revenue?",
        "Does active mean transacted in the last 30 days?",
        "What does status mean for a closed account?",
        "Is the discount applied before or after tax?",
        "Which nights make the most money?",
    ):
        assert find_schema_leak(text) is None, text


def test_every_text_is_checked_and_not_only_the_first() -> None:
    """``why`` is rendered beside the question, so it is the same surface."""
    assert find_schema_leak("plain question", "why: payments.amount decides the answer")
    assert find_schema_leak("plain question", "plain why") is None


def test_a_camel_case_brand_name_still_trips_it() -> None:
    """A known limitation, pinned rather than left to be rediscovered as a bug.

    Shape detection cannot tell ``PurchasePrice`` from ``PowerKiosk`` — both are two
    capitalised words with no space. The cost is one rephrase on the rare question naming a
    compound-capitalised brand, never a wrong answer, and the two shapes that cannot occur in
    English at all (dotted paths, snake_case) have no such mode.
    """
    assert find_schema_leak("Ask PowerKiosk what they would like to see") == "PowerKiosk"


# ── wired into the tool ────────────────────────────────────────────────────────

LEAKY = "does revenue mean payments.amount or line_items.unit_price?"


def _ask_user() -> Any:
    assets = {
        a.id: a
        for a in (
            TableAsset(
                id="sales.customers",
                schema="sales",
                physical_name="customers",
                summary="customers table",
                body="Customer master for retail.",
            ),
        )
    }
    state: dict[str, Any] = {
        "question": "what is revenue?",
        "turn_id": "turn-clar",
        "turn_index": 1,
        "licensed": ["sales.customers"],
        "messages": [],
        "knobs_resolved": {},
    }
    config = {
        "configurable": {
            "thread_id": "t-clar",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "assets_by_id": assets,
            "corpus": for_analyst(list(assets.values())),
        }
    }
    return {t.name: t for t in build_tools(state, config)}["ask_user"]


def _runtime(call_id: str) -> Any:
    """A hand-built ``ToolRuntime`` — see ``test_agent_tools_hitl.py::_runtime`` for why."""
    from langchain.tools import ToolRuntime

    return ToolRuntime(
        state={"attempts_by_call": {}},
        context=None,
        config={"configurable": {}},
        stream_writer=lambda _chunk: None,
        tool_call_id=call_id,
        store=None,
    )


def test_a_leaked_identifier_is_rejected_with_a_tool_reply_and_records_nothing() -> None:
    """Retry, not record — and the retry is what the code supports.

    The reply is what makes it safe: this ``tool_use`` is answered on the same pass, so nothing
    is stranded. Recording instead would mean pausing first, which shows the reader the text the
    guard exists to keep away from them, and then needing a channel for the verdict.
    """
    command = asyncio.run(
        _ask_user().coroutine(question=LEAKY, basis="data_definition", runtime=_runtime("c1"))
    )
    reply = str(command.update["messages"][0].content)
    assert "ask_user rejected" in reply
    assert "payments.amount" in reply, "a rejection that does not name the word is one the model guesses at"
    assert list(command.update) == ["messages"], (
        f"the rejected call recorded state: {sorted(command.update)}. A leaked question must not "
        "reach clarifications_by_call — the guard flags nothing and records nothing, it retries."
    )


def test_a_rejected_question_does_not_consume_the_one_outstanding_slot() -> None:
    """Placement, asserted where it is observable.

    The guard sits **before** ``pending_clarification.append``. Put it after, and a rejected
    question would hold the turn's only clarification slot for the rest of the node execution:
    the next call would be told "this turn already has one" while no human had been asked
    anything at all.
    """
    ask = _ask_user()
    first = asyncio.run(
        ask.coroutine(question=LEAKY, basis="data_definition", runtime=_runtime("c1"))
    )
    second = asyncio.run(
        ask.coroutine(question=LEAKY, basis="data_definition", runtime=_runtime("c2"))
    )
    for command in (first, second):
        reply = str(command.update["messages"][0].content)
        assert "ask_user rejected" in reply, reply
        assert "already has one" not in reply, reply


def test_a_plain_question_still_reaches_the_interrupt() -> None:
    """The guard must not swallow the path it is guarding.

    ``interrupt()`` outside a graph raises ``RuntimeError`` from LangGraph's config lookup, so
    that raise *is* the pass condition: the call got past the guard to the line that pauses the
    turn. Returning a ``Command`` here would mean an ordinary business question was refused.
    """
    with pytest.raises(RuntimeError):
        asyncio.run(
            _ask_user().coroutine(
                question="Should cancelled orders be excluded from total revenue?",
                basis="data_definition",
                runtime=_runtime("c3"),
            )
        )


def test_unknown_basis_is_rejected_before_the_turn_pauses() -> None:
    command = asyncio.run(
        _ask_user().coroutine(
            question="Should cancelled orders be excluded from total revenue?",
            basis="join_missing",
            runtime=_runtime("c4"),
        )
    )
    reply = str(command.update["messages"][0].content)
    assert "ask_user rejected" in reply
    assert "data_definition" in reply and "ranking_ambiguity" in reply
    assert list(command.update) == ["messages"]
