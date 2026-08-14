"""Two arms are a comparison when the declared treatment moved and nothing else did.

Neither half had a wire before 2026-08-11.

**Confounders.** ``register/knobs.py`` declares 47 knobs ``Role.comparability`` and derives
``comparability_keys()`` / ``config_hash_keys()`` from them. Neither function had a production
caller — every hit outside that module was a comment — and there is no ``config_hash`` at all.
``comparison_quotable`` was ``context_hashes_distinct`` plus *each arm's own* gates, and the one
gate reading knobs, ``_knobs_resolved_gate``, uses ``resume_drift_keys()`` **within** one arm. So
two arms differing in ``chat_model`` were published as a clean delta.

This is the between-arm twin of a defect already fixed once here: ``session.py``'s
``_resolved_knobs`` records that ``UNSET`` knobs were *absent* rather than null from all 9,457
rows of seven arms, and "a key missing from every row compares equal to itself and the drift gate
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

import pytest

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


def test_a_malformed_arms_file_is_a_loud_failure_and_not_an_undeclared_arm(
    tmp_path, monkeypatch
) -> None:
    """``summarise`` reads the treatment from ``arms.toml`` when the caller does not name one.

    ``_declared_treatment`` used to catch ``ValueError`` alongside ``KeyError``, so one typo in
    that file — a treatment naming something that is not a comparability knob, which the loader
    refuses the *whole file* for — silently un-declared **every** arm and turned each comparison
    into ``cannot_evaluate``. Nothing distinguishes that from "these two arms genuinely cannot be
    compared", so a broken register reads as a data problem and sends the reader to the
    artifacts.

    Driven through ``summarise`` rather than by calling the private helper, because the property
    is about what a *caller* sees (D25: when a test can reach the real path, reaching for the
    function instead is a different test).
    """
    from governed_bi.eval.report import summarise
    from governed_bi.register import arm_profiles

    broken = tmp_path / "arms.toml"
    broken.write_text('[arm.v4]\ntreatment = ["prompt_sett"]\n', encoding="utf-8")
    monkeypatch.setattr(arm_profiles, "ARMS_FILE", broken)
    arm_profiles.load_arm_profiles.cache_clear()
    try:
        with pytest.raises(ValueError, match="prompt_sett"):
            summarise(
                {
                    "v3_fold": _rows(2, knobs=_recorded(), hash_prefix="a"),
                    "v4": _rows(2, knobs=_recorded(prompt_set="p-b"), hash_prefix="b"),
                },
                pair=("v3_fold", "v4"),
            )
    finally:
        arm_profiles.load_arm_profiles.cache_clear()


def test_an_arm_with_no_profile_still_reads_as_nobody_said_what_changed(
    tmp_path, monkeypatch
) -> None:
    """The case that *must* stay a soft one, or the correction above would break every
    undeclared arm. A well-formed file that simply has no entry for this arm is ``KeyError``,
    which is exactly "nobody wrote a profile".

    "Well-formed" now includes a ``corpus_content_hash`` on every entry: an arm that declares
    none cannot be reconciled and the loader refuses the file for it, because the alternative
    was ``v3_fold`` silently exempting itself from the only check ``reconcile`` performs. The
    fixture carries one so that this test still exercises the *absent-entry* path and not the
    malformed-file path above it.
    """
    from governed_bi.eval.report import summarise
    from governed_bi.register import arm_profiles

    fine = tmp_path / "arms.toml"
    fine.write_text(
        '[arm.somebody_else]\ntreatment = ["prompt_set"]\ncorpus_content_hash = "abc123"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(arm_profiles, "ARMS_FILE", fine)
    arm_profiles.load_arm_profiles.cache_clear()
    try:
        summary = summarise(
            {
                "v3_fold": _rows(2, knobs=_recorded(), hash_prefix="a"),
                "v4": _rows(2, knobs=_recorded(prompt_set="p-b"), hash_prefix="b"),
            },
            pair=("v3_fold", "v4"),
        )
    finally:
        arm_profiles.load_arm_profiles.cache_clear()

    assert summary["comparison"]["treatment"] == []
    assert summary["comparison"]["quotable"] is False
