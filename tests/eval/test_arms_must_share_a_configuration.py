"""The between-arm half of comparability, which had no wire.

``register/knobs.py`` declares 45 knobs ``Role.comparability`` and derives
``comparability_keys()`` / ``config_hash_keys()`` from them. As of 2026-08-11 neither had a
single production caller — every hit outside that module was a comment — and
``comparison_quotable`` was ``context_hashes_distinct`` plus *each arm's own* gates, where
``_knobs_resolved_gate`` reads ``resume_drift_keys()`` **within** one arm. Nothing compared arm
A's resolved knobs to arm B's, so two arms differing in ``chat_model`` or ``embedding_model``
were published as a clean delta provided each was internally homogeneous.

This is the between-arm twin of a defect this repository already fixed once. ``session.py``'s
``_resolved_knobs`` docstring records the within-arm version: ``UNSET`` knobs were *absent*
rather than null from all 8,106 rows of six arms, and "a key missing from every row compares
equal to itself and the drift gate passes on a configuration it never saw." Same mechanism, one
scope up.

The absent/``None`` distinction below is that fix's, kept: ``None`` is a recorded measurement
("this run had no calibrated value") and two arms may legitimately agree on it. A key **missing
from the mapping** is not a value, and must never read as agreement.
"""

from __future__ import annotations

from typing import Any

from governed_bi.eval.report import arm_population, comparison_quotable, knobs_comparable
from governed_bi.measure.gates import Verdict
from governed_bi.register.knobs import UNSET, defaults


def _recorded() -> dict[str, Any]:
    """What a real arm writes: every declared knob, ``UNSET`` flattened to ``None``.

    Built from the register rather than hand-listed, so a knob added later is covered here
    without an edit — a hand-written fixture would silently stop representing an arm.
    Mirrors ``serve/session.py::_resolved_knobs``, whose contract is "**No key is ever
    omitted.**"
    """
    return {k: (None if v is UNSET else v) for k, v in defaults().items()}


def _rows(n: int, *, knobs: dict[str, Any], hash_prefix: str) -> list[dict[str, Any]]:
    """``n`` graded turns. ``context_hash`` differs per question and per arm so the delivery
    gate passes — an arm that tripped *that* gate would pass these tests for the wrong reason."""
    return [
        {
            "question_id": f"q{i}",
            "correct": i % 2 == 0,
            "context_hash": f"{hash_prefix}-{i}",
            "knobs_resolved": dict(knobs),
            # Enough of a real row that every *other* gate passes. Without these the whole
            # comparison is unquotable for unrelated reasons, and `assert not ok` in the model
            # -swap test would hold whether or not the knob gate exists — a test passing for a
            # reason it does not name is the kind this repository counts as worse than absent.
            "corpus_content_hash": "corpus-abc",
            "crashed": False,
            "guardrail_error": False,
            "negative_failed_open": False,
            "facet_channels": {"entity": "ran"},
            "facet_degraded": 0,
        }
        for i in range(n)
    ]


def _pair(knobs_a: dict[str, Any], knobs_b: dict[str, Any], n: int = 40) -> tuple[Any, Any]:
    return (
        arm_population(_rows(n, knobs=knobs_a, hash_prefix="a"), label="a"),
        arm_population(_rows(n, knobs=knobs_b, hash_prefix="b"), label="b"),
    )


def test_a_model_swap_is_not_a_comparison() -> None:
    """The defect, in the form that would have shipped a wrong number.

    Mutation-verified 2026-08-11: drop the ``knobs_comparable`` call from
    ``comparison_quotable`` and this goes red.
    """
    a, b = _pair(_recorded(), {**_recorded(), "chat_model": "another-model"})
    ok, _results_a, _results_b, _ctx, knobs = comparison_quotable(a, b)

    assert knobs.verdict is Verdict.failed
    assert not ok, "two arms on different models were published as a comparison"
    assert "chat_model" in knobs.detail


def test_the_same_configuration_still_compares() -> None:
    """The fence. A gate that refused every pair would satisfy the test above and be useless."""
    a, b = _pair(_recorded(), _recorded())
    ok, _a, _b, _ctx, knobs = comparison_quotable(a, b)

    assert knobs.verdict is Verdict.passed, knobs.detail
    assert ok


def test_an_operational_or_scope_difference_is_not_fatal() -> None:
    """Only the comparability set may block.

    ``Role.operational`` is "recorded; difference does not invalidate a comparison" and
    ``Role.scope`` is "not a comparability key", both by their own definitions in
    ``register/knobs.py``. Two arms built from different commits are still a comparison.

    This test is what makes the key set load-bearing. An earlier version moved ``run_id``,
    which is **not a knob at all**, so the gate was free to read ``resume_drift_keys()`` — the
    superset, and the wrong question — with every test still green. Mutation-verified
    2026-08-11 in the corrected form: swapping ``comparability_keys()`` for
    ``resume_drift_keys()`` turns this red.
    """
    a, b = _pair(
        _recorded(),
        {
            **_recorded(),
            "git_sha": "beefbeef",          # operational
            "working_tree_dirty": True,     # operational
            "split": "dev",                 # scope
        },
    )
    _ok, _a, _b, _ctx, knobs = comparison_quotable(a, b)

    assert knobs.verdict is Verdict.passed, knobs.detail


def test_a_knob_missing_on_one_side_cannot_evaluate() -> None:
    """Absent on one side is a difference in what is *known*, not a difference in value."""
    thin = _recorded()
    del thin["embedding_model"]
    a, b = _pair(_recorded(), thin)
    ok, _a, _b, _ctx, knobs = comparison_quotable(a, b)

    assert knobs.verdict is Verdict.cannot_evaluate
    assert not ok
    assert "embedding_model" in knobs.detail


def test_a_knob_absent_from_both_arms_is_not_silently_equal() -> None:
    """The hole in its purest form: what ``row.get`` would have called agreement.

    Both arms omit the key, so ``get`` returns ``None`` on each side and ``==`` says they match
    — certifying a knob the gate never saw. That is how ``context_hash``'s ``'unknown'``
    sentinel used to pass (see that field's note in ``register/record.py``).
    """
    both_thin = _recorded()
    del both_thin["sqlglot_version"]
    a, b = _pair(both_thin, dict(both_thin))
    result = knobs_comparable(a, b)

    assert result.verdict is Verdict.cannot_evaluate
    assert "sqlglot_version" in result.detail


def test_none_on_both_sides_is_agreement_not_ignorance() -> None:
    """The other side of that line, and why the two cannot be collapsed.

    ``_resolved_knobs`` flattens ``UNSET`` to ``None`` deliberately: "this run had no calibrated
    value" is a measurement worth writing down. Two arms that both recorded it agree. Treating
    recorded-``None`` as unmeasured would make every real comparison ``cannot_evaluate`` —
    ``negative_tau`` is ``None`` on every turn the negative gate ships disabled.
    """
    recorded = _recorded()
    assert recorded["negative_tau"] is None, "fixture no longer represents a real arm"
    a, b = _pair(recorded, dict(recorded))

    assert knobs_comparable(a, b).verdict is Verdict.passed
