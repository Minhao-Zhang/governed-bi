"""Regression tests for the 2026-07-29 curator step-budget incident.

On a paid 55-schema run, 30 of 57 curator deep agents died with
``GraphRecursionError`` and every one of them reported its tool counts as
unmeasurable. Two separate defects produced that:

1. ``recursion_limit = max(max_agent_steps * 4, 100)`` pinned the real limit at
   100 super-steps for every ``max_agent_steps`` at or below its default of 25,
   so the knob the drivers told an operator to raise did nothing. 100 super-steps
   buys 33 sequential tool calls, against a prompt asking for 126-238.
2. ``agent.invoke`` returns the accumulated state only on success, so a crash
   left ``result=None`` and the trajectory was gone — no way to tell a starved
   agent from a looping one.

These tests pin the *properties*, not the digits: that the budget scales with the
work, that the arithmetic admits the calls it claims to, and that an exhausted run
is still measurable and still says what it spent its calls on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.curator.asset_bag import AssetBag
from governed_bi.curator.pipeline import (
    SUPER_STEPS_PER_TOOL_CALL,
    _invoke_agent,
    derive_step_budget,
    recursion_limit_for,
)


def _table(name: str, n_columns: int) -> TableAsset:
    return TableAsset(
        id=f"tbl_s_{name}",
        schema="s",
        physical_name=name,
        columns=[
            Column(
                physical_name=f"c{i}",
                physical_type="TEXT",
                logical_type=LogicalType.string,
                nullable=True,
                is_unique=False,
            )
            for i in range(n_columns)
        ],
    )


# --------------------------------------------------------------------------- #
# Budget arithmetic
# --------------------------------------------------------------------------- #


def test_budget_scales_with_the_work_not_a_constant():
    """The incident's signature was a cap rate flat across schema size — a 2-join
    schema capped as often as an 86-join one — which is what a constant budget
    against variable work looks like. A 73-table schema must get more than a
    3-table one."""
    tiny = derive_step_budget(n_tables=3, n_columns=25, n_pairs=49)
    median = derive_step_budget(n_tables=8, n_columns=74, n_pairs=86)
    widest = derive_step_budget(n_tables=73, n_columns=703, n_pairs=306)
    assert tiny < median < widest
    # And every one of them must beat the 33 calls the old constant 100-step
    # limit actually bought, or nothing has been fixed.
    assert tiny > 33


def test_recursion_limit_admits_the_tool_calls_it_promises():
    """The old formula's failure was arithmetic: it converted a tool-call figure
    into super-steps with the wrong rate and then floored it. Pin the round trip.

    Three super-steps per sequential call, not two: the deepagents loop is
    ``model -> TodoListMiddleware.after_model -> tools``.
    """
    for calls in (1, 10, 33, 104, 472):
        limit = recursion_limit_for(calls)
        assert limit >= SUPER_STEPS_PER_TOOL_CALL * calls
        # Slack for the one-off `before_agent` node and a final tool-free turn.
        assert limit > SUPER_STEPS_PER_TOOL_CALL * calls


def test_budget_is_monotonic_in_every_dimension():
    base = dict(n_tables=8, n_columns=74, n_pairs=86)
    for field in base:
        more = dict(base)
        more[field] = base[field] + 40
        assert derive_step_budget(**more) > derive_step_budget(**base), field


def test_old_formula_regression_no_floor_swallows_the_knob():
    """``max(steps * 4, 100)`` made every budget <= 25 identical. Two different
    budgets must now produce two different limits — that is the whole bug."""
    assert recursion_limit_for(5) != recursion_limit_for(25)
    assert recursion_limit_for(25) != recursion_limit_for(26)


# --------------------------------------------------------------------------- #
# An exhausted run stays measurable
# --------------------------------------------------------------------------- #


class _LoopingModel(GenericFakeChatModel):
    """Re-issues one identical ``read_corpus`` call forever — pure churn.

    This is the behaviour the artifact evidence was used to argue for and could
    not actually show. If the trace cannot distinguish it from a starved agent,
    the instrumentation has not been fixed.
    """

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_corpus",
                    "args": {"table": "orders"},
                    "id": f"call{len(messages)}",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kw):  # the fake ignores binding
        return self


@pytest.fixture
def looping_agent(tmp_path: Path):
    pytest.importorskip("deepagents")
    from governed_bi.curator.deep_agent import build_curator_agent

    bag = AssetBag.from_tables("s", [_table("orders", 3)])
    agent = build_curator_agent(
        _LoopingModel(messages=iter([])),
        connector=None,
        schema="s",
        bag=bag,
        run_dir=tmp_path,
    )
    return agent, tmp_path


def test_recursion_exhaustion_keeps_real_tool_counts(looping_agent):
    """Before: ``result=None`` on any crash, so counts were ``None`` and a
    half-authored corpus was reported as an untouched one for 13 of 55 schemas.
    After: the last streamed ``values`` chunk carries the messages, so the tally
    is real on the failure path."""
    agent, out = looping_agent
    result, counts, err = _invoke_agent(
        agent, user="curate", max_agent_steps=10, trace_path=out / "curator_trace.jsonl"
    )
    assert err is not None and "GraphRecursionError" in err
    assert counts["exhausted"] is True
    assert result is not None, "streaming must retain the state built before the raise"
    assert counts["read_total"] > 0, "counts must be real, not None and not zero"
    assert "unmeasured_reason" not in counts
    assert counts["n_super_steps"] > 0
    assert counts["recursion_limit"] == recursion_limit_for(10)


def test_exhausted_run_writes_a_trace_that_shows_the_loop(looping_agent):
    """The trace exists to answer "what did it loop on" — the one question the
    2026-07-29 artifacts could not answer, and which drove a wrong diagnosis."""
    agent, out = looping_agent
    _result, counts, _err = _invoke_agent(
        agent, user="curate", max_agent_steps=10, trace_path=out / "curator_trace.jsonl"
    )
    rows = [
        json.loads(line)
        for line in (out / "curator_trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows, "an exhausted run must still leave a trace"
    assert all(r["tool"] == "read_corpus" for r in rows)
    # Identical args => one digest => the loop is legible from counts alone.
    assert len({r["args_digest"] for r in rows}) == 1
    repeats = counts["repeats"]
    assert repeats["total"] == len(rows)
    assert repeats["distinct"] == 1
    assert repeats["max_repeat"] == repeats["total"]
    assert repeats["top_repeated"][0]["tool"] == "read_corpus"


def test_nothing_streamed_is_unmeasured_not_zero():
    """The distinction the ``_unmeasured_tool_counts`` guard exists to keep: a
    failure before the first super-step committed really is unknown, and must not
    be reported as a measured zero."""

    class _DeadAgent:
        def stream(self, payload, config=None, stream_mode=None):
            raise RuntimeError("died before yielding")

    result, counts, err = _invoke_agent(_DeadAgent(), user="curate", max_agent_steps=10)
    assert result is None
    assert err is not None and "died before yielding" in err
    assert counts["read_total"] is None
    assert counts["unmeasured_reason"]


def test_every_curator_trace_artifact_is_promoted_by_the_pooled_driver():
    """Coupling guard. The pooled driver promotes a fixed list of named sidecars out
    of the staging root and deletes the rest, so an artifact the curator writes but
    ``_SIDECARS`` does not name is written and then thrown away. That is how this
    trace was lost the first time it was added — and losing it recreates the exact
    2026-07-29 blind spot the trace exists to close."""
    import inspect

    from governed_bi.curator import pipeline
    from governed_bi.eval.run_datalake import _SIDECARS

    src = inspect.getsource(pipeline)
    written = {
        name for name in re.findall(r'out_root / "([^"]+\.jsonl)"', src)
    }
    assert written, "expected the pipeline to name its jsonl sidecars inline"
    missing = sorted(n for n in written if n not in _SIDECARS)
    assert not missing, (
        f"{missing} written by the curator but absent from _SIDECARS, so the pooled "
        f"driver deletes them with the staging tree"
    )
