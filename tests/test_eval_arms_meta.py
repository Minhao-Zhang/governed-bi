"""What the eval solver relays out of ``answer.provenance``.

``arms.py`` is the only bridge between the serve path's per-turn provenance and the
rows an eval run writes to disk, so a diagnostic the serve path records but this
relay drops does not exist as far as any measurement is concerned. That already
happened once: ``ledger_len`` was computed here and never reached a row.

The relay is also where "not measured" has to survive. A missing provenance key must
arrive as ``None``; an empty dict or a zero would assert the different, false claim
that the producer looked and found nothing.

Ledger entries are projected before disk: ``result`` (full query payloads) is
stripped; ``action`` / ``verdict`` / ``layer`` / ``sql`` / ``allowed`` / ``row_count``
remain.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from governed_bi.analyst.middleware import serialize_result
from governed_bi.config import Environment, Settings
from governed_bi.eval.arms import _ledger_for_artifact, agent_solver
from governed_bi.gateway import Gateway, Identity, SqliteConnector

BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"


@pytest.fixture
def solve_with(monkeypatch):
    """Build the eval solver over a stub graph returning one canned answer."""

    def _build(provenance: dict | None, *, sql: str | None = "SELECT 1"):
        answer = SimpleNamespace(
            sql=sql,
            provenance=provenance,
            tier=SimpleNamespace(value="governed"),
            semantic_assurance=SimpleNamespace(value="unflagged"),
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
    assert meta["governance_ledger"] == [
        {
            "action": "run_query",
            "verdict": None,
            "layer": None,
            "reason": None,
            "sql": None,
            "allowed": None,
        },
        {
            "action": "sample_rows",
            "verdict": None,
            "layer": None,
            "reason": None,
            "sql": None,
            "allowed": None,
        },
    ]


def test_absent_diagnostics_relay_as_none_not_as_empty(solve_with):
    """"The producer recorded nothing" and "the producer recorded zero" are different
    facts, and only the second one may print as a zero."""
    _sql, meta = solve_with({"refused_by": None})
    assert meta["stage_events"] is None
    assert meta["n_tool_calls"] is None
    assert meta["by_guardrail_layer"] is None
    assert meta["ledger_len"] is None
    assert meta["governance_ledger"] is None


def test_an_empty_ledger_is_zero_not_none(solve_with):
    _sql, meta = solve_with({"governance_ledger": []})
    assert meta["ledger_len"] == 0
    assert meta["governance_ledger"] == []


def test_ledger_result_payload_is_projected_away(solve_with):
    """A pass entry that carries full ``result`` rows must not reach the artifact."""
    raw = {
        "action": "run_query",
        "verdict": "pass",
        "layer": None,
        "sql": "SELECT 1",
        "allowed": ["beer_factory.transaction"],
        "licensed_ids": ["tbl_beer_factory_transaction"],
        "result": {
            "columns": ["x"],
            "rows": [[Decimal("1.5")], [b"\x00blob"]],
            "row_count": 2,
            "truncated": False,
        },
    }
    _sql, meta = solve_with({"governance_ledger": [raw]})
    assert meta["ledger_len"] == 1
    projected = meta["governance_ledger"]
    assert projected == [
        {
            "action": "run_query",
            "verdict": "pass",
            "layer": None,
            "reason": None,
            "sql": "SELECT 1",
            "allowed": ["beer_factory.transaction"],
            "row_count": 2,
        }
    ]
    assert "result" not in projected[0]
    assert "licensed_ids" not in projected[0]
    json.dumps({"governance_ledger": projected}, ensure_ascii=False)


def test_the_blocking_reason_survives_into_the_artifact():
    """"Which layer blocked" was answerable and "why" was nowhere on disk — zero
    non-null reasons across 1351 baseline rows, because the projection dropped the
    key. A local eval artifact already carries the question and the SQL verbatim, so
    keeping the reason beside them exposes nothing the row does not already say; the
    client surface (``viz.presenter``) still redacts it (AUDIT S7)."""
    from governed_bi.eval.arms import _ledger_for_artifact

    (row,) = _ledger_for_artifact(
        [
            {
                "action": "run_query",
                "verdict": "block",
                "layer": "ast_column_allowlist",
                "reason": "column beer_factory.customers.ssn is not allowlisted",
                "sql": "SELECT ssn FROM customers",
            }
        ]
    )
    assert row["reason"] == "column beer_factory.customers.ssn is not allowlisted"


def test_projected_ledger_from_real_gateway_is_json_serializable():
    """Production middleware stamps ``result: serialize_result(...)`` with connector
    values. Projection must leave a generations row that ``json.dumps`` accepts."""
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    try:
        gw = Gateway(conn)
        result = gw.execute(
            'SELECT "PurchasePrice", "TransactionDate" FROM "transaction" LIMIT 3',
            Identity(user="eval", all_access=True),
        )
    finally:
        conn.close()
    raw_entry = {
        "action": "run_query",
        "verdict": "pass",
        "sql": 'SELECT "PurchasePrice" FROM "transaction" LIMIT 3',
        "allowed": ["beer_factory.transaction"],
        "licensed_ids": ["tbl_beer_factory_transaction"],
        "result": serialize_result(result),
    }
    # Inject a non-JSON type the way Postgres numeric / bytea would appear.
    raw_entry["result"]["rows"] = [
        [Decimal("3.14"), b"\xff\xfe"],
        *([list(r) for r in result.rows] if result.rows else []),
    ]
    projected = _ledger_for_artifact([raw_entry])
    assert projected is not None
    assert "result" not in projected[0]
    assert projected[0]["row_count"] == result.row_count
    row = {
        "ledger_len": 1,
        "governance_ledger": projected,
        "graded_delivery": False,
    }
    json.dumps(row, ensure_ascii=False)


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
