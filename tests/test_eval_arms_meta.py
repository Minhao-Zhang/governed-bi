"""What the eval solver relays out of ``answer.provenance``.

``arms.py`` is the only bridge between the serve path's per-turn provenance and the
rows an eval run writes to disk, so a diagnostic the serve path records but this
relay drops does not exist as far as any measurement is concerned. That already
happened once: ``ledger_len`` was computed here and never reached a row.

The relay is also where "not measured" has to survive. A missing provenance key must
arrive as ``None``; an empty dict or a zero would assert the different, false claim
that the producer looked and found nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from governed_bi.config import Environment, Settings
from governed_bi.eval.arms import agent_solver


@pytest.fixture
def solve_with(monkeypatch):
    """Build the eval solver over a stub graph returning one canned answer."""

    def _build(provenance: dict | None, *, sql: str | None = "SELECT 1"):
        answer = SimpleNamespace(
            sql=sql,
            provenance=provenance,
            tier=SimpleNamespace(value="governed"),
            semantic_assurance=SimpleNamespace(value="grounded"),
            safety_clearance=True,
        )
        graph = SimpleNamespace(invoke=lambda state, config=None: {"answer": answer})
        monkeypatch.setattr(
            "governed_bi.analyst.agent.build_serve_rails",
            lambda **kwargs: graph,
        )
        solver = agent_solver(
            corpus=None,
            gateway=None,
            settings=Settings.for_env(Environment.dev),
            identity=None,
            model=None,
        )
        return solver.solve_with_meta("how many customers?")

    return _build


def test_stage_diagnostics_are_relayed_verbatim(solve_with):
    events = [{"stage": "route", "status": "ok", "ms": 12.5, "detail": {"intent": "sql"}}]
    tool_calls = {"search_corpus": 3, "run_query": 1}
    layers = {"ast_column_allowlist": 1}
    _sql, meta = solve_with(
        {
            "stage_events": events,
            "n_tool_calls": tool_calls,
            "by_guardrail_layer": layers,
            "governance_ledger": [{"action": "run_query"}, {"action": "sample_rows"}],
        }
    )
    assert meta["stage_events"] == events
    assert meta["n_tool_calls"] == tool_calls
    assert meta["by_guardrail_layer"] == layers
    assert meta["ledger_len"] == 2


def test_absent_diagnostics_relay_as_none_not_as_empty(solve_with):
    """"The producer recorded nothing" and "the producer recorded zero" are different
    facts, and only the second one may print as a zero."""
    _sql, meta = solve_with({"refused_by": None})
    assert meta["stage_events"] is None
    assert meta["n_tool_calls"] is None
    assert meta["by_guardrail_layer"] is None
    assert meta["ledger_len"] is None


def test_an_empty_ledger_is_zero_not_none(solve_with):
    _sql, meta = solve_with({"governance_ledger": []})
    assert meta["ledger_len"] == 0


def test_no_answer_at_all_is_a_coverage_refusal(monkeypatch):
    """The one path that bypasses provenance entirely: the graph produced no answer
    object, so there is nothing to relay and the arm records why."""
    graph = SimpleNamespace(invoke=lambda state, config=None: {})
    monkeypatch.setattr(
        "governed_bi.analyst.agent.build_serve_rails", lambda **kwargs: graph
    )
    solver = agent_solver(
        corpus=None,
        gateway=None,
        settings=Settings.for_env(Environment.dev),
        identity=None,
        model=None,
    )
    sql, meta = solver.solve_with_meta("q")
    assert sql is None
    assert meta == {"refused_by": "no_coverage"}
