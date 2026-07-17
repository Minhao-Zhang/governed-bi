"""Regression tests for governance review findings (sample_rows, stamp, exec, G1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn, tool_call
from governed_bi.memory import InMemoryWorkingMemory
from governed_bi.analyst.agent import (
    ServeRailsState,
    answer_question_agent,
    build_agent_core,
    extract_final_sql,
)
from governed_bi.analyst.middleware import (
    AGENT_RECURSION_LIMIT,
    GovernanceMiddleware,
)

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "corpus"
BIRD_DB = Path(__file__).resolve().parents[1] / "data" / "bird" / "beer_factory.sqlite"
TXN = "tbl_beer_factory_transaction"


@pytest.fixture
def corpus():
    return load_corpus(CORPUS_ROOT, schema="beer_factory").for_analyst()


@pytest.fixture
def settings():
    return Settings.for_env(Environment.dev)


@pytest.fixture
def identity():
    return Identity(user="dev", all_access=True)


@pytest.fixture
def bird_gateway():
    if not BIRD_DB.exists():
        pytest.skip("vendored beer_factory.sqlite not present")
    conn = SqliteConnector(BIRD_DB)
    yield Gateway(conn)
    conn.close()


def _agent(corpus, gateway, identity, settings, responses):
    return build_agent_core(
        corpus,
        gateway,
        identity,
        FakeToolModel(responses=responses),
        settings=settings,
        dialect="sqlite",
        multi_schema=False,
        default_schema=None,
    )


# --------------------------------------------------------------------------- #
# #1 sample_rows is guardrailed — no SELECT * exfiltration
# --------------------------------------------------------------------------- #


def test_sample_rows_selects_only_allowlisted_columns(
    corpus, bird_gateway, settings, identity
):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn("sample_rows", {"table_id": TXN, "n": 2}, "c2"),
        AIMessage(content="ok"),
    ]
    agent = _agent(corpus, bird_gateway, identity, settings, turns)
    final = agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})
    sample = next(e for e in final["ledger"] if e.get("action") == "sample_rows")
    assert sample["verdict"] == "pass"
    sql = sample["sql"]
    assert "SELECT *" not in sql.upper().replace(" ", " ")
    assert "SELECT *" not in sql
    # Explicit columns only
    assert "FROM" in sql.upper()
    cols = sample["result"]["columns"]
    assert cols  # returned something
    # Audit: exactly one gateway execute for the sample
    assert any(a.sql == sql for a in bird_gateway.audit_log)


def test_sample_rows_blocked_without_license(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("sample_rows", {"table_id": TXN, "n": 2}, "c1"),
        AIMessage(content="ok"),
    ]
    agent = _agent(corpus, bird_gateway, identity, settings, turns)
    final = agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})
    sample = next(e for e in final["ledger"] if e.get("action") == "sample_rows")
    assert sample["verdict"] == "deny"
    assert "not licensed" in sample["reason"]
    assert not any("sample" in (a.sql or "").lower() for a in bird_gateway.audit_log)


# --------------------------------------------------------------------------- #
# #2 tables_used from parsed SQL (G3), not the whole licensed set
# --------------------------------------------------------------------------- #


def test_extract_final_sql_parses_tables_used_not_all_licensed(corpus):
    # License two tables; SQL only touches transaction.
    final = {
        "licensed": [TXN, "tbl_beer_factory_customers"],
        "ledger": [
            {
                "action": "run_query",
                "verdict": "pass",
                "sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"',
                "result": {"columns": ["total_revenue"], "rows": [[1]], "row_count": 1, "truncated": False},
            }
        ],
    }
    sql, tables_used, entry = extract_final_sql(
        final, corpus=corpus, dialect="sqlite", multi_schema=False
    )
    assert sql is not None
    assert tables_used == frozenset({TXN})
    assert "tbl_beer_factory_customers" not in tables_used
    assert entry is not None


def test_finalize_stamps_over_sql_tables_only(corpus, bird_gateway, settings, identity):
    # Inspect two tables, query only one — stamp must not plan over the unused one.
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn("inspect_schema", {"table_id": "tbl_beer_factory_customers"}, "c2"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c3",
        ),
        AIMessage(content="done"),
    ]
    before = len(bird_gateway.audit_log)
    ans = answer_question_agent(
        "What is the total revenue?",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="stamp-test",
        model=FakeToolModel(responses=turns),
    )
    assert ans.safety_clearance is True
    assert ans.provenance["tables_used"] == [TXN]
    # #3: one execute for the winning SQL (not double)
    winning_sql = ans.sql
    assert winning_sql is not None
    after = bird_gateway.audit_log[before:]
    matching = [a for a in after if a.sql == winning_sql]
    assert len(matching) == 1


# --------------------------------------------------------------------------- #
# #8 hard-stop preserves full ledger
# --------------------------------------------------------------------------- #


def test_hard_stop_preserves_prior_ledger(corpus, bird_gateway, settings, identity):
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT "StarRating" FROM "rootbeerreview"'},
            "c2",
        ),
        ai_tool_turn("run_query", {"sql": "DROP TABLE customers"}, "c3"),
    ]
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="l2-ledger",
        model=FakeToolModel(responses=turns),
    )
    ledger = ans.provenance["governance_ledger"]
    assert len(ledger) >= 2
    assert ledger[0]["verdict"] == "block"
    assert ledger[0]["layer"] == "term_semantics"
    assert ledger[-1]["layer"] == "policy_blacklist"


# --------------------------------------------------------------------------- #
# recursion exhaustion preserves the accumulated ledger (Inv #10)
# --------------------------------------------------------------------------- #


def test_recursion_exhaustion_preserves_ledger(corpus, bird_gateway, settings, identity):
    # A trailing tool-call turn repeats forever (FakeToolModel replays its last
    # message), so the agent never returns a terminal answer and blows the step
    # budget → GraphRecursionError. The refusal must still carry the real ledger
    # (run_query pass + sample_rows entries) and attempt count, not an empty one.
    # Many distinct turns (unique tool_call ids so add_messages doesn't dedup)
    # so the step budget (40) is exhausted well before the script runs out —
    # avoids the last-message replay reusing an id.
    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c2",
        ),
    ]
    turns += [
        ai_tool_turn("sample_rows", {"table_id": TXN, "n": 1}, f"s{i}")
        for i in range(30)
    ]
    ans = answer_question_agent(
        "total revenue",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="recursion-ledger",
        model=FakeToolModel(responses=turns),
    )
    assert ans.tier.value == "refused"
    assert ans.provenance.get("recursion_exhausted") is True
    ledger = ans.provenance.get("governance_ledger") or []
    assert ledger, "exhaustion refusal lost the accumulated governance ledger"
    # The passing run_query the agent managed before exhausting is in the trail.
    assert any(
        e.get("action") == "run_query" and e.get("verdict") == "pass" for e in ledger
    )
    assert ans.provenance.get("attempts") == sum(
        1 for e in ledger if e.get("action") == "run_query"
    )


# --------------------------------------------------------------------------- #
# #4 working memory injected into system prompt
# --------------------------------------------------------------------------- #


def test_working_memory_reaches_agent_prompt(corpus, bird_gateway, settings, identity, monkeypatch):
    memory = InMemoryWorkingMemory()
    memory.append("mem-sess", "user", "What is revenue by brand?")
    memory.append("mem-sess", "assistant", "Here is a breakdown.")

    captured: dict = {}

    real_build = __import__(
        "governed_bi.analyst.agent", fromlist=["build_agent_core"]
    ).build_agent_core

    def spy_build(*args, **kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt")
        return real_build(*args, **kwargs)

    monkeypatch.setattr("governed_bi.analyst.agent.build_agent_core", spy_build)

    turns = [
        ai_tool_turn("inspect_schema", {"table_id": TXN}, "c1"),
        ai_tool_turn(
            "run_query",
            {"sql": 'SELECT SUM("PurchasePrice") AS total_revenue FROM "transaction"'},
            "c2",
        ),
        AIMessage(content="done"),
    ]
    answer_question_agent(
        "and the total?",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="mem-sess",
        model=FakeToolModel(responses=turns),
        working_memory=memory,
    )
    prompt = captured.get("system_prompt") or ""
    # Amendment 1: history now flows through assemble_context's conversation block,
    # injected into the agent prompt via the "## Governed context" seed.
    assert "Conversation so far" in prompt
    assert "revenue by brand" in prompt


# --------------------------------------------------------------------------- #
# #5 / #6 recursion_limit + sequential coercion
# --------------------------------------------------------------------------- #


def test_recursion_limit_constant():
    # Raised from the ADR Q6 first guess (15) after live cs_semester runs hit the
    # limit on ordinary questions — sequential tool calls (G1) inflate step count.
    assert AGENT_RECURSION_LIMIT == 40


def test_coerce_single_tool_call_keeps_first_only():
    from langchain.agents.middleware.types import ModelResponse

    msg = AIMessage(
        content="",
        tool_calls=[
            tool_call("inspect_schema", {"table_id": TXN}, "a"),
            tool_call("run_query", {"sql": "SELECT 1"}, "b"),
        ],
    )
    out = GovernanceMiddleware._coerce_single_tool_call(ModelResponse(result=[msg]))
    assert isinstance(out, ModelResponse)
    assert len(out.result[0].tool_calls) == 1
    assert out.result[0].tool_calls[0]["name"] == "inspect_schema"


def test_serve_rails_state_is_thin():
    # Finding #7: no heavy deps on the TypedDict.
    keys = set(ServeRailsState.__annotations__)
    assert "allowlist" not in keys
    assert "graph_obj" not in keys
    assert "identity" not in keys
    # Amendment 1 adds context_block (str) + seed_licensed (list of ids); the HITL
    # branch adds clarification (a plain ClarificationRequest dict) — all
    # serializable primitives, not heavy objects, so finding #7 still holds.
    assert keys <= {
        "question",
        "session_id",
        "base_provenance",
        "context_block",
        "seed_licensed",
        "answer",
        "outcome",
        "clarification",
    }
