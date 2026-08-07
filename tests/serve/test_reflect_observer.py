"""``reflect`` is an observer, and these are the four properties that make that true.

It **costs the locked arm nothing** (default off, no event, no call), it **routes nothing**
(the update's key set), it **never sees gold** (structurally, not by convention), and it
**cannot fail a turn** (every exception becomes an unmeasured verdict carrying a class name).

Written as tests rather than left to the docstring because each one is a property that a later
edit would break silently: a knob read in the wrong order, one extra key in a return dict, a
convenience argument threaded from the eval harness, a bare ``raise``. None of those would
fail any other test in this suite.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from governed_bi.serve.nodes import reflect as reflect_mod
from governed_bi.serve.nodes.reflect import (
    reflect_brief,
    reflect_node,
    reflect_on,
    reflect_signals,
)
from governed_bi.serve.state import ServeState


class _Judge:
    """A model that returns one canned reply, or raises, and counts how often it was asked."""

    def __init__(self, reply: str | None = None, raises: Exception | None = None) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[Any] = []

    async def ainvoke(self, messages: Any, config: Any = None) -> AIMessage:
        self.calls.append(messages)
        if self.raises is not None:
            raise self.raises
        return AIMessage(content=self.reply or "")


def _answered_state(**overrides: Any) -> dict[str, Any]:
    """A turn that reached the agent and produced a statement."""
    state: dict[str, Any] = {
        "question": "how many customers are there",
        "generated_sql": 'SELECT count(*) FROM sales."kunden"',
        "result_table": {"columns": ["n"], "rows": [[9590]], "row_count": 1, "truncated": False},
        "licensed": ["sales.kunden", "sales.bestellungen"],
        "retrieved": {"lexical_coverage": 0.25},
        "delivery": {"evicted": {"tables_dropped": 2, "dropped_ids": ["sales.artikel"]}},
        "execution": {
            "attempts": [
                {"passed": False, "reason_code": "r_table_not_licensed", "path": "agent"},
                {"passed": True, "reason_code": "passed", "path": "agent"},
            ],
            "terminal": "answered",
            "guardrail_errors": 0,
        },
        "turn_index": 1,
        "turn_id": "turn-reflect",
        "path_kind": "answered",
        "knobs_resolved": {"reflect_enabled": True},
    }
    state.update(overrides)
    return state


# ── it costs the locked arm nothing ───────────────────────────────────────────


def test_the_shipped_default_reflects_on_nothing() -> None:
    """The knob is off, so the node returns before it reads the config, let alone a model.

    ``{}`` and not ``{"reflect_verdict": None}``: the channel keeps its reset value, and the
    record's null then means *reflection did not run* rather than *the judge had nothing to
    say*. Those are different facts and ``Absence.not_measured`` names the first one.
    """
    judge = _Judge(reply="VERDICT: wrong\nREASON: no")
    state = _answered_state(knobs_resolved={})
    update = asyncio.run(reflect_node(state, {"configurable": {"reflect_model": judge}}))
    assert update == {}
    assert judge.calls == [], "a disabled observer called a model"


def test_a_turn_with_no_reflect_model_is_not_a_turn_with_an_unfavourable_verdict() -> None:
    update = asyncio.run(reflect_node(_answered_state(), {"configurable": {}}))
    assert update == {}


@pytest.mark.parametrize("path_kind", ["refuse", "decline", "crashed"])
def test_a_terminal_turn_is_not_billed_for_an_opinion(path_kind: str) -> None:
    """The guard ``route_node``'s docstring exists for: a crashed turn used to reach a full
    billed model call before ``stamp`` recorded the crash that had already happened."""
    judge = _Judge(reply="VERDICT: wrong\nREASON: no")
    state = _answered_state(path_kind=path_kind)
    update = asyncio.run(reflect_node(state, {"configurable": {"reflect_model": judge}}))
    assert update == {}
    assert judge.calls == []


def test_a_turn_that_produced_no_statement_has_nothing_to_judge() -> None:
    judge = _Judge(reply="VERDICT: wrong\nREASON: no")
    state = _answered_state(generated_sql=None, result_table=None)
    update = asyncio.run(reflect_node(state, {"configurable": {"reflect_model": judge}}))
    assert update == {}
    assert judge.calls == []


def test_the_default_arm_records_a_null_verdict_and_emits_no_reflect_row(
    two_schema_index: Any, two_schema_assets: Any
) -> None:
    """End to end on the real graph, with the shipped configuration.

    Two assertions, because the node could be inert in the record and still noisy on the wire:
    the UI timeline is an artifact too, and a disabled observer that adds a row to every turn
    has changed the arm.
    """
    from governed_bi.corpus.analyst import analyst_corpus_from_keys
    from governed_bi.govern.policy import GovernancePolicy
    from governed_bi.serve.graph import compile_graph
    from governed_bi.serve.scripted_model import ScriptedChatModel

    turn = {
        "question": "sensors voltage",
        "turn_index": 1,
        "thread_id": "t-reflect-default",
        "run_id": "run-reflect",
        "turn_id": "turn-reflect-default",
        "question_id": "q-1",
        "db_id": "ops_b",
        "attempt_id": "a-1",
        "corpus_content_hash": "corpus-x",
        "prompt_set_hash": "prompt-x",
        "knobs_resolved": {},
        "n_re_served": 0,
        "messages": [],
        "usage": [],
    }
    config = {
        "configurable": {
            "thread_id": "t-reflect-default",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "index": two_schema_index,
            "assets_by_id": two_schema_assets,
            "corpus": analyst_corpus_from_keys(allowed=("ops_b.sensors.voltage",)),
            "agent_model": ScriptedChatModel(responses=[AIMessage(content="one sensor")]),
        }
    }
    app = compile_graph()
    events = [chunk for chunk in app.stream(turn, config, stream_mode="custom")]
    steps = {e.get("step") for e in events if isinstance(e, dict)}
    assert "reflect" not in steps, f"a disabled observer put a row on the timeline: {sorted(steps)}"

    out = app.invoke({**turn, "turn_id": "turn-reflect-default-2"}, config)
    record = out["answer"]["record"]
    assert "reflect_verdict" in record, "the field must be written, as null, on every path"
    assert record["reflect_verdict"] is None


# ── it routes nothing ─────────────────────────────────────────────────────────


def test_the_update_carries_a_verdict_and_no_control_flow() -> None:
    """The key set *is* the invariant. ``path_kind`` would re-route the turn, ``terminal_reason``
    would relabel a decline, and ``answer`` is ``stamp``'s to write and nobody else's."""
    judge = _Judge(reply="VERDICT: wrong\nREASON: counts orders, not customers")
    update = asyncio.run(
        reflect_node(_answered_state(), {"configurable": {"reflect_model": judge}})
    )
    assert set(update) <= {"reflect_verdict", "usage"}, (
        f"reflect wrote {sorted(set(update) - {'reflect_verdict', 'usage'})}. It is an "
        "observer; the only key besides the verdict is the cost of the call it made."
    )
    assert update["reflect_verdict"]["verdict"] == "wrong"
    assert update["reflect_verdict"]["reason"] == "counts orders, not customers"


def test_no_edge_in_the_graph_reads_the_verdict() -> None:
    """The other half of the property above: writing no control-flow key is worthless if a
    conditional edge reads the channel it *does* write."""
    import inspect

    from governed_bi.serve import graph as graph_mod

    source = inspect.getsource(graph_mod)
    routers = source.split("def build_graph")[0]
    assert "reflect_verdict" not in routers, (
        "a routing function names reflect_verdict; the observer has become a decision"
    )


# ── it never sees gold ────────────────────────────────────────────────────────


def test_the_turn_state_has_no_gold_channel_at_all() -> None:
    """The structural half. Gold lives on ``eval/``'s question dict and never enters the graph,
    so there is no channel for a future edit to thread it through by accident."""
    named = [k for k in ServeState.__annotations__ if "gold" in k.lower()]
    assert named == [], f"ServeState declares {named}; gold is now one thread away from the judge"


def test_the_brief_cannot_carry_gold_even_when_the_state_does() -> None:
    """The behavioural half. Both readers work from a fixed key list, so a caller that stuffs
    gold into the state — an eval harness reusing a turn dict, say — cannot leak it."""
    poisoned = _answered_state(
        gold_sql="SELECT count(*) FROM sales.kunden WHERE aktiv",
        gold_fingerprint="deadbeef",
        gold_rows=[[42]],
        correct=False,
    )
    brief = reflect_brief(poisoned, reflect_signals(poisoned))
    for leak in ("gold", "deadbeef", "42", "aktiv"):
        assert leak not in brief, f"{leak!r} reached the judge's prompt:\n{brief}"


def test_no_string_the_module_executes_names_gold() -> None:
    """A source check over the strings the module *runs*, prose excluded.

    The two tests above pass a module that reads a gold key under a name they did not think
    of; this one fails the moment ``state.get("gold_...")`` is written, whatever it is called
    downstream. Docstrings are exempt because the docstrings have to be able to say what the
    rule is — that is where a reader learns it.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(reflect_mod))
    prose = {
        id(node.body[0].value)
        for node in [tree, *ast.walk(tree)]
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    named = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
        and "gold" in node.value.lower()
    ]
    assert named == [], f"serve/nodes/reflect.py executes a string naming gold: {named}"


# ── it cannot fail a turn ─────────────────────────────────────────────────────


def test_an_exception_becomes_an_unmeasured_verdict_carrying_only_the_class() -> None:
    """ADR 0006 §11: the exception's class, never its text. Provider and driver messages echo
    the statement and its literals — libpq prints ``LINE 1: SELECT ...`` — so a verdict built
    from ``str(exc)`` would put the SQL it was judging into the audit log by the back door."""
    secret = 'psycopg error near WHERE c."county" = \'ARECIBO\''
    judge = _Judge(raises=RuntimeError(secret))
    verdict, spent = asyncio.run(reflect_on(judge, _answered_state()))
    assert verdict["verdict"] is None
    assert verdict["why_unmeasured"] == "RuntimeError"
    assert spent is None
    assert "ARECIBO" not in repr(verdict)


def test_a_malformed_knob_cannot_crash_a_turn_that_already_answered() -> None:
    """``wrap_node`` turns any exception a node raises into ``path_kind: "crashed"``, so an
    observer that raises *is* a turn-ending observer. ``bool_knob`` raises on a value it cannot
    read — rightly, since coercing ``"no"`` to ``True`` would record the opposite of what ran —
    and the node has to absorb that rather than take the turn down with it."""
    judge = _Judge(reply="VERDICT: wrong\nREASON: no")
    state = _answered_state(knobs_resolved={"reflect_enabled": "yes please"})
    update = asyncio.run(reflect_node(state, {"configurable": {"reflect_model": judge}}))
    assert set(update) == {"reflect_verdict"}
    assert update["reflect_verdict"]["verdict"] is None
    assert update["reflect_verdict"]["why_unmeasured"] == "ValueError"
    assert "path_kind" not in update and "failure" not in update


def test_a_reply_naming_no_declared_verdict_is_unmeasured_rather_than_mapped() -> None:
    """A label the register does not declare is not a near-miss to be rounded to the nearest
    one. Mapping it would be the instrument inventing readings."""
    judge = _Judge(reply="VERDICT: probably fine\nREASON: looks plausible")
    verdict, _ = asyncio.run(reflect_on(judge, _answered_state()))
    assert verdict["verdict"] is None
    assert verdict["why_unmeasured"] == "reply named no declared verdict"


# ── the signals are what make it worth measuring ──────────────────────────────


def test_every_declared_signal_reaches_the_judge() -> None:
    """A judge shown only the SQL is the known-weak version of this instrument. These five are
    the engine's own recorded reasons an answer may be wrong."""
    signals = reflect_signals(_answered_state())
    assert signals["result_shape"] == {
        "row_count": 1, "n_columns": 1, "columns": ["n"], "truncated": False
    }
    assert signals["evicted"]["tables_dropped"] == 2
    assert signals["lexical_coverage"] == 0.25
    assert signals["n_licensed"] == 2
    # `bestellungen` is licensed and the statement never names it.
    assert signals["licensed_but_unreferenced"] == ["sales.bestellungen"]
    assert signals["attempts"] == {"n_attempts": 2, "n_passed": 1, "terminal": "answered"}


def test_a_clean_turn_carries_no_empty_signal_keys() -> None:
    """Absence is the signal. A ``lexical_coverage`` of ``None`` rendered as ``0`` would tell
    the judge the question's words are absent from the corpus when nothing measured them."""
    clean = _answered_state(delivery={}, retrieved={}, licensed=[])
    signals = reflect_signals(clean)
    assert "evicted" not in signals
    assert "lexical_coverage" not in signals
    assert "licensed_but_unreferenced" not in signals


def test_an_introspection_row_is_not_an_attempt_to_answer() -> None:
    """``sample`` runs ``SELECT DISTINCT`` to show the model what values look like. Counting it
    would tell the judge the turn tried harder than it did — the same filter every other reader
    of this ledger applies."""
    state = _answered_state(
        execution={
            "attempts": [
                {"passed": True, "reason_code": "passed", "path": "sample"},
                {"passed": False, "reason_code": "r_binding", "path": "agent"},
            ],
            "terminal": "refused",
            "guardrail_errors": 0,
        }
    )
    assert reflect_signals(state)["attempts"] == {
        "n_attempts": 1, "n_passed": 0, "terminal": "refused"
    }
