"""F3: tools bounds, delivery_hash, ask_user HITL + identity-bound resume."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE
from governed_bi.govern.layers import GUARDRAIL_ERROR
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.agent_state import CAP_LEDGER_KEY
from governed_bi.serve.delivery import delivery_hash_for, payload_digest
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


def _tools(state: dict[str, Any] | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {t.name: t for t in build_tools(state or _state(), config or _config())}


def _runtime(call_id: str, committed: dict[str, Any] | None = None) -> Any:
    """A ``ToolRuntime`` built by hand, because a tool that takes one cannot be invoked alone.

    ``ToolRuntime`` is injected by the agent's tool node, not by ``langchain_core``'s
    ``StructuredTool``: ``runtime`` is a **required** field of the generated args schema, so
    ``tool.invoke({"args": ...})`` fails validation on it before the body runs. Supplying one
    here is what keeps these tests direct unit tests of the tool bodies rather than agent
    round-trips — the agent path is covered end to end by
    ``test_ask_user_interrupt_and_identity_resume`` and the F turn contracts.
    """
    from langchain.tools import ToolRuntime

    return ToolRuntime(
        state={"attempts_by_call": dict(committed or {})},
        context=None,
        config={"configurable": {}},
        stream_writer=lambda _chunk: None,
        tool_call_id=call_id,
        store=None,
    )


def _call(
    tool: Any, call_id: str = "call-1", committed: dict[str, Any] | None = None, **args: Any
) -> tuple[str, dict[str, Any]]:
    """Run a tool's body and split its ``Command`` in two.

    Every tool now returns a ``Command`` carrying its own ``ToolMessage``; the tool call id is
    what keys the durable ledger, so passing one is the point rather than ceremony.
    ``committed`` is the ledger the *checkpoint* already holds, which is how the attempt cap's
    resume behaviour is exercised.

    Returns ``(text the model sees, everything the call recorded)``.
    """
    # `.coroutine` first: the tools are `async def` now — the shape the nested agent's `astream`
    # needs — and `@tool` puts an async implementation there, leaving `.func` as None.
    body = tool.coroutine or tool.func
    returned = body(runtime=_runtime(call_id, committed), **args)
    command = asyncio.run(returned) if inspect.isawaitable(returned) else returned
    update = dict(getattr(command, "update", None) or {})
    messages = list(update.pop("messages", []) or [])
    return (str(getattr(messages[0], "content", "")) if messages else ""), update


def test_out_of_scope_tools_share_identical_message() -> None:
    tools = _tools()
    assert _call(tools["read_body"], asset_ids=["nope"])[0] == OUT_OF_SCOPE_MESSAGE
    assert _call(tools["inspect_schema"], table_id="other.table")[0] == OUT_OF_SCOPE_MESSAGE
    assert (
        _call(tools["sample_rows"], column_id="other.table.col", limit=3)[0]
        == OUT_OF_SCOPE_MESSAGE
    )


def test_an_out_of_scope_refusal_is_not_recorded_as_a_delivery() -> None:
    """The model receives the message; the corpus delivered nothing.

    ``delivery_hash`` audits what the corpus handed over, so a refusal counted as a delivery
    would put a digest of ``OUT_OF_SCOPE_MESSAGE`` in the record and make three refused reads
    hash-distinct from three refused reads of something else. The old code encoded this by
    *skipping* the tracker call on an early return — correct, and invisible to any caller.
    """
    for tool_name, args in (
        ("read_body", {"asset_ids": ["nope"]}),
        ("inspect_schema", {"table_id": "other.table"}),
        ("sample_rows", {"column_id": "other.table.col"}),
    ):
        text, update = _call(_tools()[tool_name], **args)
        assert text == OUT_OF_SCOPE_MESSAGE
        assert "tool_delivered" not in update, f"{tool_name} recorded a refusal as a delivery"


def test_inspect_schema_licensed_succeeds() -> None:
    payload, update = _call(_tools()["inspect_schema"], table_id="sales.customers")
    assert "sales.customers" in payload
    assert "physical_type" in payload
    delivered = update["tool_delivered"]
    assert delivered == {"call-1": payload_digest(payload)}, (
        "the delivery must be keyed by the tool call id. It was a fresh uuid4(), so a digest "
        "in the record named nothing and could not be traced to the call that produced it."
    )


def test_read_body_records_delivery_and_hash_changes_with_payload() -> None:
    p1, u1 = _call(_tools()["read_body"], asset_ids=["sales.customers"])
    d1 = dict(u1["tool_delivered"])
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
    tools2 = _tools(
        _state(), _config(assets_by_id=assets, corpus=for_analyst(list(assets.values())))
    )
    p2, u2 = _call(tools2["read_body"], asset_ids=["sales.customers"])
    assert p1 != p2
    h2 = delivery_hash_for("a" * 64, u2["tool_delivered"])
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
    tools = _tools(_state(licensed=["sales.other"]), _config(connector=connector))
    out, update = _call(tools["run_query"], sql="SELECT id FROM customers")
    assert "refused" in out.lower() or "not" in out.lower()
    assert list(update["attempts_by_call"]) == ["call-1"], (
        "a governed statement must leave exactly one ledger row, keyed by its call id"
    )


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
    tools = _tools(
        _state(licensed=["main.customers", "customers"]),
        _config(connector=connector, policy=policy),
    )
    # Force failures that still count as attempts. Distinct call ids, because the cap is now
    # counted over ids rather than over a list length — which is what makes it idempotent
    # under a replay instead of resetting on one.
    rows: dict[str, Any] = {}
    for i in range(2):
        _, update = _call(tools["run_query"], call_id=f"rq-{i}", sql="SELECT * FROM nope")
        rows.update(update.get("attempts_by_call") or {})
    assert list(rows) == ["rq-0", "rq-1"], rows

    capped, update = _call(tools["run_query"], call_id="rq-2", sql="SELECT * FROM nope")
    assert "capped" in capped.lower()
    assert list(update.get("attempts_by_call") or {}) == [CAP_LEDGER_KEY], (
        "the cap must write its own ledger row. `_run_query` used to return on the cap "
        "*before* appending, so a capped turn carried an empty ledger while `generated_sql` "
        "was still read out of the tool arguments -- ExecutionRecord declared 'capped' and "
        "nothing ever wrote it. The key is a constant rather than `cap:<call_id>` so that "
        "the tool and the middleware that now ends the turn cannot write two of them."
    )


def test_a_replayed_run_query_does_not_consume_a_second_attempt_slot() -> None:
    """The cap counts governed statements, not tool invocations.

    This is the property that makes the ledger survive a resume. Attempts are keyed by tool
    call id, so the same call arriving twice — which is what a replay is — is one statement.
    Under the previous list-append accounting it was two, and under the previous *closure*
    accounting a resume reset the count to zero instead.
    """
    from governed_bi.serve.agent_state import AttemptBook

    committed = {"rq-0": {"passed": False}}

    book = AttemptBook(1)
    assert book.admit(committed, "rq-0") is True, "a replay of a counted call may run"
    assert book.admit(committed, "rq-1") is False, "a new call at the cap must be refused"

    # A fresh book over the same committed ledger agrees, which is the resume case: the count
    # comes from the checkpoint, not from how many times the node has executed.
    assert AttemptBook(1).admit(committed, "rq-2") is False

    # And within one super-step, where nothing has committed yet, the in-flight set is what
    # stops two parallel calls both reading a count of zero.
    parallel = AttemptBook(1)
    assert parallel.admit(None, "rq-a") is True
    assert parallel.admit(None, "rq-b") is False, (
        "two run_query calls in one AI message both read committed=0 and both proceeded: "
        "a cap of 1 admitting 2 governed statements"
    )


class _Answering:
    """A connector that answers, so a test can be about governance rather than about the driver."""

    dialect = "postgres"

    def execute(self, sql: str, **_: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        return (["id"], [(1,)], False)
def test_tool_exception_is_not_refuse() -> None:
    class Boom:
        dialect = "sqlite"

        def execute(self, sql: str):
            raise RuntimeError("boom")

    tools = _tools(_state(), _config(connector=Boom()))
    out, update = _call(tools["run_query"], sql="SELECT 1")
    # Discriminating, deliberately. This used to read
    #     out.startswith("run_query") or "refused" in out.lower() or "error" in out.lower()
    # which the real refusal string `run_query refused: id binds to customers.id, which is not
    # allowed` also satisfies — so the test could not fail for the reason it exists (audit M4).
    assert "RuntimeError" in out, f"the driver's failure is not named: {out!r}"
    assert "refused" not in out.lower(), f"a driver failure is reported as a refusal: {out!r}"
    assert "refused_by" not in out
    # The statement passed governance and was sent to the driver, so the ledger owes it a row
    # even though the driver raised. Returning only the error string would make a driver
    # failure indistinguishable from a turn that attempted nothing.
    assert list(update.get("attempts_by_call") or {}) == ["call-1"], update
    row = update["attempts_by_call"]["call-1"]
    assert row["passed"] is True, "the statement did pass every layer; the driver is what failed"
    assert row["reason_code"] != GUARDRAIL_ERROR, (
        "a driver failure is not a guardrail error; counting it as one would block quotability "
        "for an operational fault"
    )


def test_a_checker_that_raises_is_recorded_rather_than_returned_as_a_string() -> None:
    """Audit C1 — the worst measurement defect found, and the one with no coverage at all.

    An exception escaping ``prepare()`` was caught on the tool surface, refunded, and handed to
    the model as a string with **no ledger row**. ``stamp`` reads an empty ledger as "answered
    from the delivered context", so the turn recorded ``outcome: answered``,
    ``guardrail_errors: 0``, every quotability gate green, and ``generated_sql`` holding a
    statement that never reached ``prepare()``. A systematically broken ``check()`` presented as
    a clean, quotable arm.

    The escape is reached the way it happens in production: a malformed key in the corpus.
    ``check()`` normalises its key arguments *outside* its own ``try`` on purpose
    (``check.py:89-100``) — "a security parameter was not wired up" must not become a blocked
    verdict — and ``normalise_column_key`` raises ``ValueError`` on a four-part key. The
    governance side is right; the recording side had nowhere for the raise to land.

    Paired with ``test_tool_exception_is_not_refuse`` above: a **driver** failure keeps its
    passing row and is not a guardrail error, while a **checker** failure produces a
    ``guardrail_error`` row and crashes the turn. The two must not collapse into each other.
    """
    from governed_bi.corpus.analyst import AnalystCorpus
    from governed_bi.serve.ledger import execution_from_attempts

    # Constructed directly rather than through `analyst_corpus_from_keys`, which validates and
    # would raise here instead of inside `check()`. A corpus object that exists and holds a
    # malformed key is exactly the state C1 needs: the failure has to happen *inside the tool
    # body*, past the wiring checks, where the old code turned it into a string.
    broken = AnalystCorpus({}, frozenset({"a.b.c.d"}), frozenset(), frozenset())
    tools = _tools(_state(), _config(corpus=broken, connector=_Answering()))
    out, update = _call(tools["run_query"], sql="SELECT id FROM customers")

    rows = list((update.get("attempts_by_call") or {}).values())
    assert rows, (
        f"the checker raised and nothing was recorded: {out!r}. An empty ledger is what stamp "
        "reads as 'answered from context', so this turn would be quotable and wrong."
    )
    assert rows[0]["reason_code"] == GUARDRAIL_ERROR
    assert rows[0]["passed"] is False
    assert rows[0]["executed_sql"] is None, "nothing was executed; no statement may be claimed"

    execution = execution_from_attempts(rows)
    assert execution["guardrail_errors"] == 1, (
        "the failure is not countable, so the `guardrail_errors == 0` quotability gate cannot "
        "see it"
    )


def test_ask_user_rejects_a_schema_term_leak_before_pausing() -> None:
    """Gap 2 (utku-ai-deployment-targets.md): a dotted `table.column` reference
    in `question`/`why` is rejected before `ask_user` ever calls `interrupt` --
    checked via direct tool invocation (`_call`), which would surface an
    unhandled `GraphInterrupt` if the rejection didn't short-circuit before it.
    """
    tools = _tools()
    text, update = _call(
        tools["ask_user"],
        question="does revenue mean payments.amount or line_items.unit_price?",
        basis="data_definition",
    )
    assert "rejected" in text
    assert "payments.amount" in text or "line_items.unit_price" in text
    assert "clarifications_by_call" not in update


def test_ask_user_rejects_a_leak_in_why_too() -> None:
    tools = _tools()
    text, _update = _call(
        tools["ask_user"],
        question="How should we handle cancelled orders?",
        why="the amount could come from pct_delivered",
        basis="data_definition",
    )
    assert "rejected" in text
    assert "pct_delivered" in text


def test_ask_user_requires_a_basis_of_exactly_two_kinds() -> None:
    """Phase 1 (this initiative): the model must self-report which of two ambiguity
    kinds triggered ``ask_user``, so a later phase can route a data-definition answer
    into the shared corpus while keeping a ranking/superlative answer turn-scoped only.

    ``basis`` has no default, so its absence must be visible in the tool's own schema
    (``required``) rather than discovered only when a call omits it and something downstream
    silently guesses. ``.args`` is ``StructuredTool``'s public view of its argument schema
    (``tool_call_schema.model_json_schema()`` under the hood); no test in this repo already
    asserts a tool's arg schema, so this is the standard LangChain property rather than a
    codebase-specific idiom.
    """
    tools = _tools()
    schema = tools["ask_user"].args
    assert schema["basis"]["enum"] == ["data_definition", "ranking_ambiguity"], schema
    assert "default" not in schema["basis"], (
        "basis must have no default -- the model has to state one every time"
    )
    required = tools["ask_user"].tool_call_schema.model_json_schema().get("required", [])
    assert "basis" in required, "basis must be required, not optional"


def test_state_assumption_records_plain_language_text() -> None:
    """Gap 1 (utku-ai-deployment-targets.md): the model's own self-reported
    assumption, distinct from `ask_user` (no interrupt, never pauses the turn)."""
    tools = _tools()
    text, update = _call(
        tools["state_assumption"], text="Excluded cancelled orders from the total."
    )
    assert text == "noted"
    assert list(update["assumptions_by_call"].values()) == [
        "Excluded cancelled orders from the total."
    ]


def test_state_assumption_rejects_a_schema_term_leak() -> None:
    tools = _tools()
    text, update = _call(
        tools["state_assumption"], text="Used payments.amount for the total."
    )
    assert "rejected" in text
    assert "payments.amount" in text
    assert "assumptions_by_call" not in update


def test_ask_user_interrupt_and_identity_resume() -> None:
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_user",
                        "args": {"question": "which year?", "basis": "data_definition"},
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


def test_state_assumption_reaches_the_final_answer_unconditionally() -> None:
    """Gap 1 end to end: a self-reported assumption survives agent_core -> stamp and
    lands on `answer["assumptions"]` — the exact field the UI must render
    unconditionally, not gated behind `graded`/`heuristic` the way `why` is."""
    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "state_assumption",
                        "args": {"text": "Excluded cancelled orders from the total."},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Total revenue is $18,496."),
        ]
    )
    graph = compile_graph()
    config = {
        "configurable": {
            "thread_id": "t-assumption",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
        }
    }
    turn = {
        "question": "revenue?",
        "thread_id": "t-assumption",
        "turn_index": 1,
        "turn_id": "turn-assumption",
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
        "clarifications": [],
    }
    out = graph.invoke(turn, config)
    assert out["answer"]["assumptions"] == ["Excluded cancelled orders from the total."]


def test_no_assumption_stated_is_a_real_empty_list_not_a_missing_field() -> None:
    model = ScriptedChatModel(responses=[AIMessage(content="Total revenue is $18,496.")])
    graph = compile_graph()
    config = {
        "configurable": {
            "thread_id": "t-no-assumption",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": model,
        }
    }
    turn = {
        "question": "revenue?",
        "thread_id": "t-no-assumption",
        "turn_index": 1,
        "turn_id": "turn-no-assumption",
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
        "clarifications": [],
    }
    out = graph.invoke(turn, config)
    assert out["answer"]["assumptions"] == []


def test_the_ledger_survives_the_interrupt() -> None:
    """A governed statement made **before** ``ask_user`` must still be in the record after.

    This is the property the whole ``Command``-into-agent-state move exists for, and it is the
    one the closures could not have. ``interrupt()`` aborts the outer node without committing
    its update, so on resume the node re-executes, ``build_tools`` builds fresh boxes, and the
    nested agent restores its *messages* from its own checkpoint rather than re-invoking the
    tools. Every ToolMessage was therefore present while every box that recorded what those
    calls did was empty — the turn reported ``terminal: "no_sql"`` with ``attempts: []``
    beside a populated ``generated_sql``, one row of the artifact contradicting itself.

    Order: ``run_query`` (a governed statement, recorded), then ``ask_user`` (the interrupt),
    then the answer. The assertion is on what the record says *after* the resume.
    """
    # A connector, because the statement has to reach `check()` for the assertion below to be
    # about the ledger. Until the 2026-08-10 audit (C2) this turn ran with none, and `fetch.py`
    # manufactured `refuse("r_not_a_read", "no connector configured")` for that — so "a governed
    # statement made before ask_user" was a fabricated refusal for a wiring failure. A missing
    # connector now raises, which is what surfaced it.
    class Answering:
        dialect = "postgres"

        def execute(self, sql: str, **_: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
            return (["id"], [(1,)], False)

    call = {"name": "run_query", "args": {"sql": "SELECT id FROM customers"}, "type": "tool_call"}
    model = ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{**call, "id": "rq-1"}]),
            AIMessage(content="", tool_calls=[
                {"name": "ask_user", "args": {"question": "which year?", "basis": "data_definition"},
                 "id": "c1", "type": "tool_call"},
            ]),
            AIMessage(content="ok: 2020"),
        ]
    )
    graph = compile_graph()
    token = "identity-ledger"
    config = {"configurable": {
        "thread_id": "t-ledger", "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": model, "assets_by_id": _assets(),
        "corpus": for_analyst(list(_assets().values())),
        "connector": Answering(),
    }}
    turn = {
        "question": "revenue?", "thread_id": "t-ledger", "turn_index": 1,
        "turn_id": "turn-ledger", "run_id": "r", "question_id": "q", "db_id": "sales",
        "attempt_id": "a", "corpus_content_hash": "c", "prompt_set_hash": "p",
        "knobs_resolved": {}, "n_re_served": 0, "licensed": ["sales.customers"],
        "facet_route_hits": [("facet_schema", "sales", 1.0)],
        "messages": [], "usage": [], "identity": {"token": token},
    }

    paused = graph.invoke(turn, config)
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"

    done = resume_clarification(graph, config=config, identity={"token": token}, answer="2020")
    execution = done["answer"]["record"]["execution"]
    attempts = list(execution.get("attempts") or ())

    assert attempts, (
        "the resumed turn records no attempt, though run_query was called before the "
        f"interrupt. terminal={execution.get('terminal')!r}, "
        f"generated_sql={done['answer']['record'].get('generated_sql')!r} -- a ledger that "
        "disagrees with the SQL field beside it."
    )
    assert execution.get("terminal") != "no_sql", (
        f"terminal={execution.get('terminal')!r} on a turn that attempted a statement"
    )
    # The ledger is checkpointed now, so its rows have to be serialisable *without* a
    # `default=str` escape hatch. They were not: `verdict_layer` held a `Layer` enum, and
    # LangGraph's serde said so out loud -- "Deserializing unregistered type
    # governed_bi.govern.layers.Layer from checkpoint. This will be blocked in a future
    # version." A row a future LangGraph refuses to load is a ledger that stops existing on
    # the resume path, which is the path it was moved into state to protect.
    import json

    json.dumps(attempts)  # raises TypeError on any non-JSON-native value

    clars = done.get("clarifications") or []
    assert [c.get("answer") for c in clars] == ["2020"], (
        f"the clarification is missing or duplicated: {clars}. It used to be recovered from "
        "the message pairs *and* re-injected as a human message, so one answer became two "
        "rows -- and the recovered one carried the current turn_id rather than its own."
    )


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
        },
        {},
    )
    assert bounds.may_read_body("s.t.extra")
    assert bounds.may_inspect_schema("s.t")


def test_sample_rows_asks_for_the_engines_spelling_in_the_right_schema() -> None:
    """The tool that could tell the analyst a column's value vocabulary never returned a row.

    Two independent halves, and it needed both to stay invisible. ``parent_table`` is a
    corpus **key**, so ``.split(".")[-1]`` yielded the slug ``Air_Carriers_66c534`` for a
    table whose engine spelling is ``Air Carriers`` (ADR 0008 D1). And the column's
    ``schema`` was read into a local and then dropped, so ``PostgresConnector`` fell back to
    its private ``schema="public"`` default. On a pooled 57-schema lake the result is
    ``FROM "public"."Air_Carriers_66c534"`` — 42P01 every time, surfaced as a tool error
    that no metric counted.
    """
    from governed_bi.serve.fetch import sample_rows
    from governed_bi.serve.tools import tool_bounds_from_state

    table = TableAsset(
        id="airline.Air_Carriers_66c534",
        schema="airline",
        physical_name="Air Carriers",
        summary="air carriers (Air Carriers): Code, Description",
        columns=("airline.Air_Carriers_66c534.Code",),
    )
    column = ColumnAsset(
        id="airline.Air_Carriers_66c534.Code",
        schema="airline",
        parent_table="airline.Air_Carriers_66c534",
        physical_name="Code",
        summary="Code — Air_Carriers_66c534.Code",
        physical_type="TEXT",
    )
    assets = {a.id: a for a in (table, column)}
    statements: list[str] = []

    class Recorder:
        dialect = "postgres"

        def execute(self, sql, **kwargs):
            statements.append(sql)
            return (["Code"], [("AA",), ("DL",)], False)

    state = _state(licensed=["airline.Air_Carriers_66c534"])
    state["retrieved"]["by_type"]["table"] = ["airline.Air_Carriers_66c534"]
    state["retrieved"]["selected"] = {
        "airline.Air_Carriers_66c534.Code": {
            "asset_id": "airline.Air_Carriers_66c534.Code",
            "asset_type": "column",
            "score": 1.0,
        }
    }
    payload, ok, attempt = sample_rows(
        "airline.Air_Carriers_66c534.Code",
        limit=5,
        bounds=tool_bounds_from_state(state, {}),
        assets=assets,
        connector=Recorder(),
        corpus=for_analyst([table, column]),
        policy=GovernancePolicy(),
    )

    assert ok, payload
    assert statements, "nothing reached the connector"
    sent = statements[0]
    assert '"airline"."Air Carriers"' in sent, (
        f"asked the engine for {sent!r}; the corpus key is not a relation"
    )
    assert '"Air Carriers"."Code"' in sent, sent
    assert '"AA"' in payload
    # The half the old test could not see: this is a governed executor path now.
    assert attempt is not None and attempt["path"] == "sample" and attempt["passed"]
    assert attempt["executed_sql"] == sent


def test_sample_rows_is_a_governed_executor_path() -> None:
    """``sample`` clears the layer stack and writes a ledger row, or it does not run.

    The path used to reach ``PostgresConnector.execute`` through
    ``connector.sample_values`` — the method ``ports.Connector`` reserves for
    ``govern.pipeline`` — with no PARSE, NO_WRITE, FUNCTIONS, BINDING, COLUMNS or TABLES
    layer and no ``attempt_record``. Two things followed, and this test pins both.

    **The bypass.** ``reliability.status is suspect`` columns stay in ``by_id``, and
    ``hard_block_suspect`` is enforced only inside ``check()``. So under one identical
    policy ``run_query`` refused a suspect column and ``sample_rows`` returned its real
    values. No attacker and no unusual configuration required.

    **The vacuous count.** With no ledger row on the path, ``guardrail_errors == 0`` and an
    empty attempt list were true of every value the tool ever showed the model.
    """
    from governed_bi.corpus.schema import Reliability, ReliabilityStatus
    from governed_bi.serve.fetch import sample_rows

    table = TableAsset(
        id="sales.customers",
        schema="sales",
        physical_name="customers",
        summary="customers table",
        columns=("sales.customers.ssn",),
    )
    suspect = ColumnAsset(
        id="sales.customers.ssn",
        schema="sales",
        parent_table="customers",
        physical_name="ssn",
        summary="national id — known unreliable",
        physical_type="TEXT",
        reliability=Reliability(status=ReliabilityStatus.suspect),
    )
    assets = {a.id: a for a in (table, suspect)}
    executed: list[str] = []

    class Recorder:
        dialect = "postgres"

        def execute(self, sql, **kwargs):
            executed.append(sql)
            return (["ssn"], [("123-45-6789",)], False)

    state = _state(licensed=["sales.customers"])
    state["retrieved"]["selected"] = {
        "sales.customers.ssn": {
            "asset_id": "sales.customers.ssn",
            "asset_type": "column",
            "score": 1.0,
        }
    }
    payload, ok, attempt = sample_rows(
        "sales.customers.ssn",
        limit=5,
        bounds=tool_bounds_from_state(state, {}),
        assets=assets,
        connector=Recorder(),
        corpus=for_analyst([table, suspect]),
        policy=GovernancePolicy(hard_block_suspect=True),
    )

    assert not ok
    assert not executed, f"a suspect column's values reached the engine: {executed}"
    assert "123-45-6789" not in payload
    assert attempt is not None
    assert attempt["path"] == "sample"
    assert attempt["passed"] is False
    assert attempt["reason_code"] == "r_column_suspect", attempt
    assert attempt["executed_sql"] is None


def test_the_sample_tool_writes_its_ledger_row_and_does_not_answer_the_turn() -> None:
    """Through the real tool adapter: the row is durable, and ``terminal`` stays ``no_sql``.

    Two halves that have to hold together. ``attempts_by_call`` is the channel the record is
    built from, so a governed statement missing from it is a statement the audit trail does not
    have. And ``terminal`` must not read a passing ``sample`` row as an answer: a turn that
    sampled a column and then answered from context produced no SQL, and both facts are true of
    it at once.
    """
    from governed_bi.serve.ledger import execution_from_attempts

    class Recorder:
        dialect = "postgres"

        def execute(self, sql, **kwargs):
            return (["name"], [("Ada",), ("Grace",)], False)

    tools = _tools(config=_config(connector=Recorder()))
    payload, update = _call(
        tools["sample_rows"], call_id="s-1", column_id="sales.customers.name", limit=3
    )

    assert '"Ada"' in payload, payload
    rows = list((update.get("attempts_by_call") or {}).values())
    assert len(rows) == 1, update
    assert rows[0]["path"] == "sample"
    assert rows[0]["passed"] is True
    assert rows[0]["executed_sql"]

    execution = execution_from_attempts(rows)
    assert execution["attempts"] == rows, "the row must stay in the ledger"
    assert execution["terminal"] == "no_sql", (
        "a passing sample row made the turn look answered; that is the "
        "crash-counted-as-refusal inversion arriving through a second executor path"
    )
    assert execution["guardrail_errors"] == 0


def test_the_model_cannot_widen_the_sample_row_bound() -> None:
    """``limit`` is a model-supplied argument and it is clamped from both ends.

    It used to be ``max(1, int(limit))`` — a ceiling-free row bound on a tool that grants
    privilege, which is the shape ``ToolBounds`` exists to prevent (ADR 0006 §8).
    """
    from governed_bi.serve.fetch import SAMPLE_ROWS_MAX_VALUES, distinct_values_statement

    class Recorder:
        dialect = "postgres"

        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql, **kwargs):
            self.sql.append(sql)
            return (["name"], [], False)

    connector = Recorder()
    tools = _tools(config=_config(connector=connector))
    _call(tools["sample_rows"], call_id="s-2", column_id="sales.customers.name", limit=10_000)

    ceiling = distinct_values_statement(
        schema="sales",
        table="customers",
        column="name",
        limit=SAMPLE_ROWS_MAX_VALUES,
        dialect="postgres",
    )
    assert connector.sql and connector.sql[0] == ceiling, connector.sql


def test_sample_rows_cannot_escape_its_identifier() -> None:
    """A ``physical_name`` holding a double quote names a column, not more SQL.

    ``physical_name`` is deliberately unconstrained in content — ``corpus/identity.slug``
    says it holds the engine's identifier "verbatim: any character, any case, any script",
    and ``corpus/validate.py`` validates only ``slug(physical_name)``, so ``problems_with()``
    raises no objection to the asset below. The old Postgres adapter interpolated it into
    ``f'SELECT DISTINCT "{column}" FROM …'`` with no quote-doubling, and the statement escaped
    its intended relation. No test covered identifier quoting on either adapter.
    """
    from governed_bi.serve.fetch import distinct_values_statement

    evil = 'x" FROM "pg_catalog"."pg_shadow" -- '
    sql = distinct_values_statement(
        schema="sales", table="customers", column=evil, limit=5, dialect="postgres"
    )
    assert "pg_shadow" in sql, "the fixture stopped exercising the escape"
    assert '"sales"."customers"' in sql
    # Every occurrence of the payload is inside one quoted identifier, so the only relation
    # named is the intended one.
    import sqlglot

    tree = sqlglot.parse_one(sql, dialect="postgres")
    tables = {t.sql(dialect="postgres") for t in tree.find_all(sqlglot.exp.Table)}
    assert tables == {'"sales"."customers"'}, tables
    columns = {c.name for c in tree.find_all(sqlglot.exp.Column)}
    assert columns == {evil}, columns


def test_only_one_clarification_may_be_outstanding_per_turn() -> None:
    """A second ``ask_user`` in one assistant message is refused, not queued.

    **The bug it closes.** LangGraph dispatches one ``Send`` per pending tool call, so two
    ``ask_user`` calls in one message both interrupt. The surfacing order is a race,
    ``_clarification`` returns the first interrupt, and ``Command(resume=...)`` always lands on
    the first tool call — so the user is shown "which region?", answers it, and the answer is
    recorded against, and handed to the model as, "which year?". The resume surface carries no
    way to say *which* question is being answered, so the fix has to be that only one is ever
    outstanding.

    Both calls share one ``build_tools`` closure, which is what makes a latch work. The check
    and the set have no ``await`` between them, so two concurrent ``Send``s cannot both pass.
    """
    import asyncio

    ask = _tools()["ask_user"]

    async def _both() -> tuple[Any, Any]:
        # `basis` is required in this fork; it routes the answer, not the pause.
        call = lambda q, c: ask.coroutine(  # noqa: E731
            question=q, runtime=_runtime(c), basis="data_definition"
        )
        first = asyncio.create_task(call("which region?", "c1"))
        second = asyncio.create_task(call("which year?", "c2"))
        done, pending = await asyncio.wait({first, second}, timeout=5)
        for task in pending:
            task.cancel()
        return done, {first: "first", second: "second"}

    done, _ = asyncio.run(_both())

    # Exactly one of the two returned. The other raised `GraphInterrupt` (it paused) or is still
    # pending; either way it did not produce a second question for the user to answer.
    replies = []
    for task in done:
        try:
            replies.append(task.result())
        except BaseException:  # noqa: BLE001 — GraphInterrupt is the paused call, not a failure
            pass
    assert len(replies) == 1, "exactly one ask_user should return a refusal without pausing"
    text = replies[0].update["messages"][0].content
    assert "one clarifying question" in text.lower()
    assert "already has one" in text.lower()
