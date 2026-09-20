"""``eval/shadow.py``: the gates prod enforces, replayed over an arm that ran without them.

The measured configuration is not the served one — ``tools/run_datalake_eval.py`` passes
``guard_rules_enabled={}`` and ``api/graph_app.py`` enables all six — and the resolution is to
keep the arm permissive and replay the gates afterwards, because enforcement destroys the
counterfactual a projection needs. The module docstring carries the argument; this file pins
the three properties that make the projection trustworthy rather than merely available:

* a gate whose inputs are missing **refuses** instead of reporting that it never fired,
* a turn that ended before a gate's stage is counted apart from a turn the gate cleared,
* an arm that actually enforced a gate cannot be projected for it at all.
"""

from __future__ import annotations

import hashlib

import pytest

from governed_bi.eval.shadow import (
    ABSTENTION,
    DETERMINISTIC_GUARD,
    Counterfactual,
    abstention_gate,
    bi_scope_gate,
    deterministic_guard_gate,
    prod_projection,
)
from governed_bi.measure.population import Population
from governed_bi.serve.context import EMPTY_CONTEXT

_RAN = {"lexical": "ran", "semantic": "ran", "extraction": "ran"}


def _row(qid: str, **over):
    row = {
        "question_id": qid,
        "outcome": "answered",
        "correct": True,
        "licensed": ["s.t"],
        "schemas": ["s"],
        "context_hash": hashlib.sha256(b"a real context block").hexdigest(),
        "facet_channels": {"facet_schema": dict(_RAN)},
        "context_evicted": None,
        "lexical_coverage": 0.8,
        "abstention": {"policy": "context_sufficiency_v1", "outcome": "disabled"},
    }
    row.update(over)
    return row


def _questions(*qids: str, text: str = "how many orders were there last month?"):
    return {qid: {"question_id": qid, "question": text, "evidence": ""} for qid in qids}


def test_a_gate_with_no_inputs_refuses_the_projection_rather_than_reporting_zero() -> None:
    """The distinction ``measure/gates.py`` spends a third verdict on, one layer out.

    An arm predating ``facet_channels`` cannot have the abstention policy replayed over it, and
    an empty ``fires`` would read identically to a policy that judged every turn and withheld
    none.
    """
    rows = [_row("q1", facet_channels=None), _row("q2", facet_channels=None)]
    gate = abstention_gate(rows)
    assert gate.unevaluable
    assert "predates the field" in gate.unevaluable

    with pytest.raises(ValueError, match="could not be evaluated"):
        prod_projection(Population.of("arm", rows), [gate])


def test_a_turn_that_ended_before_the_stage_is_not_a_turn_the_gate_cleared() -> None:
    """v4's four zero-licensed clarifications, in miniature.

    They carry no ``facet_channels`` because the fan-out never ran, and the row says so itself:
    ``abstention`` is ``null``, which the record register defines as "ended before the node".
    Counting them as refusals would put a number in the report for turns the gate never saw;
    counting them as an uninstrumented arm would refuse the whole projection.
    """
    rows = [_row("q1"), _row("q2", facet_channels=None, abstention=None, licensed=[])]
    gate = abstention_gate(rows)

    assert gate.unevaluable == ""
    assert gate.fires == frozenset()
    assert gate.not_reached == frozenset({"q2"})

    projection = prod_projection(Population.of("arm", rows), [gate])
    assert "ended before its stage" in "\n".join(projection.render())


def test_nothing_licensed_is_replayed_as_a_withhold() -> None:
    """The one rule of the four that fires on a real arm, so the replay must catch it."""
    rows = [_row("q1"), _row("q2", licensed=[])]
    gate = abstention_gate(rows)
    assert gate.fires == frozenset({"q2"})
    assert gate.tier is Counterfactual.replay


def test_an_empty_context_is_detected_through_the_hash_and_not_guessed() -> None:
    """The one policy input the row does not carry, recovered exactly rather than assumed."""
    digest = hashlib.sha256(EMPTY_CONTEXT.encode("utf-8")).hexdigest()
    gate = abstention_gate([_row("q1", context_hash=digest)])
    assert gate.fires == frozenset({"q1"})


def test_an_arm_that_enforced_the_policy_cannot_be_projected_for_it() -> None:
    """Enforcement already spent the counterfactual; there is nothing left to replay.

    This is the module's whole thesis applied to the one gate somebody might reach for
    ``--abstain`` on first.
    """
    rows = [_row("q1", abstention={"policy": "context_sufficiency_v1", "outcome": "answer"})]
    gate = abstention_gate(rows)
    assert gate.unevaluable
    assert "nothing to shadow" in gate.unevaluable


def test_the_guard_screens_the_question_and_not_the_question_plus_evidence() -> None:
    """What ``guard_node`` reads is ``state["question"]``; only ``agent_core`` composes the pair.

    A projection that screened the pair would over-count the gate against a system that does
    not screen it. ``g_length`` is the rule with a bound wide enough to show the difference.
    """
    from governed_bi.register.knobs import knob_default

    over_bound = "x" * (int(knob_default("g_length_max_chars")) + 10)
    rows = [_row("q1")]
    questions = {"q1": {"question": "how many orders?", "evidence": over_bound}}

    gate = deterministic_guard_gate(rows, questions)
    assert gate.fires == frozenset(), "evidence is not screened by the served path"

    blocked = deterministic_guard_gate(rows, {"q1": {"question": over_bound, "evidence": ""}})
    assert blocked.fires == frozenset({"q1"})


def test_an_unjudged_question_blocks_the_scope_gate_rather_than_clearing_it() -> None:
    """A probe that did not cover the arm must not read as a gate that found nothing."""
    rows = [_row("q1"), _row("q2")]
    gate = bi_scope_gate(rows, {"q1": "clear"})
    assert gate.unevaluable
    assert "an unjudged turn is not a cleared one" in gate.unevaluable


def test_a_scope_refusal_is_priced_in_right_answers_and_not_in_a_p_value() -> None:
    """The projection can only remove turns, so the finding is a count. See ``NestedPolicies``."""
    rows = [_row("q1"), _row("q2"), _row("q3", correct=False)]
    questions = _questions("q1", "q2", "q3")
    gates = [
        deterministic_guard_gate(rows, questions),
        abstention_gate(rows),
        bi_scope_gate(rows, {"q1": "blocked", "q2": "clear", "q3": "clear"}),
    ]
    projection = prod_projection(Population.of("arm", rows), gates)

    assert projection.nested.withheld == 1
    # q1 was correct and delivered; prod refuses it, so one right answer is lost.
    assert projection.nested.lost == 1
    assert projection.nested.is_decisive is False
    assert "No paired test is reported" in projection.nested.render()


def test_error_failed_open_is_not_a_refusal_and_not_hidden() -> None:
    """The served path lets it through, so the projection must too — and must still show it."""
    rows = [_row("q1")]
    gate = bi_scope_gate(rows, {"q1": "error_failed_open"})
    assert gate.fires == frozenset()
    assert gate.unevaluable == ""


def test_every_gate_id_is_distinct_and_names_its_rule() -> None:
    """One spelling per rule. Two spellings of one id is the 2026-09-18 review's §1.1."""
    from governed_bi.govern.policy import BI_SCOPE_RULE_ID
    from governed_bi.serve.abstention import ABSTENTION_POLICY

    from governed_bi.eval.shadow import BI_SCOPE_GATE

    assert BI_SCOPE_GATE.endswith(BI_SCOPE_RULE_ID)
    assert ABSTENTION.endswith(ABSTENTION_POLICY)
    assert len({ABSTENTION, DETERMINISTIC_GUARD, BI_SCOPE_GATE}) == 3
