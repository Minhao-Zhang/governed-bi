"""Two arms are a comparison when the declared treatment moved and nothing else did.

Neither half had a wire before 2026-08-11.

**Confounders.** ``register/knobs.py`` declares 45 knobs ``Role.comparability`` and derives
``comparability_keys()`` / ``config_hash_keys()`` from them. Neither function had a production
caller — every hit outside that module was a comment — and there is no ``config_hash`` at all.
``comparison_quotable`` was ``context_hashes_distinct`` plus *each arm's own* gates, and the one
gate reading knobs, ``_knobs_resolved_gate``, uses ``resume_drift_keys()`` **within** one arm. So
two arms differing in ``chat_model`` were published as a clean delta.

This is the between-arm twin of a defect already fixed once here: ``session.py``'s
``_resolved_knobs`` records that ``UNSET`` knobs were *absent* rather than null from all 8,106
rows of six arms, and "a key missing from every row compares equal to itself and the drift gate
passes on a configuration it never saw." Hence the absent-versus-``None`` care below — ``None``
is a recorded measurement two arms may agree on; a missing key is the arm declining to say.

**The treatment.** Audit D9: ``context_hash`` distinctness was standing in for "the treatment
changed" and actually measured retrieval nondeterminism, passing at 0.9993 on ``run1``/``run2``,
which differ only by a random seed. The fix that audit prescribes is to demote that gate to an
existence check and read the treatment from declared fields instead — which is
``knobs_comparable``'s ``treatment`` argument.

The first version of this file got the confounder half right and missed the treatment half
entirely, which made the gate refuse this repository's own headline comparison: v3-fold → v4 is
a prompt change and ``prompt_set`` is a comparability knob.
"""

from __future__ import annotations

from typing import Any

from governed_bi.eval.report import arm_population, comparison_quotable, knobs_comparable
from governed_bi.measure.gates import Verdict
from governed_bi.register.knobs import UNSET, defaults

#: The treatment in the fixtures below. A real one: v3-fold → v4 moved the prompt and nothing
#: else, and ``prompt_set`` is ``Role.comparability`` — which is exactly why the treatment has
#: to be declared rather than assumed to be whichever knobs happen to differ.
_TREATMENT = frozenset({"prompt_set"})


def _recorded(**overrides: Any) -> dict[str, Any]:
    """What a real arm writes: every declared knob, ``UNSET`` flattened to ``None``.

    Built from the register rather than hand-listed, so a knob added later is covered without
    an edit. Mirrors ``serve/session.py::_resolved_knobs``, whose contract is "**No key is ever
    omitted.**"
    """
    base = {k: (None if v is UNSET else v) for k, v in defaults().items()}
    base["prompt_set"] = "prompts-a"
    return {**base, **overrides}


def _rows(n: int, *, knobs: dict[str, Any], hash_prefix: str) -> list[dict[str, Any]]:
    """``n`` graded turns, carrying enough of a real row that every *unrelated* gate passes.

    Without the trailing fields the comparison is unquotable for reasons this file does not
    name, and ``assert not ok`` would hold whether or not the gate under test existed — a test
    passing for a reason it does not name is the failure mode this repository counts as worse
    than no test at all.
    """
    return [
        {
            "question_id": f"q{i}",
            "correct": i % 2 == 0,
            "context_hash": f"{hash_prefix}-{i}",
            "knobs_resolved": dict(knobs),
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


def _treated() -> tuple[Any, Any]:
    """A legitimate arm pair: the prompt moved, nothing else did."""
    return _pair(_recorded(), _recorded(prompt_set="prompts-b"))


# ── confounders ──────────────────────────────────────────────────────────────


def test_a_model_swap_is_not_a_comparison() -> None:
    """The defect, in the form that would have shipped a wrong number.

    Mutation-verified 2026-08-11: drop the ``knobs_comparable`` call from
    ``comparison_quotable`` and this goes red.
    """
    a, b = _pair(_recorded(), _recorded(prompt_set="prompts-b", chat_model="another-model"))
    ok, _ra, _rb, _ctx, knobs = comparison_quotable(a, b, treatment=_TREATMENT)

    assert knobs.verdict is Verdict.failed
    assert not ok, "two arms on different models were published as a comparison"
    assert "chat_model" in knobs.detail


def test_a_real_arm_pair_still_compares() -> None:
    """The fence. A gate refusing every pair would satisfy the test above and be useless.

    This is the shape of v3-fold → v4: one comparability knob moved, on purpose, and it is the
    declared treatment.
    """
    ok, _ra, _rb, _ctx, knobs = comparison_quotable(*_treated(), treatment=_TREATMENT)

    assert knobs.verdict is Verdict.passed, knobs.detail
    assert ok


def test_an_operational_or_scope_difference_is_not_fatal() -> None:
    """Only the comparability set may block.

    ``Role.operational`` is "recorded; difference does not invalidate a comparison" and
    ``Role.scope`` is "not a comparability key", both by their own definitions in
    ``register/knobs.py``.

    This test is what makes the key set load-bearing. An earlier version moved ``run_id``,
    which is **not a knob at all**, so the gate was free to read ``resume_drift_keys()`` — the
    superset, and the wrong question — with every test still green. Mutation-verified in the
    corrected form: swapping ``comparability_keys()`` for ``resume_drift_keys()`` turns this red.
    """
    a, b = _pair(
        _recorded(),
        _recorded(
            prompt_set="prompts-b",
            git_sha="beefbeef",          # operational
            working_tree_dirty=True,     # operational
            split="dev",                 # scope
        ),
    )
    _ok, _ra, _rb, _ctx, knobs = comparison_quotable(a, b, treatment=_TREATMENT)

    assert knobs.verdict is Verdict.passed, knobs.detail


def test_a_knob_missing_on_one_side_cannot_evaluate() -> None:
    """Absent on one side is a difference in what is *known*, not a difference in value."""
    thin = _recorded(prompt_set="prompts-b")
    del thin["embedding_model"]
    ok, _ra, _rb, _ctx, knobs = comparison_quotable(
        *_pair(_recorded(), thin), treatment=_TREATMENT
    )

    assert knobs.verdict is Verdict.cannot_evaluate
    assert not ok
    assert "embedding_model" in knobs.detail


def test_a_knob_absent_from_both_arms_is_not_silently_equal() -> None:
    """The hole in its purest form: what ``row.get`` would have called agreement.

    Both arms omit the key, so ``get`` returns ``None`` on each side and ``==`` says they match,
    certifying a knob the gate never saw. That is how ``context_hash``'s ``'unknown'`` sentinel
    used to pass — see that field's note in ``register/record.py``.
    """
    a_knobs, b_knobs = _recorded(), _recorded(prompt_set="prompts-b")
    del a_knobs["sqlglot_version"]
    del b_knobs["sqlglot_version"]
    result = knobs_comparable(*_pair(a_knobs, b_knobs), treatment=_TREATMENT)

    assert result.verdict is Verdict.cannot_evaluate
    assert "sqlglot_version" in result.detail


def test_none_on_both_sides_is_agreement_not_ignorance() -> None:
    """The other side of that line, and why the two cannot be collapsed.

    ``_resolved_knobs`` flattens ``UNSET`` to ``None`` deliberately: "this run had no calibrated
    value" is a measurement worth writing down. Treating recorded-``None`` as unmeasured would
    make every real comparison ``cannot_evaluate`` — ``negative_tau`` is ``None`` on every turn
    while the negative gate ships disabled.
    """
    assert _recorded()["negative_tau"] is None, "fixture no longer represents a real arm"

    assert knobs_comparable(*_treated(), treatment=_TREATMENT).verdict is Verdict.passed


# ── the treatment itself (audit D9) ──────────────────────────────────────────


def test_a_pair_whose_declared_treatment_did_not_move_is_a_replicate() -> None:
    """D9's fix, stated positively.

    Two arms identical on every comparability knob are a replicate, whatever their contexts
    look like. The old gate inferred "the treatment changed" from ``context_hash``
    distinctness, which is retrieval noise, and so said yes on a seed-only pair.
    """
    result = knobs_comparable(*_pair(_recorded(), _recorded()), treatment=_TREATMENT)

    assert result.verdict is Verdict.failed
    assert "replicate" in result.detail
    assert "prompt_set" in result.detail


def test_a_pair_with_no_declared_treatment_cannot_be_evaluated() -> None:
    """"Nobody said what changed" is not "nothing changed", and neither is a pass.

    Two identical arms, which is the shape of the seed-only null pair the D9 control uses:
    nothing in the declared configuration separates them, and no caller said what was supposed
    to. This is the honest verdict for that pair, and the reason the control can stop being an
    ``xfail``.

    With a knob differing and no treatment named the verdict is ``failed`` instead — everything
    is a confounder when nothing is declared — which the model-swap test already covers.
    """
    result = knobs_comparable(*_pair(_recorded(), _recorded()))

    assert result.verdict is Verdict.cannot_evaluate
    assert "no treatment declared" in result.detail


def test_a_treatment_that_is_not_a_comparability_knob_is_refused() -> None:
    """A category error, refused rather than quietly ignored.

    Silently dropping an unknown name would let a typo — ``prompt_sett`` — read as "no treatment
    declared" on one path and as a passing comparison on another.
    """
    result = knobs_comparable(*_treated(), treatment=frozenset({"git_sha"}))

    assert result.verdict is Verdict.cannot_evaluate
    assert "git_sha" in result.detail
