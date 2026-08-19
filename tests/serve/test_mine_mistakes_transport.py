"""Mistake-memory mining fires for a real, agent-driven correction sequence through the
compiled graph -- not just when ``mine_mistake_from_execution`` is called directly.

**Mirrors ``tests/serve/test_clarification_mining_transport.py``'s own discipline exactly, and
for the identical reason (that file's own docstring, and commit ``d20832a``): a test that only
calls the extraction function directly, or that hand-builds an ``execution`` dict and calls
``mine_mistakes_node`` in isolation (``tests/serve/test_mine_mistakes.py``), proves the
algorithm is right but proves nothing about whether the node actually fires on real traffic --
whether ``agent_core``'s own ledger construction lands in the shape this node expects, wired at
the point in the graph where it actually runs.**

Drives ``compile_graph().invoke()`` (the same harness ``test_turn_contract.py`` and
``test_agent_tools_hitl.py`` already use) with a :class:`ScriptedChatModel` that calls
``run_query`` twice in one turn -- once against a table the turn's corpus does not license
(refused before it reaches the connector), once against the one table it does (answered,
through the ``_EchoConnector`` double) -- reproducing exactly the "governance block, then
self-correct within the same turn" pattern ``mine_mistake_from_execution`` mines. No import of,
or call into, ``scripts/mine_mistakes_v2.py`` anywhere in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# `two_schema_index` / `two_schema_assets` are not imported: they are `tests/serve/conftest.py`
# fixtures, injected by name as test-function parameters below, the same way every other file
# in this directory reaches them.
from turn_contract_fixtures import _base_turn, _EchoConnector, _policy  # noqa: E402


def _scripted_correction(*, unlicensed_sql: str, licensed_sql: str) -> Any:
    """Calls ``run_query`` on ``unlicensed_sql``, then ``licensed_sql``, then answers."""
    from langchain_core.messages import AIMessage

    from governed_bi.serve.scripted_model import ScriptedChatModel

    def _call(sql: str, call_id: str) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[{"name": "run_query", "args": {"sql": sql}, "id": call_id, "type": "tool_call"}],
        )

    return ScriptedChatModel(
        responses=[
            _call(unlicensed_sql, "rq-1"),
            _call(licensed_sql, "rq-2"),
            AIMessage(content="Corrected: there are 3 sensors."),
        ]
    )


def _config(**extra: Any) -> dict[str, Any]:
    conf: dict[str, Any] = {"thread_id": "t-mine-mistakes", "policy": _policy(), "connector": _EchoConnector()}
    conf.update(extra)
    return {"configurable": conf}


def test_a_governance_block_then_self_correction_writes_a_mistake_memory_draft(
    two_schema_index, two_schema_assets, tmp_path: Path  # noqa: F811 -- pytest fixtures by name
) -> None:
    from governed_bi.corpus.analyst import analyst_corpus_from_keys
    from governed_bi.corpus.store import load
    from governed_bi.serve.graph import compile_graph

    # `sensors voltage` licenses ops_b.sensors and nothing else -- the identical corpus shape
    # `test_turn_contract.py::test_execution_terminal_agrees_with_the_attempts_it_carries` uses
    # to get one refused and one answered attempt, here driven as two calls in ONE turn.
    corpus = analyst_corpus_from_keys(allowed=("ops_b.sensors.voltage",))
    model = _scripted_correction(
        unlicensed_sql="SELECT count(*) FROM sales_a.orders",
        licensed_sql="SELECT count(*) FROM ops_b.sensors",
    )
    config = _config(
        index=two_schema_index, assets_by_id=two_schema_assets, corpus=corpus,
        agent_model=model, corpus_root=tmp_path,
    )
    turn = _base_turn(
        question="sensors voltage", db_id="ops_b", turn_id="turn-mine-mistakes",
        knobs_resolved={"enable_mistake_memory_mining": True, "route_top_n": 1, "candidate_depth": 50},
    )

    out = compile_graph().invoke(turn, config)

    execution = out["answer"]["record"].get("execution") or {}
    attempts = list(execution.get("attempts") or ())
    assert len(attempts) == 2, f"precondition: two run_query attempts landed in the ledger, got {attempts!r}"
    assert attempts[0].get("passed") is False and attempts[1].get("passed") is True, (
        f"precondition: the first attempt was refused and the second answered, got {attempts!r}"
    )
    assert out["answer"]["outcome"] == "answered"

    assets, problems = load(tmp_path)
    assert not problems, problems
    (draft,) = assets
    assert draft.asset_type.value == "few_shot"
    # The *executed* SQL (canonicalised, row-limited), not the model's raw text -- the same
    # distinction `agent_core.py::_last_executed_sql` draws.
    assert "ops_b.sensors" in draft.sql
    assert "sensors voltage" in draft.summary


def test_a_clean_turn_with_no_correction_mines_nothing(
    two_schema_index, two_schema_assets, tmp_path: Path  # noqa: F811 -- pytest fixtures by name
) -> None:
    """The negative case: a turn whose first (and only) ``run_query`` attempt already passes
    has nothing to teach, and the corpus stays empty even with the knob on."""
    from langchain_core.messages import AIMessage

    from governed_bi.corpus.analyst import analyst_corpus_from_keys
    from governed_bi.corpus.store import load
    from governed_bi.serve.graph import compile_graph
    from governed_bi.serve.scripted_model import ScriptedChatModel

    corpus = analyst_corpus_from_keys(allowed=("ops_b.sensors.voltage",))
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "run_query",
                    "args": {"sql": "SELECT count(*) FROM ops_b.sensors"},
                    "id": "rq-1",
                    "type": "tool_call",
                }],
            ),
            AIMessage(content="There are 3 sensors."),
        ]
    )
    config = _config(
        index=two_schema_index, assets_by_id=two_schema_assets, corpus=corpus,
        agent_model=model, corpus_root=tmp_path,
    )
    turn = _base_turn(
        question="sensors voltage", db_id="ops_b", turn_id="turn-clean",
        knobs_resolved={"enable_mistake_memory_mining": True, "route_top_n": 1, "candidate_depth": 50},
    )

    out = compile_graph().invoke(turn, config)

    execution = out["answer"]["record"].get("execution") or {}
    attempts = list(execution.get("attempts") or ())
    assert len(attempts) == 1 and attempts[0].get("passed") is True, (
        f"precondition: the one attempt this turn made passed immediately, got {attempts!r}"
    )

    assets, _ = load(tmp_path)
    assert assets == [], f"a clean turn with no correction was mined anyway: {[a.id for a in assets]}"
