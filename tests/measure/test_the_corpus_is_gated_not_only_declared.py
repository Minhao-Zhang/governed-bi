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
"""

from __future__ import annotations

from typing import Any

from governed_bi.measure import gates
from governed_bi.measure.population import Population
from governed_bi.register.record import GATE_CONDITIONS
from governed_bi.register.stages import Outcome


def _arm(values: list[Any], outcomes: list[str] | None = None) -> Population:
    outcomes = outcomes or [Outcome.answered.value] * len(values)
    return Population.of(
        "arm",
        [
            {"question_id": str(i), "corpus_content_hash": v, "outcome": o}
            for i, (v, o) in enumerate(zip(values, outcomes))
        ],
    )


def _verdict(values: list[Any], outcomes: list[str] | None = None) -> str:
    """Through the registry, not the private function.

    The first version of this file called ``gates._corpus_content_hash_gate`` directly, and
    swapping the registry entry for a weak stand-in left all seven tests green — the
    implementation was covered and the *wiring* was not. That is the defect shape the audit that
    produced this gate spent its time on, reproduced inside its own regression test.
    """
    return gates.GATE_IMPLEMENTATIONS["corpus_content_hash"](_arm(values, outcomes)).verdict.name


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


def test_a_clarification_does_not_count_as_missing_instrumentation() -> None:
    """``stamp`` writes the field and a turn paused on ``ask_user`` never reaches it.

    Every null row in the five instrumented artifacts is ``outcome: clarification`` (4 to 13 per
    arm). Judging those as missing instrumentation would fail every arm that ever asked a
    question — a gate nobody can keep green, which this repository treats as a preference rather
    than a gate. The first version of this gate did exactly that and failed all five.
    """
    verdict = _verdict(
        ["abc", "abc", None],
        [Outcome.answered.value, Outcome.answered.value, Outcome.clarification.value],
    )
    assert verdict == "passed", (
        "a clarification carries no corpus hash because it never reached stamp; failing on it "
        "makes the gate unkeepable"
    )


def test_an_arm_of_nothing_but_clarifications_is_not_a_pass() -> None:
    """The other side of that exclusion: restricting the denominator must not create a vacuous
    pass, which is the trap ``_facet_channels_gate`` documents for the same reason."""
    assert _verdict([None, None], [Outcome.clarification.value] * 2) == "cannot_evaluate"
