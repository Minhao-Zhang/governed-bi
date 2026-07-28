"""The canonical outcome/stage taxonomy.

The distinction these tests exist to protect is crash-vs-refusal. A run whose
crashes were counted as refusals had to be discarded, so every case below that
asserts ``Outcome.crashed`` is guarding a number, not a naming convention.
"""

from governed_bi.stages import (
    CRASH_REFUSED_BY,
    REFUSED_BY_TO_STAGE,
    Outcome,
    Stage,
    classify_outcome,
    classify_row,
)


def test_produced_sql_is_answered():
    outcome, stage, recognised = classify_outcome(generated_sql="SELECT 1")
    assert outcome is Outcome.answered
    assert stage is None
    assert recognised


def test_an_exception_outranks_any_refusal_metadata():
    # A turn that raised did not refuse, whatever else its metadata claims.
    outcome, stage, _ = classify_outcome(
        generated_sql=None, exception="boom", refused_by="refuse_gate"
    )
    assert outcome is Outcome.crashed
    assert stage is None


def test_model_error_is_a_crash_not_a_refusal():
    # The serve path stamps refused_by="model_error" when it catches an internal
    # exception and degrades to a refusal. Failing closed is right; scoring it as
    # a refusal is what let a NameError hide in the serve path.
    outcome, stage, recognised = classify_outcome(
        generated_sql=None, refused_by="model_error"
    )
    assert outcome is Outcome.crashed
    assert stage is Stage.agent_core
    assert recognised


def test_curated_refusal_is_a_refusal_at_the_gate():
    outcome, stage, _ = classify_outcome(generated_sql=None, refused_by="refuse_gate")
    assert outcome is Outcome.refused
    assert stage is Stage.refuse_gate


def test_exhaustion_is_capped_not_refused():
    outcome, stage, _ = classify_outcome(generated_sql=None, refused_by="exhausted")
    assert outcome is Outcome.capped
    assert stage is Stage.agent_core


def test_recursion_exhausted_flag_alone_is_capped():
    outcome, stage, _ = classify_outcome(
        generated_sql=None, refused_by=None, recursion_exhausted=True
    )
    assert outcome is Outcome.capped
    assert stage is Stage.agent_core


def test_declined_clarification_is_its_own_outcome():
    outcome, _, _ = classify_outcome(
        generated_sql=None, refused_by="clarification_declined"
    )
    assert outcome is Outcome.clarification


def test_unknown_refused_by_is_flagged_rather_than_bucketed():
    # refused_by is free text with no central declaration, so a typo must be
    # countable, not silently absorbed into a stage bucket nothing observed.
    outcome, stage, recognised = classify_outcome(
        generated_sql=None, refused_by="tpyo_gate"
    )
    assert outcome is Outcome.refused
    assert stage is None
    assert not recognised


def test_no_sql_and_no_reason_is_refused_with_no_stage():
    outcome, stage, recognised = classify_outcome(generated_sql=None)
    assert outcome is Outcome.refused
    assert stage is None
    assert recognised


def test_every_crash_refused_by_value_is_a_known_stage():
    # CRASH_REFUSED_BY and the stage table must not drift apart: a crash reason
    # with no stage would report a crash nobody can locate.
    assert CRASH_REFUSED_BY <= REFUSED_BY_TO_STAGE.keys()


# --------------------------------------------------------------------------- #
# Row classification (rows on disk, incl. rows written before this existed)
# --------------------------------------------------------------------------- #


def test_row_prefers_the_stamped_outcome():
    # A row scored by a newer classifier must not be re-derived by an older one.
    outcome, stage, _ = classify_row(
        {"outcome": "crashed", "failed_stage": "execute", "generated_sql": "SELECT 1"}
    )
    assert outcome is Outcome.crashed
    assert stage is Stage.execute


def test_row_ignores_an_unrecognised_stamped_outcome_and_infers():
    outcome, _, _ = classify_row(
        {"outcome": "something_new", "generated_sql": "SELECT 1"}
    )
    assert outcome is Outcome.answered


def test_legacy_row_with_grader_refusal_reads_as_refused():
    # "refusal" is the grader's own word for "no SQL", not an exception message.
    outcome, _, _ = classify_row(
        {"generated_sql": None, "error": "refusal", "refused_by": "no_coverage"}
    )
    assert outcome is Outcome.refused


def test_legacy_row_with_exception_text_reads_as_crashed():
    outcome, _, _ = classify_row(
        {"generated_sql": None, "error": "KeyError: 'schema'"}
    )
    assert outcome is Outcome.crashed


def test_grader_gradeability_errors_are_not_crashes():
    # missing_gold_hash / gold_unusable are grading gaps on a turn that really did
    # produce SQL. Reading them as crashes would blame the model for our data gap.
    for err in ("missing_gold_hash", "gold_unusable:missing_hash"):
        outcome, _, _ = classify_row({"generated_sql": "SELECT 1", "error": err})
        assert outcome is Outcome.answered, err


def test_exec_error_is_answered_not_crashed():
    # Model SQL that raises at grading time is a wrong answer (audit E4 contrast).
    outcome, _, _ = classify_row(
        {
            "generated_sql": "SELECT missing",
            "error": "exec_error:UndefinedColumn: no such column",
        }
    )
    assert outcome is Outcome.answered


def test_infra_error_is_a_crash_not_a_wrong_answer():
    # Timeouts / connection deaths / truncation share ``infra_error:`` so they
    # enter crash_rate and block quotability instead of silently moving EX.
    for err in (
        "infra_error:OperationalError: server closed the connection",
        "infra_error:QueryCanceled: canceling statement due to statement timeout",
        "infra_error:truncated: result exceeded row cap (200000 rows returned)",
    ):
        outcome, _, _ = classify_row({"generated_sql": "SELECT 1", "error": err})
        assert outcome is Outcome.crashed, err


def test_stage_and_outcome_are_plain_strings_for_json():
    # Both land in JSONL rows, so they must serialise without a custom encoder.
    import json

    blob = json.dumps({"outcome": Outcome.crashed, "stage": Stage.guardrail})
    assert json.loads(blob) == {"outcome": "crashed", "stage": "guardrail"}
