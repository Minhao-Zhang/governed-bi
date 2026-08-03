"""F3: tools bounds, delivery_hash, ask_user HITL + identity-bound resume."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.delivery import DeliveryTracker, delivery_hash_for, payload_digest
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import ResumeRejected, resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.tools import build_tools, tool_bounds_from_state


def _assets() -> dict[str, Any]:
    table = TableAsset(
        id="sales.customers",
        schema="sales",
        physical_name="customers",
        summary="customers table",
        body="Customer master for retail.",
        columns=("sales.customers.id", "sales.customers.name"),
    )
    col_id = ColumnAsset(
        id="sales.customers.id",
        schema="sales",
        parent_table="customers",
        physical_name="id",
        summary="customer id",
        physical_type="INTEGER",
    )
    col_name = ColumnAsset(
        id="sales.customers.name",
        schema="sales",
        parent_table="customers",
        physical_name="name",
        summary="customer name",
        physical_type="TEXT",
    )
    return {a.id: a for a in (table, col_id, col_name)}


def _state(**overrides: Any) -> dict[str, Any]:
    payload = {
        "question": "how many customers",
        "turn_id": "turn-f3",
        "turn_index": 1,
        "licensed": ["sales.customers"],
        "retrieved": {
            "by_type": {"table": ["sales.customers"]},
            "selected": {
                "sales.customers": {
                    "asset_id": "sales.customers",
                    "asset_type": "table",
                    "score": 1.0,
                }
            },
            "attributions": {},
            "pulled_in": {},
            "schema_ranking": [("sales", 1.0)],
            "lexical_coverage": 1.0,
        },
        "delivery": {
            "context_block": "ctx",
            "context_hash": "a" * 64,
            "tool_delivered": {},
            "delivery_hash": None,
        },
        "execution": {"attempts": [], "terminal": "no_sql", "guardrail_errors": 0},
        "messages": [],
        "knobs_resolved": {},
    }
    payload.update(overrides)
    return payload


def _config(**extra: Any) -> dict[str, Any]:
    conf = {
        "thread_id": "t-f3",
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "assets_by_id": _assets(),
        "corpus": for_analyst(list(_assets().values())),
    }
    conf.update(extra)
    return {"configurable": conf}


def test_out_of_scope_tools_share_identical_message() -> None:
    tracker = DeliveryTracker()
    tools = {t.name: t for t in build_tools(_state(), _config(), tracker)}
    assert tools["read_body"].invoke({"asset_ids": ["nope"]}) == OUT_OF_SCOPE_MESSAGE
    assert (
        tools["inspect_schema"].invoke({"table_id": "other.table"})
        == OUT_OF_SCOPE_MESSAGE
    )
    assert (
        tools["sample_rows"].invoke({"column_id": "other.table.col", "limit": 3})
        == OUT_OF_SCOPE_MESSAGE
    )


def test_inspect_schema_licensed_succeeds() -> None:
    tracker = DeliveryTracker()
    tools = {t.name: t for t in build_tools(_state(), _config(), tracker)}
    payload = tools["inspect_schema"].invoke({"table_id": "sales.customers"})
    assert "sales.customers" in payload
    assert "physical_type" in payload
    assert tracker.tool_delivered
    digest = next(iter(tracker.tool_delivered.values()))
    assert digest == payload_digest(payload)


def test_read_body_records_delivery_and_hash_changes_with_payload() -> None:
    tracker = DeliveryTracker()
    tools = {t.name: t for t in build_tools(_state(), _config(), tracker)}
    p1 = tools["read_body"].invoke({"asset_ids": ["sales.customers"]})
    d1 = dict(tracker.tool_delivered)
    h1 = delivery_hash_for("a" * 64, d1)

    assets = _assets()
    from governed_bi.corpus.schema import TableAsset

    assets["sales.customers"] = TableAsset(
        id="sales.customers",
        schema="sales",
        physical_name="customers",
        summary="customers table",
        body="DIFFERENT BODY",
        columns=("sales.customers.id",),
    )
    tracker2 = DeliveryTracker()
    tools2 = {
        t.name: t
        for t in build_tools(
            _state(),
            _config(assets_by_id=assets, corpus=for_analyst(list(assets.values()))),
            tracker2,
        )
    }
    p2 = tools2["read_body"].invoke({"asset_ids": ["sales.customers"]})
    assert p1 != p2
    h2 = delivery_hash_for("a" * 64, tracker2.tool_delivered)
    assert h1 != h2
    assert delivery_hash_for("a" * 64, d1) == h1


def test_run_query_blocks_unlicensed_table(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO customers VALUES (1, 'a')")
    conn.commit()
    conn.close()

    from governed_bi.datasource.sqlite import SqliteConnector

    connector = SqliteConnector(db)
    connector._connect()  # noqa: SLF001 — open for tool use
    tracker = DeliveryTracker()
    state = _state(licensed=["sales.other"])
    tools = {
        t.name: t
        for t in build_tools(state, _config(connector=connector), tracker)
    }
    out = tools["run_query"].invoke({"sql": "SELECT id FROM customers"})
    assert "refused" in out.lower() or "not" in out.lower()


def test_run_query_attempt_cap(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER)")
    conn.commit()
    conn.close()
    from governed_bi.datasource.sqlite import SqliteConnector

    connector = SqliteConnector(db)
    connector._connect()  # noqa: SLF001
    policy = GovernancePolicy(guard_rules_enabled={}, run_query_attempt_cap=2)
    tracker = DeliveryTracker()
    tools = {
        t.name: t
        for t in build_tools(
            _state(licensed=["main.customers", "customers"]),
            _config(connector=connector, policy=policy),
            tracker,
        )
    }
    # Force failures that still count as attempts
    for _ in range(2):
        tools["run_query"].invoke({"sql": "SELECT * FROM nope"})
    capped = tools["run_query"].invoke({"sql": "SELECT * FROM nope"})
    assert "capped" in capped.lower()


def test_tool_exception_is_not_refuse() -> None:
    class Boom:
        dialect = "sqlite"

        def execute(self, sql: str):
            raise RuntimeError("boom")

        def sample_values(self, *a, **k):
            raise RuntimeError("boom")

    tracker = DeliveryTracker()
    tools = {
        t.name: t for t in build_tools(_state(), _config(connector=Boom()), tracker)
    }
    out = tools["run_query"].invoke({"sql": "SELECT 1"})
    assert out.startswith("run_query") or "refused" in out.lower() or "error" in out.lower()
    assert "refused_by" not in out


def test_ask_user_interrupt_and_identity_resume() -> None:
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {"question": "which year?"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="ok: 2020"),
        ]
    )
    graph = compile_graph()
    token = "identity-secret-f3"
    config = {
        "configurable": {
            "thread_id": "t-hitl",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
        }
    }
    turn = {
        "question": "revenue?",
        "thread_id": "t-hitl",
        "turn_index": 1,
        "turn_id": "turn-hitl",
        "run_id": "r",
        "question_id": "q",
        "db_id": "sales",
        "attempt_id": "a",
        "corpus_content_hash": "c",
        "prompt_set_hash": "p",
        "knobs_resolved": {},
        "n_re_served": 0,
        "facet_route_hits": [("facet_schema", "sales", 1.0)],
        "messages": [],
        "usage": [],
        "identity": {"token": token},
        "clarifications": [],
    }
    paused = graph.invoke(turn, config)
    assert paused.get("__interrupt__")

    with pytest.raises(ResumeRejected):
        resume_clarification(
            graph, config=config, identity={"token": "wrong"}, answer="2020"
        )

    done = resume_clarification(
        graph, config=config, identity={"token": token}, answer="2020"
    )
    assert done.get("path_kind") == "answered" or done.get("answer", {}).get(
        "outcome"
    ) in {"answered", "clarification"}
    clars = done.get("clarifications") or []
    assert any(c.get("answer") == "2020" for c in clars)
    assert done["answer"]["outcome"] in {"answered", "clarification"}


def test_delivery_hash_stable_for_same_tool_payload() -> None:
    delivered = {"c1": payload_digest("hello")}
    assert delivery_hash_for("ctx", delivered) == delivery_hash_for("ctx", delivered)
    assert delivery_hash_for("ctx", delivered) != delivery_hash_for(
        "ctx", {"c1": payload_digest("hello!")}
    )


def test_tool_bounds_from_state_includes_pulled_in() -> None:
    bounds = tool_bounds_from_state(
        {
            "licensed": ["s.t"],
            "retrieved": {
                "selected": {},
                "pulled_in": {"s.t.extra": "resolve"},
                "attributions": {},
            },
        }
    )
    assert bounds.may_read_body("s.t.extra")
    assert bounds.may_inspect_schema("s.t")
