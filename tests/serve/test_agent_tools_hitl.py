"""Tool bounds, delivery hash, and ``ask_user`` resume. The latch tests stay here.
"""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import Command

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.bounds import OUT_OF_SCOPE_MESSAGE
from governed_bi.govern.layers import GUARDRAIL_ERROR
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.serve.agent_state import CAP_LEDGER_KEY
from governed_bi.serve.delivery import delivery_hash_for, payload_digest
from governed_bi.serve.events import tool_event_id
from governed_bi.serve.graph import compile_graph
from governed_bi.serve.resume import resume_clarification
from governed_bi.serve.scripted_model import ScriptedChatModel
from governed_bi.serve.tools import build_tools


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
        "retrieved_tables": ["sales.customers"],
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
        "settings_resolved": {},
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
    """A ``ToolRuntime`` built by hand, because a tool that takes one cannot be invoked alone."""
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
    """Run a tool's body and split its ``Command`` in two."""
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
    """The model receives the message; the corpus delivered nothing."""
    for tool_name, args in (
        ("read_body", {"asset_ids": ["nope"]}),
        ("inspect_schema", {"table_id": "other.table"}),
        ("sample_rows", {"column_id": "other.table.col"}),
    ):
        text, update = _call(_tools()[tool_name], **args)
        assert text == OUT_OF_SCOPE_MESSAGE
        assert "tool_delivered" not in update, f"{tool_name} recorded a refusal as a delivery"


def test_inspect_schema_retrieved_tables_succeeds() -> None:
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


def test_run_query_blocks_not_retrieved_table(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO customers VALUES (1, 'a')")
    conn.commit()
    conn.close()

    from governed_bi.datasource.sqlite import SqliteConnector

    connector = SqliteConnector(db)
    tools = _tools(_state(retrieved_tables=["sales.other"]), _config(connector=connector))
    out, update = _call(tools["run_query"], sql="SELECT id FROM customers")
    assert "refused" in out.lower() or "not" in out.lower()
    assert "attempt 1 of" in out, f"the model cannot see the budget: {out!r}"
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
    policy = GovernancePolicy(guard_rules_enabled={}, run_query_attempt_cap=2)
    tools = _tools(
        _state(retrieved_tables=["main.customers", "customers"]),
        _config(connector=connector, policy=policy),
    )
    # Force failures that still count as attempts. Distinct call ids, because the cap is now
    # counted over ids rather than over a list length — which is what makes it idempotent
    # under a replay instead of resetting on one.
    rows: dict[str, Any] = {}
    first_out = ""
    for i in range(2):
        out, update = _call(tools["run_query"], call_id=f"rq-{i}", sql="SELECT * FROM nope")
        if i == 0:
            first_out = out
        rows.update(update.get("attempts_by_call") or {})
    assert list(rows) == ["rq-0", "rq-1"], rows
    assert "attempt 1 of 2" in first_out, first_out

    capped, update = _call(tools["run_query"], call_id="rq-2", sql="SELECT * FROM nope")
    assert "capped" in capped.lower()
    assert "attempt 2 of 2" in capped, capped
    assert list(update.get("attempts_by_call") or {}) == [CAP_LEDGER_KEY], (
        "the cap must write its own ledger row. `_run_query` used to return on the cap "
        "*before* appending, so a capped turn carried an empty ledger while `generated_sql` "
        "was still read out of the tool arguments -- ExecutionRecord declared 'capped' and "
        "nothing ever wrote it. The key is a constant rather than `cap:<call_id>` so that "
        "the tool and the middleware that now ends the turn cannot write two of them."
    )


def test_a_replayed_run_query_does_not_consume_a_second_attempt_slot() -> None:
    """The cap counts governed statements, not tool invocations."""
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
    #     out.startswith("run_query") or "refused" in out.lower() or "error" in out.lower()
    # which the real refusal string `run_query refused: id binds to customers.id, which is not
    # allowed` also satisfies — so the test could not fail for the reason it exists (audit M4).
    assert "RuntimeError" in out, f"the driver's failure is not named: {out!r}"
    assert "attempt 1 of" in out, f"the model cannot see the budget: {out!r}"
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
    """Audit C1 — the worst measurement defect found, and the one with no coverage at all."""
    from governed_bi.corpus.analyst import AnalystCorpus
    from governed_bi.serve.ledger import execution_from_attempts

    # Constructed directly rather than through `analyst_corpus_from_keys`, which validates and
    # would raise here instead of inside `check()`. A corpus object that exists and holds a
    # malformed key is exactly the state C1 needs: the failure has to happen *inside the tool
    broken = AnalystCorpus({}, frozenset({"a.b.c.d"}), frozenset(), frozenset())
    tools = _tools(_state(), _config(corpus=broken, connector=_Answering()))
    out, update = _call(tools["run_query"], sql="SELECT id FROM customers")

    rows = list((update.get("attempts_by_call") or {}).values())
    assert rows, (
        f"the checker raised and nothing was recorded: {out!r}. An empty ledger is what finish "
        "reads as 'answered from context', so this turn would be quotable and wrong."
    )
    assert rows[0]["reason_code"] == GUARDRAIL_ERROR
    assert rows[0]["passed"] is False
    assert rows[0]["executed_sql"] is None, "nothing was executed; no statement may be claimed"

    execution = execution_from_attempts(rows)
    assert execution["guardrail_errors"] == 1, (
        "the failure is not countable, so the `guardrail_errors == 0` quotability check cannot "
        "see it"
    )


def test_ask_user_interrupt_and_resume() -> None:
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
        "settings_resolved": {},
        "n_re_served": 0,
        "channel_route_hits": [("channel_schema", "sales", 1.0)],
        "messages": [],
        "usage": [],
        "clarifications": [],
    }
    paused = graph.invoke(turn, config)
    assert paused.get("__interrupt__")

    done = resume_clarification(graph, config=config, answer="2020")
    assert done.get("path_kind") == "answered" or done.get("answer", {}).get(
        "outcome"
    ) in {"answered", "clarification", "no_sql"}
    clars = done.get("clarifications") or []
    assert any(c.get("answer") == "2020" for c in clars)
    # `no_sql`: the scripted model answers "ok: 2020" in prose and never calls `run_query`, so the
    # subject here is the resume, and what it must not be is `crashed` or a refusal.
    assert done["answer"]["outcome"] in {"answered", "clarification", "no_sql"}


def test_the_ledger_survives_the_interrupt() -> None:
    """A governed statement made **before** ``ask_user`` must still be in the record after."""
    # A connector, because the statement has to reach `check()` for the assertion below to be
    # manufactured `refuse("r_not_a_read", "no connector configured")` for that — so "a governed
    # statement made before ask_user" was a fabricated refusal for a wiring failure. A missing
    class Answering:
        dialect = "postgres"

        def execute(self, sql: str, **_: Any) -> tuple[list[str], list[tuple[Any, ...]], bool]:
            return (["id"], [(1,)], False)

    call = {"name": "run_query", "args": {"sql": "SELECT id FROM customers"}, "type": "tool_call"}
    model = ScriptedChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{**call, "id": "rq-1"}]),
            AIMessage(content="", tool_calls=[
                {"name": "ask_user", "args": {"question": "which year?", "basis": "data_definition"}, "id": "c1",
                 "type": "tool_call"},
            ]),
            AIMessage(content="ok: 2020"),
        ]
    )
    graph = compile_graph()
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
        "settings_resolved": {}, "n_re_served": 0, "retrieved_tables": ["sales.customers"],
        "channel_route_hits": [("channel_schema", "sales", 1.0)],
        "messages": [], "usage": [],
    }

    paused = graph.invoke(turn, config)
    assert paused.get("__interrupt__"), "precondition: ask_user paused the turn"

    done = resume_clarification(graph, config=config, answer="2020")
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
    # `default=str` escape hatch. They were not: `result_layer` held a `Layer` enum, and
    # LangGraph's serde said so out loud -- "Deserializing unregistered type
    import json

    json.dumps(attempts)  # raises TypeError on any non-JSON-native value

    clars = done.get("clarifications") or []
    assert [c.get("answer") for c in clars] == ["2020"], (
        f"the clarification is missing or duplicated: {clars}. It used to be recovered from "
        "the message pairs *and* re-injected as a human message, so one answer became two "
        "rows -- and the recovered one carried the current turn_id rather than its own."
    )



def test_only_one_clarification_may_be_outstanding_per_turn() -> None:
    """A second ``ask_user`` in one assistant message is refused, not queued."""
    import asyncio

    ask = _tools()["ask_user"]

    async def _both() -> tuple[Any, Any]:
        first = asyncio.create_task(
            ask.coroutine(
                question="which region?",
                basis="data_definition",
                runtime=_runtime("c1"),
            )
        )
        second = asyncio.create_task(
            ask.coroutine(
                question="which year?",
                basis="data_definition",
                runtime=_runtime("c2"),
            )
        )
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


def _clarify_turn(*, thread: str, turn: str) -> dict[str, Any]:
    """A turn shaped for the ``ask_user`` path — the keys ``test_ask_user_interrupt_and_resume`` uses."""
    return {
        "question": "revenue?", "thread_id": thread, "turn_index": 1, "turn_id": turn,
        "run_id": "r", "question_id": "q", "db_id": "sales", "attempt_id": "a",
        "corpus_content_hash": "c", "prompt_set_hash": "p", "settings_resolved": {},
        "n_re_served": 0, "channel_route_hits": [("channel_schema", "sales", 1.0)],
        "messages": [], "usage": [], "clarifications": [],
    }


def test_a_second_question_after_a_resume_is_not_refused_as_still_outstanding() -> None:
    """The one-outstanding-question latch has to be **given back** when the question is answered."""
    graph = compile_graph()
    config = {
        "configurable": {
            "thread_id": "t-two-q",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "agent_model": ScriptedChatModel(
                responses=[
                    AIMessage(content="", tool_calls=[
                        {"name": "ask_user", "args": {"question": "which year?", "basis": "data_definition"},
                         "id": "c1", "type": "tool_call"},
                    ]),
                    AIMessage(content="", tool_calls=[
                        {"name": "ask_user", "args": {"question": "which region?", "basis": "data_definition"},
                         "id": "c2", "type": "tool_call"},
                    ]),
                    AIMessage(content="ok: 2020 EMEA"),
                ]
            ),
        }
    }

    paused = graph.invoke(_clarify_turn(thread="t-two-q", turn="turn-two-q"), config)
    first = [i.value for i in (paused.get("__interrupt__") or ())]
    assert [v.get("question") for v in first] == ["which year?"], (
        f"precondition: the turn must pause on the first question, got {first}"
    )

    after = resume_clarification(graph, config=config, answer="2020")

    texts = [str(getattr(m, "content", "")) for m in (after.get("messages") or ())]
    assert not [t for t in texts if "already has one" in t.lower()], (
        "the second question was refused as still outstanding, though the first was answered in "
        f"the same pass. transcript: {texts}"
    )
    second = [i.value for i in (after.get("__interrupt__") or ())]
    assert [v.get("question") for v in second] == ["which region?"], (
        f"the second question did not pause the turn; interrupts={second}, transcript={texts}"
    )
    assert second[0]["clarification_id"] != first[0]["clarification_id"], (
        "the two questions must not share a clarification_id, or a resume cannot say which one "
        "it answers"
    )

    done = resume_clarification(graph, config=config, answer="EMEA")
    answers = [c.get("answer") for c in (done.get("clarifications") or ())]
    assert answers == ["2020", "EMEA"], (
        f"both answered clarifications must reach the record, got {answers}"
    )


def test_the_replayed_ask_user_start_reuses_the_row_id_rather_than_opening_a_second() -> None:
    """The resume replay re-emits ``start``."""
    graph = compile_graph()
    conf = {
        "thread_id": "t-replay",
        "policy": GovernancePolicy(guard_rules_enabled={}),
        "agent_model": ScriptedChatModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "ask_user",
                                                   "args": {"question": "which year?", "basis": "data_definition"},
                                                   "id": "c1", "type": "tool_call"}]),
                AIMessage(content="ok: 2020"),
            ]
        ),
    }

    def ask_user_events(payload: Any, configurable: dict[str, Any]) -> list[dict[str, Any]]:
        # ``subgraphs=True`` is not optional: the tools run inside the nested ``create_agent``
        # graph, and LangGraph does not propagate a nested writer to the parent stream without it.
        out: list[dict[str, Any]] = []
        for chunk in graph.stream(
            payload, {"configurable": configurable}, stream_mode="custom", subgraphs=True
        ):
            while isinstance(chunk, tuple) and chunk:
                chunk = chunk[-1]
            if isinstance(chunk, dict) and chunk.get("step") == "ask_user":
                out.append(chunk)
        return out

    paused = ask_user_events(
        _clarify_turn(thread="t-replay", turn="turn-replay"), conf
    )
    resumed = ask_user_events(Command(resume="2020"), conf)
    events = paused + resumed

    assert [e["status"] for e in events] == ["start", "start", "ok"], (
        "the ask_user row is start (pause), start (replay), then one resolve; got "
        f"{[(e['status'], e['seq']) for e in events]}"
    )
    assert {e["id"] for e in events} == {tool_event_id("ask_user", "c1")}, (
        f"the replayed start opened a second row: {sorted({e['id'] for e in events})}"
    )
    assert len({e["detail"]["clarification_id"] for e in events}) == 1, (
        "the replay must re-derive the same clarification_id, or the client's side table cannot "
        f"join onto the row: {[e['detail'] for e in events]}"
    )
    assert events[-1]["status"] != "start", "the resolve must arrive last, or the row spins"
