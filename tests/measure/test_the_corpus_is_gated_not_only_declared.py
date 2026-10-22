"""A config that names no corpus is not a pass, and a paused turn is not missing instrumentation.

A written decline that reached ``finish`` stays in the denominator.
"""

from __future__ import annotations

from typing import Any

from governed_bi.measure import checks
from governed_bi.measure.population import Population
from governed_bi.registry.record import CHECK_CONDITIONS, RECORD_REGISTER, Absence
from governed_bi.registry.stages import (
    ATTEMPT_CAP_REFUSED_BY,
    Outcome,
    Stage,
    classify_outcome,
)


def _config(
    values: list[Any],
    outcomes: list[str] | None = None,
    prompts: list[Any] | None = None,
) -> Population:
    """``prompts`` defaults to a written value. A paused turn has to pass ``None``, or it leaves the denominator by
    accident."""
    outcomes = outcomes or [Outcome.answered.value] * len(values)
    prompts = prompts if prompts is not None else ["prompt-abc"] * len(values)
    return Population.of(
        "config",
        [
            {
                "question_id": str(i),
                "corpus_content_hash": v,
                "outcome": o,
                "prompt_set_hash": p,
            }
            for i, (v, o, p) in enumerate(zip(values, outcomes, prompts))
        ],
    )


def _result(
    values: list[Any],
    outcomes: list[str] | None = None,
    prompts: list[Any] | None = None,
) -> str:
    """Through the registry, so a stand-in implementation cannot leave the tests green."""
    config = _config(values, outcomes, prompts)
    return checks.CHECK_IMPLEMENTATIONS["corpus_content_hash"](config).result.name


def test_the_check_is_declared_in_the_register() -> None:
    """Declared and implemented are two halves; ``checks.py`` asserts the closure at import, and
    this asserts the declaration exists at all — the defect was a field with no check."""
    assert "corpus_content_hash" in CHECK_CONDITIONS
    assert "corpus_content_hash" in checks.CHECK_IMPLEMENTATIONS


def test_one_corpus_named_on_every_finished_turn_passes() -> None:
    assert _result(["abc", "abc", "abc"]) == "passed"


def test_an_config_that_names_no_corpus_cannot_be_evaluated() -> None:
    """The state of both runs of the designated null replicate, 1351/1351 rows."""
    assert _result([None, None, None]) == "cannot_evaluate"


def test_two_corpora_inside_one_config_is_not_one_under_test() -> None:
    assert _result(["abc", "abc", "def"]) == "failed"


def test_partial_instrumentation_fails_rather_than_passing_on_the_rows_it_has() -> None:
    assert _result(["abc", None, "abc"]) == "failed"


def test_a_clarification_that_never_reached_finish_is_not_missing_instrumentation() -> None:
    """Judging a paused turn as missing instrumentation would fail every config that asked a question."""
    result = _result(
        ["abc", "abc", None],
        [Outcome.answered.value, Outcome.answered.value, Outcome.clarification.value],
        ["prompt-abc", "prompt-abc", None],
    )
    assert result == "passed", (
        "a turn that paused before finish carries no corpus hash; failing on it makes the check "
        "unkeepable"
    )


def test_an_config_of_nothing_but_clarifications_is_not_a_pass() -> None:
    """Restricting the denominator must not create a vacuous pass."""
    result = _result(
        [None, None],
        [Outcome.clarification.value] * 2,
        [None, None],
    )
    assert result == "cannot_evaluate"


def test_a_stamped_fail_closed_clarification_is_still_counted_by_the_corpus_check() -> None:
    """The exclusion is "did not reach finish", not "outcome says clarification"."""
    result = _result(
        ["abc", "abc", None],
        [Outcome.answered.value, Outcome.answered.value, Outcome.clarification.value],
        ["prompt-abc", "prompt-abc", "prompt-abc"],
    )
    assert result == "failed", (
        "a written clarification carries every field `finish` writes, so a null corpus hash on it "
        "is missing instrumentation and not a paused turn"
    )


def test_a_stamped_fail_closed_clarification_that_names_its_corpus_passes() -> None:
    """A written clarification that names its corpus stays in the denominator and passes."""
    result = _result(
        ["abc", "abc", "abc"],
        [Outcome.answered.value, Outcome.answered.value, Outcome.clarification.value],
    )
    assert result == "passed"


def test_the_check_rests_on_finish_writing_both_under_test_identities() -> None:
    """A null prompt hash means "never reached finish" only while both fields are written by ``finish``."""
    declared = {f.name: f for f in RECORD_REGISTER}
    for name in (checks._FINISH_EVIDENCE_FIELD, "corpus_content_hash"):
        assert declared[name].absence is Absence.never
        assert declared[name].owner is Stage.finish


def test_a_declined_clarification_outranks_a_derived_guardrail_refusal() -> None:
    """A decline outranks a derived guardrail refusal. A crash and the cap still outrank the decline."""
    both = dict(error=None, refused_by="guardrail", has_sql=False, clarification_requested=True)
    assert classify_outcome(**both) is Outcome.clarification
    assert (
        classify_outcome(**{**both, "refused_by": "guardrail_error"}) is Outcome.crashed
    ), "a swallowed exception inside check() is our bug and outranks any decline"
    assert (
        classify_outcome(**{**both, "refused_by": ATTEMPT_CAP_REFUSED_BY}) is Outcome.capped
    ), "the cap ended the loop before anything could be asked of a reader"
    assert (
        classify_outcome(error=None, refused_by="guardrail", has_sql=False) is Outcome.refused
    ), "with no decline the same signals are a refusal, so the branch above is what moved it"
