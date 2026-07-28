"""Regression tests for governance review findings (sample_rows, stamp, exec, G1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

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
from governed_bi.config import Environment, Settings
from governed_bi.corpus import load_corpus
from governed_bi.gateway import Gateway, Identity, SqliteConnector
from governed_bi.llm.fake import FakeToolModel, ai_tool_turn, tool_call
from governed_bi.memory import InMemoryWorkingMemory

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
        default_schema="beer_factory",
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
    assert "SELECT *" not in sql.upper()
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
    # The point is that NOTHING executed. Asserting on the substring "sample" was
    # vacuous (it never appears in generated SQL), so deleting the license gate at
    # middleware.py left this test green — assert on the audit log itself instead.
    assert bird_gateway.audit_log == []


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
        final, corpus=corpus, dialect="sqlite", default_schema="beer_factory"
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


def test_recursion_limit_leaves_room_for_a_realistic_tool_chain():
    """Not a pin on the number — a check on the property the number exists for.

    Sequential tool calls (G1) mean a normal search -> inspect x N -> query ->
    repair chain costs ~2 steps per tool call. The ADR Q6 first guess of 15 was hit
    by ordinary live questions. Asserting `== 40` only detected someone editing the
    line; this fails if the budget stops covering the chain it is sized for.
    """
    search, inspects, query, repair = 1, 4, 1, 2
    steps_per_tool_call = 2
    assert AGENT_RECURSION_LIMIT >= (search + inspects + query + repair) * steps_per_tool_call


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


# --------------------------------------------------------------------------- #
# A blocked turn carries the tables its SQL referenced.
#
# Offline analysis reads `tables_used` to ask "did this answer reach past the router".
# A turn that generated a query and had it *rejected* used to carry no `tables_used` at
# all, so those rows were dropped from the routing-escape denominator — and the drop is
# correlated with the event being measured: the escape most likely to trip L4
# term-semantics is precisely one that reached an out-of-routed table without
# `inspect_schema` licensing it first. So the escape rate was biased low by an unknown
# amount, which is the same failure class as the two earlier versions of that metric,
# narrowed rather than removed.
# --------------------------------------------------------------------------- #


def test_a_blocked_query_still_reports_the_tables_it_referenced(
    corpus, bird_gateway, settings, identity
):
    from governed_bi.analyst.agent import answer_question_agent

    # A write statement: L2 policy refuses it outright, so the turn generates SQL over a
    # real table and still fails. Using an unlicensed table instead is fragile — the
    # beer_factory corpus licenses most of them.
    blocked_sql = 'DELETE FROM "beer_factory"."customers"'
    model = FakeToolModel(
        responses=[
            ai_tool_turn("run_query", {"sql": blocked_sql}, "c1"),
            AIMessage(content="I cannot answer that."),
        ]
    )
    answer = answer_question_agent(
        "how many customers are there",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="blocked",
        model=model,
    )
    prov = answer.provenance or {}
    # The turn did not succeed...
    assert prov.get("refused_by") or answer.sql is None or prov.get("failed_layer")
    # ...but it referenced a table, and that fact is recorded rather than lost.
    used = prov.get("tables_used")
    assert used, (
        "a blocked query left no tables_used, so offline analysis cannot tell which "
        "schemas the attempt touched and silently drops the row"
    )
    assert any("customer" in str(t).lower() for t in used), used


def test_a_turn_that_generated_no_sql_reports_no_tables(
    corpus, bird_gateway, settings, identity
):
    """The complementary case, so the stamp above cannot be always-on. A turn that never
    produced a query used no tables — which is a different fact from a turn whose tables
    could not be resolved, and must stay absent rather than becoming an empty list."""
    from governed_bi.analyst.agent import answer_question_agent

    model = FakeToolModel(responses=[AIMessage(content="I have no idea.")])
    answer = answer_question_agent(
        "unanswerable question",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="nosql",
        model=model,
    )
    assert not (answer.provenance or {}).get("tables_used")


def test_a_column_blocked_refusal_reports_the_table_it_referenced(
    corpus, bird_gateway, settings, identity
):
    """The *other* refusal path. A guardrail hard stop is caught in one place; a query
    rejected and then abandoned falls through `extract_final_sql` finding no passing entry,
    which is a different branch. Patching them one at a time is how the hard stop got
    missed, so both are pinned.

    The table must be one the corpus knows, because `tables_used` records asset **ids** —
    a query over a table absent from the corpus resolves to nothing and correctly records
    nothing. So this blocks on a column instead: `customers` is licensed, the column is not.
    """
    from governed_bi.analyst.agent import answer_question_agent

    model = FakeToolModel(
        responses=[
            ai_tool_turn(
                "run_query",
                {"sql": 'SELECT "definitely_not_a_column" FROM "customers"'},
                "c1",
            ),
            AIMessage(content="I cannot answer that."),
        ]
    )
    answer = answer_question_agent(
        "unanswerable via a blocked column",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="colblock",
        model=model,
    )
    prov = answer.provenance or {}
    assert prov.get("refused_by"), "the blocked column should not have produced an answer"
    used = prov.get("tables_used")
    assert used, (
        "a rejected-then-abandoned query left no tables_used, so the routing-escape "
        "measurement silently drops the row"
    )
    assert any("customer" in str(t).lower() for t in used), used


def test_a_query_over_a_table_the_corpus_does_not_know_records_nothing(
    corpus, bird_gateway, settings, identity
):
    """`tables_used` holds asset ids, so a table absent from the corpus has none to record.
    That is correct rather than a gap: for the routing-escape measurement the table is
    always *in* the pooled corpus, just in a schema the router excluded."""
    from governed_bi.analyst.agent import answer_question_agent

    model = FakeToolModel(
        responses=[
            ai_tool_turn("run_query", {"sql": 'SELECT COUNT(*) FROM "geolocation"'}, "c1"),
            AIMessage(content="I cannot answer that."),
        ]
    )
    answer = answer_question_agent(
        "how many geolocations",
        identity,
        corpus=corpus,
        gateway=bird_gateway,
        settings=settings,
        session_id="unknown-table",
        model=model,
    )
    prov = answer.provenance or {}
    assert prov.get("refused_by")
    assert not prov.get("tables_used")


# --------------------------------------------------------------------------- #
# AUDIT R1: the delivered answer was always the LAST passing run_query, so a
# post-answer sanity check got reported as the answer.
# --------------------------------------------------------------------------- #


def _ledger_with(*sqls):
    return [{"action": "run_query", "verdict": "pass", "sql": s} for s in sqls]


def test_final_sql_prefers_the_query_the_agent_quoted(corpus):
    from langchain_core.messages import AIMessage

    from governed_bi.analyst.agent import extract_final_sql

    real = 'SELECT "CustomerID" FROM "beer_factory"."customers"'
    sanity = 'SELECT COUNT(*) FROM "beer_factory"."customers"'
    final = {
        # The sanity check ran last...
        "ledger": _ledger_with(real, sanity),
        # ...but the agent presented the first query as its answer.
        "messages": [AIMessage(content=f"Here is the answer:\n```sql\n{real}\n```")],
    }

    sql, _tables, entry = extract_final_sql(final, corpus=corpus, dialect="sqlite")
    assert sql == real
    assert entry["final_sql_source"] == "agent_final_message"


def test_final_sql_falls_back_to_last_passing_when_the_message_says_nothing(corpus):
    from langchain_core.messages import AIMessage

    from governed_bi.analyst.agent import extract_final_sql

    first = 'SELECT "CustomerID" FROM "beer_factory"."customers"'
    last = 'SELECT COUNT(*) FROM "beer_factory"."customers"'
    final = {
        "ledger": _ledger_with(first, last),
        "messages": [AIMessage(content="Done — see the table above.")],
    }

    sql, _tables, entry = extract_final_sql(final, corpus=corpus, dialect="sqlite")
    assert sql == last
    assert entry["final_sql_source"] == "last_passing"


def test_final_sql_ignores_blocked_entries(corpus):
    from governed_bi.analyst.agent import extract_final_sql

    ok = 'SELECT "CustomerID" FROM "beer_factory"."customers"'
    final = {
        "ledger": [
            *_ledger_with(ok),
            {"action": "run_query", "verdict": "block", "sql": "SELECT * FROM secrets"},
        ],
        "messages": [],
    }
    sql, _tables, _entry = extract_final_sql(final, corpus=corpus, dialect="sqlite")
    assert sql == ok


# --------------------------------------------------------------------------- #
# AUDIT T3: the fake model discarded `messages`, so the system prompt and the
# tool set were untested — both could be emptied with a green suite.
# --------------------------------------------------------------------------- #


def test_the_agent_actually_sends_the_governance_system_prompt(
    corpus, bird_gateway, settings, identity
):
    from governed_bi.llm.fake import FakeToolModel

    model = FakeToolModel(responses=[AIMessage(content="done")])
    agent = build_agent_core(
        corpus,
        bird_gateway,
        identity,
        model,
        settings=settings,
        dialect="sqlite",
        default_schema="beer_factory",
    )
    agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})

    system = model.system_text()
    assert system, "no system message reached the model at all"
    # Load-bearing instructions, not incidental wording: without these the loop is
    # an ungoverned text-to-SQL agent that happens to be wrapped in middleware.
    assert "inspect_schema" in system
    assert "run_query" in system


def test_the_agent_offers_exactly_the_governed_tool_set(
    corpus, bird_gateway, settings, identity
):
    from governed_bi.llm.fake import FakeToolModel

    model = FakeToolModel(responses=[AIMessage(content="done")])
    agent = build_agent_core(
        corpus,
        bird_gateway,
        identity,
        model,
        settings=settings,
        dialect="sqlite",
        default_schema="beer_factory",
    )
    agent.invoke({"messages": [HumanMessage("x")], "licensed": [], "ledger": []})

    assert model.tools_seen, "create_agent never bound tools"
    offered = {getattr(t, "name", None) for t in model.tools_seen[-1]}
    assert {"search_corpus", "inspect_schema", "sample_rows", "run_query"} <= offered
    # ask_user is serve-only (needs a checkpointer); the eval path must not see it.
    assert "ask_user" not in offered
