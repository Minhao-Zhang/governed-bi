"""The attempt cap must end the loop, not just refuse the statement.

**The defect, reproduced before the fix.** ``register/knobs.py`` said "the cap TERMINATES the
turn. v1's returned a 'capped' tool message and the agent kept going, burning unbounded
round-trips against a cap it could never clear" — describing v2. ``run_query`` returned exactly
that message, in a ``Command`` with no ``goto``, so control went back to the model with one more
sentence and the same instructions. Measured on this tree at ``cap=5, recursion_limit=60``: five
statements executed, **25** further model calls, then ``GraphRecursionError`` — recorded as
``crashed``, which also discards the super-step and with it the ledger of the work that did
happen. The only real brake in production was ``agent_node_timeout_s = 900``.

The live symptom was quieter and worse: an agent hit the cap and wrote *"The query tool reached
its execution-attempt limit before returning the winning district, so I can't reliably state the
result."* It stopped because the model chose to. A bound that a model can decline is not a bound,
and nothing in the repository could tell the two cases apart.

**These tests count model calls**, because the ledger looked correct throughout the defect —
``terminal`` was already ``"capped"`` while 25 paid round trips were happening behind it. Only
the call count distinguishes "the cap fired" from "the cap ended the turn".
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
from governed_bi.register.stages import ATTEMPT_CAP_REFUSED_BY, Outcome
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
    connector._connect()  # noqa: SLF001 — open for tool use
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
        "licensed": ["main.customers", "customers"],
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
    """The count that matters. ``cap + 1`` calls: the model must propose the statement that
    exceeds the cap before anything can refuse it, and that proposal is the last thing it says.

    Before the fix this was 30 with ``cap=5`` — the recursion limit, reached by 25 post-cap
    round trips that could not have gone anywhere.
    """
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
    """The cap now fires in a middleware, *before* the tool node, so ``run_query`` never runs
    and ``AttemptBook`` never sees the refused call. Without a row written from there,
    ``execution_from_attempts`` reads a passing statement and calls the turn ``answered`` —
    the inversion ``test_capped_after_a_passing_attempt`` exists to prevent, arriving from the
    other side. Two writers now exist, so "exactly one" is the other half of the assertion.
    """
    out = _run(tmp_path, _AlwaysQueries(responses=[]), cap=5)
    executed, caps = _rows(out)

    assert len(executed) == 5, f"the cap is 5 statements, got {len(executed)}"
    assert len(caps) == 1, f"one cap row per capped turn, got {len(caps)}"
    assert (out.get("execution") or {}).get("terminal") == "capped"


def test_a_capped_turn_reaches_the_record_as_capped(tmp_path: Path) -> None:
    """End to end through the compiled graph, because ``Outcome.capped`` is what an eval reads.

    Also the guard on ``thread_limit``: the native counter is a ``PrivateStateAttr`` on the
    nested agent's checkpointed state, so if it were scoped to the outer *thread* rather than
    the turn, turn 2 of a conversation would open with its budget already spent. It is not —
    both turns execute a full budget — and this is the test that would notice if a LangGraph
    release changed where nested channels live.
    """
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
                "corpus_content_hash": "c", "prompt_set_hash": "p", "knobs_resolved": {},
                "n_re_served": 0, "licensed": ["main.customers", "customers"],
                "facet_route_hits": [("facet_schema", "main", 1.0)],
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
    """``ToolCallLimitMiddleware``'s own ``exit_behavior="end"`` raises ``NotImplementedError``
    when the blocked message also calls a different tool, and ``_run`` would record that as
    ``crashed``. Falling back to ``"continue"`` instead restores the original defect exactly —
    measured at 20 model calls and ``GraphRecursionError``. So the middleware here ends the turn
    itself and answers the stranded calls, and this is the test that says which of the three
    behaviours shipped.
    """
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


def test_sampling_a_column_does_not_spend_a_statement(tmp_path: Path) -> None:
    """The two enforcers have to be counting the same population.

    ``attempts_by_call`` is the *turn's* ledger, not the cap's: since the sample path started
    writing rows into it, ``AttemptBook`` charged a slot for every ``sample_rows`` call, so a
    turn that explored three columns had two statements left out of five. The native counter
    counts ``run_query`` tool calls and nothing else, so leaving that in place would have meant
    two enforcers disagreeing about what "5 attempts" means, with the stricter one winning
    invisibly.
    """
    model = _SamplesThenQueries(responses=[])
    out = _run(tmp_path, model, cap=5)
    executed, caps = _rows(out)

    assert len(executed) == 5, (
        f"three sample_rows calls left {len(executed)} of 5 statements; the knob is named "
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
