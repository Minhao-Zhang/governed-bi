"""The corpus is the setting under test identity, so a check has to read it.

``docs/measurement.md`` and ``record.py`` both say "the corpus is the setting under test", and until the 2026-08-10
audit (D7) no check read ``corpus_content_hash``. Two things were live at once: a config whose rows
name no corpus passed all six checks, and two configs measured over *different* corpora also passed,
because nothing compared the field. The repository had already paid for this once:
v1's ``corpus_content_hash == "unknown"`` passed a check that the runs share their settings.

Measured against the seven artifacts on disk when the check landed: the two runs the power analysis
designates as its **null replicate** carry the field as null on all 1351 rows and are now
``cannot_evaluate``; the five instrumented configs pass.

Extended 2026-08-20, when ``ask_user`` learned to fail closed. The check excluded
``outcome: clarification`` from its denominator under a restriction labelled "reached finish", and
a declined clarification now reaches ``finish`` and carries the field — so the one row the check was
written to catch was the one row it dropped. The exclusion reads a written field instead, and the
tests below fix both halves: the paused row still does not fail a config, and the written one is
still counted.
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
    """One config. ``prompts`` is ``prompt_set_hash``, the *other* setting under test identity.

    It defaults to a written value on every row, so a fixture that means "this turn never reached
    ``finish``" has to say so by passing ``None`` — which is what the rows on disk look like, both
    identities null together. Defaulting the other way would let a row be paused by accident and
    silently leave the check's denominator, which is the defect these tests were extended for.
    """
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
    """Through the registry, not the private function.

    The first version of this file called ``checks._corpus_content_hash_check`` directly, and
    swapping the registry entry for a weak stand-in left all seven tests green — the
    implementation was covered and the *wiring* was not. That is the defect shape the audit that
    produced this check spent its time on, reproduced inside its own regression test.
    """
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
    """``finish`` writes the field and a turn paused on ``ask_user`` never reaches it.

    A paused row carries **neither** setting under test identity, which is how the fixture says it is
    paused. Judging those as missing instrumentation would fail every config that ever asked a
    question — a check nobody can keep green, which this repository treats as a preference rather
    than a check. The first version of this check did exactly that and failed all five instrumented
    artifacts.
    """
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
    """The other side of that exclusion: restricting the denominator must not create a vacuous
    pass, which is the trap ``_channel_scoring_check`` documents for the same reason."""
    result = _result(
        [None, None],
        [Outcome.clarification.value] * 2,
        [None, None],
    )
    assert result == "cannot_evaluate"


def test_a_stamped_fail_closed_clarification_is_still_counted_by_the_corpus_check() -> None:
    """The exclusion is "did not reach ``finish``", not "outcome says clarification".

    ``ask_user`` now fails closed on a decline or a ranking cancel: the middleware ends the agent
    loop, the turn **reaches** ``finish``, and it is written ``outcome: clarification`` with a full
    record. While the check tested the outcome alone — under a restriction whose label was literally
    "reached finish" — such a row was dropped from the population that checks the setting under test
    identity. A row that reached ``finish`` and named no corpus is exactly what this check exists to
    fail, so dropping it is the check excusing the one case it was written for.

    The fixture is the row that turn produces: a clarification carrying the prompt identity
    ``finish`` wrote beside the corpus hash, and no corpus hash.
    """
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
    """The other half of the same row: kept in the denominator, and it satisfies the condition.

    Without this the fix above could be read as "clarifications now fail", which would be the
    unkeepable check the paused case is excluded to avoid.
    """
    result = _result(
        ["abc", "abc", "abc"],
        [Outcome.answered.value, Outcome.answered.value, Outcome.clarification.value],
    )
    assert result == "passed"


def test_the_check_rests_on_finish_writing_both_under_test_identities() -> None:
    """The premise the exclusion is derived from, read from the register rather than restated.

    ``_paused_before_finish`` treats a null ``prompt_set_hash`` as "this turn never reached
    ``finish``". That is sound only while both identities are declared as written by ``finish`` on
    every terminal path; if one is ever declared optional, the exclusion becomes a silent drop of
    exactly the rows the check is for. ``checks.py`` asserts it at import, and this asserts the
    assertion is wired to the field the check reads.
    """
    declared = {f.name: f for f in RECORD_REGISTER}
    for name in (checks._FINISH_EVIDENCE_FIELD, "corpus_content_hash"):
        assert declared[name].absence is Absence.never
        assert declared[name].owner is Stage.finish


def test_a_declined_clarification_outranks_a_derived_guardrail_refusal() -> None:
    """The precedence ``classify_outcome`` documents, pinned where the check can see it.

    A turn whose every ``run_query`` was refused and whose reader then declined a question
    carries both signals, and ``finish`` hands both over. The reachable combination is
    ``refused_by="guardrail"`` — the summary ``_path_signals`` derives from "no attempt passed" —
    and the decline wins, because it is a decision something took on this turn. The two refusals
    that outrank it are our own bug and the cap. This lives beside the corpus check's tests because
    the check's population depends on which rows end up carrying ``clarification`` at all.
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
