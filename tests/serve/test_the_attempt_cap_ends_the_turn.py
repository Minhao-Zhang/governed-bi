"""The attempt cap ends the loop.

A capped tool message that lets the agent continue burns further round-trips against a cap it cannot clear.
"""

from __future__ import annotations

import asyncio
import itertools
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.registry.stages import ATTEMPT_CAP_REFUSED_BY, Outcome
from governed_bi.serve.agent_state import CAP_LEDGER_KEY, AttemptBook
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.ledger import answering_attempts
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.scripted_model import ScriptedChatModel

#: Distinct tool call ids across the whole module. ``AttemptBook`` readmits an id it has already
#: charged — that is what makes a resume replay idempotent — so a model that reuses one id never
#: reaches the cap at all, and a test written that way would pass against the defect.
_IDS = itertools.count(1)


def _query_call(n: int) -> dict[str, Any]:
    return {
        "name": "run_query",
        "args": {"sql": f"SELECT {n} AS n FROM customers"},
        "id": f"c{n}",
        "type": "tool_call",
    }


class _AlwaysQueries(ScriptedChatModel):
    """Asks for another statement on every call, forever — a model that never gives up."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        self.prompts_seen.append(list(messages))
        message = AIMessage("", tool_calls=[_query_call(next(_IDS))])
        return ChatResult(generations=[ChatGeneration(message=message)])


class _SamplesThenQueries(ScriptedChatModel):
    """Three ``sample_rows`` calls, then statements forever."""

    n_samples: int = 3

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        self.prompts_seen.append(list(messages))
        n = next(_IDS)
        done = sum(1 for m in messages if str(getattr(m, "type", "")) == "tool")
        call = (
            {"name": "sample_rows", "args": {"column_id": "main.customers.id"},
             "id": f"s{n}", "type": "tool_call"}
            if done < self.n_samples
            else _query_call(n)
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=[call]))])


class _QueriesBesideAnotherTool(ScriptedChatModel):
    """Pairs every statement with an ``inspect_schema`` in the same assistant message."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        self.prompts_seen.append(list(messages))
        n = next(_IDS)
        calls = [
            _query_call(n),
            {"name": "inspect_schema", "args": {"table_id": "main.customers"},
             "id": f"i{n}", "type": "tool_call"},
        ]
        return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=calls))])


class _QueriesTwiceInOneMessage(ScriptedChatModel):
    """Two ``run_query`` calls in the same assistant message — the shape production hit."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        self.prompts_seen.append(list(messages))
        calls = [_query_call(next(_IDS)), _query_call(next(_IDS))]
        return ChatResult(generations=[ChatGeneration(message=AIMessage("", tool_calls=calls))])


def _assets() -> dict[str, Any]:
    table = TableAsset(
        id="main.customers", schema="main", physical_name="customers",
        summary="customers", body="Customers.", columns=("main.customers.id",),
    )
    column = ColumnAsset(
        id="main.customers.id", schema="main", parent_table="customers",
        physical_name="id", summary="id", physical_type="INTEGER",
    )
    return {a.id: a for a in (table, column)}


def _connector(tmp_path: Path) -> Any:
    from governed_bi.datasource.sqlite import SqliteConnector

    db = tmp_path / "cap.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER)")
    conn.execute("INSERT INTO customers VALUES (1)")
    conn.commit()
    conn.close()
    connector = SqliteConnector(db)
    return connector


def _config(tmp_path: Path, model: Any, cap: int, thread: str = "t-cap") -> dict[str, Any]:
    assets = _assets()
    return {
        # High enough that the recursion limit is not what stops the turn. Under the defect
        # this is precisely what *did* stop it, 25 model calls after the cap.
        "recursion_limit": 60,
        "configurable": {
            "thread_id": thread,
            "policy": GovernancePolicy(guard_rules_enabled={}, run_query_attempt_cap=cap),
            "agent_model": model,
            "connector": _connector(tmp_path),
            "assets_by_id": assets,
            "corpus": for_analyst(list(assets.values())),
        },
    }


def _turn_state() -> dict[str, Any]:
    return {
        "question": "how many customers",
        "turn_id": "turn-cap",
        "turn_index": 1,
        "retrieved_tables": ["main.customers", "customers"],
        "messages": [],
        "usage": [],
    }


def _run(tmp_path: Path, model: Any, cap: int) -> dict[str, Any]:
    return asyncio.run(agent_core_node(_turn_state(), _config(tmp_path, model, cap)))


def _rows(out: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    """``(executed statements, cap rows)`` from the turn's execution record.

    Filtered through ``answering_attempts``, because ``sample_rows`` also carries an
    ``executed_sql`` — counting those is the same mistake this module says the cap was making.
    """
    attempts = (out.get("execution") or {}).get("attempts") or []
    executed = [a for a in answering_attempts(attempts) if a.get("executed_sql")]
    caps = [a for a in attempts if a.get("reason_code") == ATTEMPT_CAP_REFUSED_BY]
    return executed, caps


def test_the_loop_ends_at_the_cap_instead_of_running_to_the_recursion_limit(tmp_path: Path) -> None:
    """The count that matters."""
    model = _AlwaysQueries(responses=[])
    out = _run(tmp_path, model, cap=5)

    assert len(model.prompts_seen) == 6, (
        f"{len(model.prompts_seen)} model calls for a cap of 5; the cap refuses the statement "
        "and something has to refuse the turn"
    )
    assert out["path_kind"] == "answered", (
        f"path_kind={out.get('path_kind')!r} failure={out.get('failure')!r}; a turn stopped by "
        "its own budget did not fail, and recording it as a crash both misreports it and "
        "discards the super-step's ledger"
    )
    assert "failure" not in out


def test_the_ended_turn_still_records_exactly_one_cap_row(tmp_path: Path) -> None:
    """The cap fires before the tool node, so the refused call never reaches ``AttemptBook``."""
    out = _run(tmp_path, _AlwaysQueries(responses=[]), cap=5)
    executed, caps = _rows(out)

    assert len(executed) == 5, f"the cap is 5 statements, got {len(executed)}"
    assert len(caps) == 1, f"one cap row per capped turn, got {len(caps)}"
    assert (out.get("execution") or {}).get("terminal") == "capped"


def test_a_capped_turn_reaches_the_record_as_capped(tmp_path: Path) -> None:
    """End to end through the compiled graph, because ``Outcome.capped`` is what an eval reads."""
    model = _AlwaysQueries(responses=[])
    graph = compile_graph()
    config = _config(tmp_path, model, cap=2, thread="t-cap-graph")

    for turn_index in (1, 2):
        out = graph.invoke(
            {
                "question": "how many customers",
                "thread_id": "t-cap-graph",
                "turn_index": turn_index,
                "turn_id": f"turn-cap-{turn_index}",
                "run_id": "r", "question_id": "q", "db_id": "main", "attempt_id": "a",
                "corpus_content_hash": "c", "prompt_set_hash": "p", "settings_resolved": {},
                "n_re_served": 0, "retrieved_tables": ["main.customers", "customers"],
                "channel_route_hits": [("channel_schema", "main", 1.0)],
                "messages": [], "usage": [], "identity": {"token": "tok"},
            },
            config,
        )
        executed, caps = _rows(out)
        assert out["answer"]["outcome"] == Outcome.capped.value, out["answer"]["outcome"]
        assert len(caps) == 1
        assert len(executed) == 2, (
            f"turn {turn_index} executed {len(executed)} of its 2 attempts; a budget that does "
            "not reset per turn silently starves every turn after the first"
        )


def test_a_cap_reached_beside_another_tool_call_still_ends_the_turn(tmp_path: Path) -> None:
    """A cap beside another tool call still ends the turn, rather than recording a crash."""
    model = _QueriesBesideAnotherTool(responses=[])
    out = _run(tmp_path, model, cap=3)
    executed, caps = _rows(out)

    assert out["path_kind"] == "answered", f"failure={out.get('failure')!r}"
    assert len(model.prompts_seen) == 4
    assert len(executed) == 3
    assert len(caps) == 1
    # Every tool call answered. A dangling `tool_call_id` is a message history most providers
    # reject, and these messages are appended to the conversation the *next* turn replays.
    asked = {
        tc["id"]
        for m in out["messages"]
        if isinstance(m, AIMessage)
        for tc in m.tool_calls or ()
    }
    answered = {
        str(getattr(m, "tool_call_id", ""))
        for m in out["messages"]
        if str(getattr(m, "type", "")) == "tool"
    }
    assert asked <= answered, f"unanswered tool calls: {sorted(asked - answered)}"


def test_a_cap_reached_inside_one_batch_of_the_capped_tool_answers_both_calls(tmp_path: Path) -> None:
    """The defect that poisoned a live thread, reproduced."""
    model = _QueriesTwiceInOneMessage(responses=[])
    out = _run(tmp_path, model, cap=3)
    _executed, caps = _rows(out)

    assert out["path_kind"] == "answered", f"failure={out.get('failure')!r}"
    assert len(caps) == 1, f"one cap row per capped turn, got {len(caps)}"

    asked = {
        tc["id"]
        for m in out["messages"]
        if isinstance(m, AIMessage)
        for tc in m.tool_calls or ()
    }
    answered = {
        str(getattr(m, "tool_call_id", ""))
        for m in out["messages"]
        if str(getattr(m, "type", "")) == "tool"
    }
    assert asked <= answered, (
        f"unanswered tool calls: {sorted(asked - answered)} — this history cannot be replayed, "
        "so the next turn on this thread is dead on arrival"
    )


def test_sampling_a_column_does_not_spend_a_statement(tmp_path: Path) -> None:
    """The two enforcers have to be counting the same population."""
    model = _SamplesThenQueries(responses=[])
    out = _run(tmp_path, model, cap=5)
    executed, caps = _rows(out)

    assert len(executed) == 5, (
        f"three sample_rows calls left {len(executed)} of 5 statements; the setting is named "
        "run_query_attempt_cap and sampling is not a run_query attempt"
    )
    assert len(caps) == 1


def test_the_book_ignores_rows_that_are_not_run_query_attempts() -> None:
    """The unit behind the turn above, including the cap row: a book that counted its own
    ``capped`` marker would spend a slot recording that it had run out of them."""
    committed = {
        "sample_rows:s1": {"path": "sample", "passed": True},
        CAP_LEDGER_KEY: {"path": "agent", "reason_code": ATTEMPT_CAP_REFUSED_BY, "passed": False},
        "rq-1": {"path": "agent", "reason_code": "passed", "passed": True},
    }
    assert AttemptBook(2).charged(committed) == 1
    assert AttemptBook(2).admit(committed, "rq-2") is True
