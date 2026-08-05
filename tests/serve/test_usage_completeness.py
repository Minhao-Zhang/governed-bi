"""Every model call a turn makes lands in ``usage``, attributed to the stage that made it.

**The defect.** ``usage`` was written by exactly one node, ``agent_core``, so the guard's
BI-scope gate and the five facet query rewriters spent tokens no record ever mentioned. Measured
on a real refusal, from ``runs/serve/``:

.. code-block:: text

    Q: 'hello'
      guard        = blocked, g_bi_scope, "model judged the question out of scope: 'no'"
      usage        = []
      cost_est_usd = None

LangSmith has that gate's call at 136 tokens. The engine's own ledger priced the turn at
nothing, and ``measure/price.py`` reads ``usage``, so every ``cost_est_usd`` in the repository
was low and refusals were free.

A count-based test would rot the moment a stage is added, so these assert the *rule*: a stage
that called a model has a row, and a stage that did not has none.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from governed_bi.govern.guard import BI_SCOPE_RULE_ID
from governed_bi.govern.policy import GovernancePolicy
from governed_bi.register.stages import FACET_STAGES, Stage
from governed_bi.serve.nodes.facets import _run_facet
from governed_bi.serve.nodes.guard import guard_node


class _Model:
    """Answers a fixed string and reports token counts, like a provider does."""

    def __init__(self, text: str = "yes") -> None:
        self.text = text
        self.calls = 0

    def invoke(self, messages: list[Any], config: Any = None, **kwargs: Any) -> Any:
        self.calls += 1
        reply = AIMessage(self.text)
        reply.usage_metadata = {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105}
        return reply


def _policy(*, scope_gate: bool) -> GovernancePolicy:
    return GovernancePolicy(guard_rules_enabled={BI_SCOPE_RULE_ID: scope_gate})


def _cfg(model: Any, **extra: Any) -> dict[str, Any]:
    return {"configurable": {"utility_model": model, **extra}}


def test_the_scope_gate_bills_the_turn_whether_it_clears_or_blocks() -> None:
    """Both outcomes cost the same call.

    A row attached to only one of them would make refusals look cheaper than they are, which is
    the exact direction the missing row was already wrong in — a refused turn recorded
    ``usage: []`` while its gate really ran.
    """
    for reply, expected in (("yes", "clear"), ("no", "blocked")):
        model = _Model(reply)
        out = guard_node(
            {"question": "how many restaurants?", "turn_index": 1},
            {"configurable": {"policy": _policy(scope_gate=True), "utility_model": model}},
        )
        assert out["guard"]["outcome"] == expected
        assert [row["stage"] for row in out["usage"]] == ["guard"]
        assert out["usage"][0]["input_tokens"] == 100
        assert out["usage"][0]["turn_index"] == 1


def test_a_gate_that_never_calls_a_model_bills_nothing() -> None:
    """"Spent nothing" and "spent and did not say" must stay different values.

    With the rule disabled there is no call, so there is no row — not a zero row. A written zero
    is this code's claim; ``measure/price.py`` prices it as free and cannot tell it from a real
    measurement.
    """
    model = _Model()
    out = guard_node(
        {"question": "how many restaurants?", "turn_index": 1},
        {"configurable": {"policy": _policy(scope_gate=False), "utility_model": model}},
    )
    assert "usage" not in out
    assert model.calls == 0


def test_every_facet_rewriter_bills_its_own_stage() -> None:
    """Five concurrent rewrites, five rows, each named for the facet that spent it.

    Attribution is the point rather than the total: the agent/utility split is a comparability
    knob (``llm_utility_model``) whose entire justification is cost and latency, and that cannot
    be argued from one number.
    """
    from governed_bi.retrieve.index import build_index

    index = build_index([])
    seen: list[str] = []
    for stage in FACET_STAGES:
        model = _Model("restaurants, dining establishments")
        out = _run_facet(
            {"question": "how many restaurants?", "turn_index": 1},
            _cfg(model, index=index),
            stage,
        )
        rows = out.get("usage") or []
        assert [row["stage"] for row in rows] == [stage.value], (
            f"{stage.value} must bill its own rewrite, got {[r.get('stage') for r in rows]}"
        )
        seen.append(stage.value)
    assert set(seen) == {s.value for s in FACET_STAGES}


def test_a_stage_name_in_a_usage_row_is_always_a_declared_stage() -> None:
    """The rows join to ``register/stages.py`` and not to strings written at each call site.

    A free-text stage label is how the nine failure vocabularies this repository replaced came
    about; a cost report that cannot be grouped by a known stage is a report nobody can total.
    """
    model = _Model()
    out = guard_node(
        {"question": "how many restaurants?", "turn_index": 1},
        {"configurable": {"policy": _policy(scope_gate=True), "utility_model": model}},
    )
    known = {stage.value for stage in Stage}
    assert all(row["stage"] in known for row in out["usage"])
