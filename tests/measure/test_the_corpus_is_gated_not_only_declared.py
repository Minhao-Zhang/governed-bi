"""The corpus is the treatment identity, so a gate has to read it.

``docs/measurement.md`` and ``record.py`` both say "the corpus is the treatment", and until the 2026-08-10
audit (D7) no gate read ``corpus_content_hash``. Two things were live at once: an arm whose rows
name no corpus passed all six gates, and two arms measured over *different* corpora also passed,
because nothing compared the field. The repository had already paid for this once —
``check_one_implementation`` carries a singleton declaration whose stated reason is v1's
``corpus_content_hash == "unknown"`` passing a comparability gate.

Measured against the seven artifacts on disk when the gate landed: the two runs the power analysis
designates as its **null replicate** carry the field as null on all 1351 rows and are now
``cannot_evaluate``; the five instrumented arms pass.

Extended 2026-08-20, when ``ask_user`` learned to fail closed. The gate excluded
``outcome: clarification`` from its denominator under a restriction labelled "reached stamp", and
a declined clarification now reaches ``stamp`` and carries the field — so the one row the gate was
written to catch was the one row it dropped. The exclusion reads a stamped field instead, and the
tests below fix both halves: the paused row still does not fail an arm, and the stamped one is
still counted.
"""

from __future__ import annotations

from typing import Any

from governed_bi.measure import gates
from governed_bi.measure.population import Population
from governed_bi.register.record import GATE_CONDITIONS, RECORD_REGISTER, Absence
from governed_bi.register.stages import (
    ATTEMPT_CAP_REFUSED_BY,
    Outcome,
    Stage,
    classify_outcome,
)


def _arm(
    values: list[Any],
    outcomes: list[str] | None = None,
    prompts: list[Any] | None = None,
) -> Population:
    """One arm. ``prompts`` is ``prompt_set_hash``, the *other* treatment identity.

    It defaults to a stamped value on every row, so a fixture that means "this turn never reached
    ``stamp``" has to say so by passing ``None`` — which is what the rows on disk look like, both
    identities null together. Defaulting the other way would let a row be paused by accident and
    silently leave the gate's denominator, which is the defect these tests were extended for.
    """
    outcomes = outcomes or [Outcome.answered.value] * len(values)
    prompts = prompts if prompts is not None else ["prompt-abc"] * len(values)
    return Population.of(
        "arm",
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


def _verdict(
    values: list[Any],
    outcomes: list[str] | None = None,
    prompts: list[Any] | None = None,
) -> str:
    """Through the registry, not the private function.

    The first version of this file called ``gates._corpus_content_hash_gate`` directly, and
    swapping the registry entry for a weak stand-in left all seven tests green — the
    implementation was covered and the *wiring* was not. That is the defect shape the audit that
    produced this gate spent its time on, reproduced inside its own regression test.
    """
    arm = _arm(values, outcomes, prompts)
    return gates.GATE_IMPLEMENTATIONS["corpus_content_hash"](arm).verdict.name


def test_the_gate_is_declared_in_the_register() -> None:
    """Declared and implemented are two halves; ``gates.py`` asserts the closure at import, and
    this asserts the declaration exists at all — the defect was a field with no gate."""
    assert "corpus_content_hash" in GATE_CONDITIONS
    assert "corpus_content_hash" in gates.GATE_IMPLEMENTATIONS


def test_one_corpus_named_on_every_finished_turn_passes() -> None:
    assert _verdict(["abc", "abc", "abc"]) == "passed"


def test_an_arm_that_names_no_corpus_cannot_be_evaluated() -> None:
    """The state of both runs of the designated null replicate, 1351/1351 rows."""
    assert _verdict([None, None, None]) == "cannot_evaluate"


def test_two_corpora_inside_one_arm_is_not_one_treatment() -> None:
    assert _verdict(["abc", "abc", "def"]) == "failed"


def test_partial_instrumentation_fails_rather_than_passing_on_the_rows_it_has() -> None:
    assert _verdict(["abc", None, "abc"]) == "failed"


def test_a_clarification_that_never_reached_stamp_is_not_missing_instrumentation() -> None:
    """``stamp`` writes the field and a turn paused on ``ask_user`` never reaches it.

    A paused row carries **neither** treatment identity, which is how the fixture says it is
    paused. Judging those as missing instrumentation would fail every arm that ever asked a
    question — a gate nobody can keep green, which this repository treats as a preference rather
    than a gate. The first version of this gate did exactly that and failed all five instrumented
    artifacts.
    """
    verdict = _verdict(
        ["abc", "abc", None],
        [Outcome.answered.value, Outcome.answered.value, Outcome.clarification.value],
        ["prompt-abc", "prompt-abc", None],
    )
    assert verdict == "passed", (
        "a turn that paused before stamp carries no corpus hash; failing on it makes the gate "
        "unkeepable"
    )


def test_an_arm_of_nothing_but_clarifications_is_not_a_pass() -> None:
    """The other side of that exclusion: restricting the denominator must not create a vacuous
    pass, which is the trap ``_facet_channels_gate`` documents for the same reason."""
    verdict = _verdict(
        [None, None],
        [Outcome.clarification.value] * 2,
        [None, None],
    )
    assert verdict == "cannot_evaluate"


def test_a_stamped_fail_closed_clarification_is_still_counted_by_the_corpus_gate() -> None:
    """The exclusion is "did not reach ``stamp``", not "outcome says clarification".

    ``ask_user`` now fails closed on a decline or a ranking cancel: the middleware ends the agent
    loop, the turn **reaches** ``stamp``, and it is stamped ``outcome: clarification`` with a full
    record. While the gate tested the outcome alone — under a restriction whose label was literally
    "reached stamp" — such a row was dropped from the population that checks the treatment
    identity. A row that reached ``stamp`` and named no corpus is exactly what this gate exists to
    fail, so dropping it is the gate excusing the one case it was written for.

    The fixture is the row that turn produces: a clarification carrying the prompt identity
    ``stamp`` wrote beside the corpus hash, and no corpus hash.
    """
    verdict = _verdict(
        ["abc", "abc", None],
        [Outcome.answered.value, Outcome.answered.value, Outcome.clarification.value],
        ["prompt-abc", "prompt-abc", "prompt-abc"],
    )
    assert verdict == "failed", (
        "a stamped clarification carries every field `stamp` writes, so a null corpus hash on it "
        "is missing instrumentation and not a paused turn"
    )


def test_a_stamped_fail_closed_clarification_that_names_its_corpus_passes() -> None:
    """The other half of the same row: kept in the denominator, and it satisfies the condition.

    Without this the fix above could be read as "clarifications now fail", which would be the
    unkeepable gate the paused case is excluded to avoid.
    """
    verdict = _verdict(
        ["abc", "abc", "abc"],
        [Outcome.answered.value, Outcome.answered.value, Outcome.clarification.value],
    )
    assert verdict == "passed"


def test_the_gate_rests_on_stamp_writing_both_treatment_identities() -> None:
    """The premise the exclusion is derived from, read from the register rather than restated.

    ``_paused_before_stamp`` treats a null ``prompt_set_hash`` as "this turn never reached
    ``stamp``". That is sound only while both identities are declared as written by ``stamp`` on
    every terminal path; if one is ever declared optional, the exclusion becomes a silent drop of
    exactly the rows the gate is for. ``gates.py`` asserts it at import, and this asserts the
    assertion is wired to the field the gate actually reads.
    """
    declared = {f.name: f for f in RECORD_REGISTER}
    for name in (gates._STAMP_WITNESS_FIELD, "corpus_content_hash"):
        assert declared[name].absence is Absence.never
        assert declared[name].owner is Stage.stamp


def test_a_declined_clarification_outranks_a_derived_guardrail_refusal() -> None:
    """The precedence ``classify_outcome`` documents, pinned where the gate can see it.

    A turn whose every ``run_query`` was refused and whose reader then declined a question
    carries both signals, and ``stamp`` hands both over. The reachable combination is
    ``refused_by="guardrail"`` — the summary ``_path_signals`` derives from "no attempt passed" —
    and the decline wins, because it is a decision something took on this turn. The two refusals
    that outrank it are our own bug and the cap. This lives beside the corpus gate's tests because
    the gate's population depends on which rows end up carrying ``clarification`` at all.
    """
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
