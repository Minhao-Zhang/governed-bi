"""A "percentage" question whose executed statement never scales by 100 is told so.

The unit rules come from the downstream fork ``utkuai/detentai-fork``
(``tests/serve/test_structured_check.py``), including its own regression: the first version of
the check matched only ``X * 100`` and fired on already-correct queries written ``100 * X``.

Three claims here are **ours** rather than the fork's, and each is the part a port gets wrong:

* the hint is computed from the **executed** statement, not from the tool argument;
* it never lands on a reply that no statement produced (the fork's function returns the hint
  when handed no SQL -- see ``serve/structured_check.py`` for why ours does not);
* the knob ships **ON**, where the fork ships it off behind an in-process-only HTTP toggle.
"""

from __future__ import annotations

import asyncio
from typing import Any

from governed_bi.corpus.analyst import for_analyst
from governed_bi.corpus.schema import ColumnAsset, TableAsset
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.knobs import comparability_keys, knob_default
from governed_bi.serve.structured_check import percentage_scale_suffix
from governed_bi.serve.tools import build_tools

PERCENT_QUESTION = "what percentage of customers are active?"


# ── the rule itself ────────────────────────────────────────────────────────────


def test_no_suffix_when_the_question_does_not_ask_for_a_percentage() -> None:
    assert percentage_scale_suffix("how many customers?", "SELECT COUNT(*) FROM t") == ""


def test_no_suffix_when_the_sql_scales_by_100_in_the_suffix_form() -> None:
    assert percentage_scale_suffix(PERCENT_QUESTION, "SELECT (a::float / b) * 100 AS pct FROM t") == ""


def test_no_suffix_when_the_sql_scales_by_100_in_the_prefix_form() -> None:
    """The fork's own regression, pinned: matching only ``X * 100`` fires on a correct query.

    A hint that contradicts a correct statement is worse than no hint, because the cheapest
    thing the model can do with it is "fix" what was already right.
    """
    assert percentage_scale_suffix(PERCENT_QUESTION, "SELECT 100 * (a::float / b) AS pct FROM t") == ""


def test_it_fires_on_an_unscaled_ratio() -> None:
    suffix = percentage_scale_suffix(PERCENT_QUESTION, "SELECT a::float / b AS ratio FROM t")
    assert "structured check" in suffix
    assert "PERCENTAGE" in suffix


def test_it_fires_on_the_percent_spelling_too() -> None:
    assert percentage_scale_suffix("what percent of orders are late?", "SELECT a::float / b FROM t") != ""


def test_no_suffix_when_the_question_is_absent() -> None:
    assert percentage_scale_suffix(None, "SELECT a / b FROM t") == ""


def test_no_suffix_when_no_statement_ran() -> None:
    """Our divergence from the fork, which returns the hint here.

    ``None`` reaches this function only from ``run_query``'s one call site, and only when
    ``executed_sql`` is ``None`` -- which means a governance refusal or a checker that broke
    before a verdict existed. There is no answer to have been mis-scaled, and the reply the
    model is holding is a verdict rather than a result.
    """
    assert percentage_scale_suffix(PERCENT_QUESTION, None) == ""
    assert percentage_scale_suffix(PERCENT_QUESTION, "") == ""


# ── the knob ───────────────────────────────────────────────────────────────────


def test_the_knob_ships_on_and_is_a_comparability_key() -> None:
    """Both halves, and the default is the interesting one.

    The fork registers this ``False`` and turns it on through ``POST /settings/toggles``, whose
    override its own handoff document records as in-process only with no environment path -- so
    every fresh server start switched it back off. A default-off knob with nothing to persist it
    is a feature nobody ever runs. It is a comparability key because the suffix is agent input:
    it can change which statement the turn ends on.
    """
    assert knob_default("enable_structured_percentage_check") is True
    assert "enable_structured_percentage_check" in comparability_keys()


# ── wired to the tool, on the executed statement ───────────────────────────────


class _EchoConnector:
    """A connector double. The property under test is the reply text, not what a database did."""

    dialect = "postgres"

    def execute(self, sql: str, max_rows: int | None = None) -> Any:
        return (["ratio"], [(0.5,)], False)


def _assets() -> dict[str, Any]:
    table = TableAsset(
        id="sales.customers",
        schema="sales",
        physical_name="customers",
        summary="customers table",
        body="Customer master for retail.",
        columns=("sales.customers.id",),
    )
    column = ColumnAsset(
        id="sales.customers.id",
        schema="sales",
        parent_table="customers",
        physical_name="id",
        summary="customer id",
        physical_type="INTEGER",
    )
    return {a.id: a for a in (table, column)}


def _runtime(call_id: str = "call-1") -> Any:
    """A hand-built ``ToolRuntime`` — see ``test_agent_tools_hitl.py::_runtime`` for why."""
    from langchain.tools import ToolRuntime

    return ToolRuntime(
        state={"attempts_by_call": {}},
        context=None,
        config={"configurable": {}},
        stream_writer=lambda _chunk: None,
        tool_call_id=call_id,
        store=None,
    )


def _query(sql: str, *, licensed: str = "sales.customers", **state_extra: Any) -> str:
    """``run_query``'s reply text for one statement, on a turn asking for a percentage."""
    assets = _assets()
    state: dict[str, Any] = {
        "question": PERCENT_QUESTION,
        "turn_id": "turn-pct",
        "turn_index": 1,
        "licensed": [licensed],
        "messages": [],
        "knobs_resolved": {},
    }
    state.update(state_extra)
    config = {
        "configurable": {
            "thread_id": "t-pct",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "assets_by_id": assets,
            "corpus": for_analyst(list(assets.values())),
            "connector": _EchoConnector(),
        }
    }
    tools = {t.name: t for t in build_tools(state, config)}
    command = asyncio.run(tools["run_query"].coroutine(sql=sql, runtime=_runtime()))
    return str(command.update["messages"][0].content)


def test_the_hint_reaches_the_model_on_an_unscaled_result() -> None:
    reply = _query("SELECT COUNT(*) * 1.0 / 2 AS ratio FROM sales.customers")
    assert reply.startswith("{"), f"the JSON payload must still lead the reply: {reply[:80]!r}"
    assert "[structured check]" in reply


def test_the_knob_switches_it_off_for_the_same_turn() -> None:
    """Reachable from the run, not only from the register (audit I10): a comparability knob
    that cannot change a result is a false claim about the run that published it."""
    reply = _query(
        "SELECT COUNT(*) * 1.0 / 2 AS ratio FROM sales.customers",
        knobs_resolved={"enable_structured_percentage_check": False},
    )
    assert "[structured check]" not in reply


def test_a_refusal_is_not_decorated_with_engine_advice() -> None:
    """No statement ran, so there is no result to be mis-scaled — and this reply is a verdict.

    Appending advice here would change the text of a governance refusal, which other code
    compares by identity (``_fetch`` against ``OUT_OF_SCOPE_MESSAGE``) and the audit surface
    shows verbatim.
    """
    reply = _query("SELECT 1.0 / 2 FROM sales.customers", licensed="sales.other")
    assert "refused" in reply.lower()
    assert "[structured check]" not in reply


def test_the_prefix_form_still_suppresses_the_hint_through_the_tool() -> None:
    """The fork's regression, end to end rather than only on the function."""
    assert "[structured check]" not in _query("SELECT 100 * (COUNT(*) * 1.0 / 2) AS pct FROM sales.customers")


def test_the_argument_and_the_executed_statement_are_two_different_strings() -> None:
    """Why the call site's choice of input is load-bearing rather than stylistic.

    ``govern.prepare`` rewrites what runs -- here by appending a row cap -- so "the SQL" is two
    values on this path. The check reads the executed one; reading the argument would produce a
    hint about a statement no database saw. No question this test can write makes the *choice*
    itself observable (both spellings carry the same ``* 100``), so what it pins is that the
    choice exists: the day these two stop differing, the comment at the call site explaining
    which one is read stops meaning anything.
    """
    assets = _assets()
    state: dict[str, Any] = {
        "question": PERCENT_QUESTION,
        "turn_id": "turn-pct",
        "turn_index": 1,
        "licensed": ["sales.customers"],
        "messages": [],
        "knobs_resolved": {},
    }
    config = {
        "configurable": {
            "thread_id": "t-pct",
            "policy": GovernancePolicy(guard_rules_enabled={}),
            "assets_by_id": assets,
            "corpus": for_analyst(list(assets.values())),
            "connector": _EchoConnector(),
        }
    }
    tools = {t.name: t for t in build_tools(state, config)}
    argument = "SELECT COUNT(*) * 1.0 / 2 AS ratio FROM sales.customers"
    command = asyncio.run(tools["run_query"].coroutine(sql=argument, runtime=_runtime()))
    executed = command.update["attempts_by_call"]["call-1"]["executed_sql"]
    assert executed and executed != argument, f"nothing rewrote the statement: {executed!r}"
