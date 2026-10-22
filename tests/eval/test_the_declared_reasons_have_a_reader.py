"""The refusal histogram reads the declared reasons. A truthy ``refused_by`` is not evidence the vocabulary is read. See
ADR 0013."""

from __future__ import annotations

from typing import Any

from governed_bi.eval.report import refusal_histogram, summarise
from governed_bi.registry.stages import ABSTENTION_REASONS, REFUSED_BY_TO_STAGE, Stage


def _row(qid: str, *, outcome: str = "refused", **extra: Any) -> dict[str, Any]:
    return {"question_id": qid, "outcome": outcome, "correct": False, "crashed": False, **extra}


def test_every_declared_abstention_reason_is_attributed_to_the_abstain_stage() -> None:
    """Fails if a reason leaves the table, moves stage, or the reader stops consulting the table."""
    rows = [
        _row(f"q{i}", terminal_reason=reason)
        for i, reason in enumerate(sorted(ABSTENTION_REASONS))
    ]
    hist = refusal_histogram(rows)

    assert hist["n_refused"] == len(ABSTENTION_REASONS)
    assert hist["by_reason"] == {reason: 1 for reason in sorted(ABSTENTION_REASONS)}
    assert hist["by_stage"] == {Stage.abstain.value: len(ABSTENTION_REASONS)}
    assert hist["unattributed"] == {}, (
        "a declared abstention reason was not attributable, so the vocabulary is decorative"
    )


def test_an_undeclared_reason_lands_in_its_own_bucket_and_not_in_a_stage() -> None:
    """An undeclared string is counted by name, outside the stages. Crediting it to a stage would misattribute it. See
    ADR 0013."""
    hist = refusal_histogram(
        [
            _row("q1", terminal_reason="nothing_retrieved"),
            _row("q2", terminal_reason="banana_not_declared_anywhere"),
        ]
    )
    assert hist["by_stage"] == {Stage.abstain.value: 1}
    assert hist["unattributed"] == {"banana_not_declared_anywhere": 1}
    assert sum(hist["by_stage"].values()) + sum(hist["unattributed"].values()) == 2


def test_only_refused_turns_are_counted_and_the_coarser_channel_is_the_fallback() -> None:
    """``terminal_reason`` wins over ``refused_by`` when both are set, so one decision is counted once."""
    hist = refusal_histogram(
        [
            _row("q1", outcome="answered", terminal_reason="nothing_retrieved"),
            _row("q2", outcome="crashed", refused_by="model_error"),
            _row("q3", outcome="capped", refused_by="attempt_cap"),
            _row("q4", refused_by="guardrail", terminal_reason="no_schema_matched"),
            _row("q5", refused_by="guard"),
            _row("q6"),
        ]
    )
    assert hist["n_rows"] == 6
    assert hist["n_refused"] == 3
    assert hist["by_reason"] == {"guard": 1, "no_schema_matched": 1}
    assert hist["by_stage"] == {Stage.guard.value: 1, Stage.route.value: 1}
    assert hist["no_reason"] == 1, "a refusal with no reason recorded must be visible as one"


def test_every_key_in_the_table_is_reachable_through_the_reader() -> None:
    """Every legal ``refused_by`` value is in the table. A key nothing can attribute is free text."""
    rows = [_row(f"q{i}", refused_by=key) for i, key in enumerate(sorted(REFUSED_BY_TO_STAGE))]
    hist = refusal_histogram(rows)
    assert hist["unattributed"] == {}
    assert sum(hist["by_stage"].values()) == len(REFUSED_BY_TO_STAGE)


def test_the_config_summary_carries_the_histogram() -> None:
    """``summarise`` is what the drivers and the report read, so the wire ends there."""
    summary = summarise(
        {"v4": [_row("q1", terminal_reason="nothing_retrieved"), _row("q2", outcome="answered")]}
    )
    assert summary["configs"]["v4"]["refusals"]["by_stage"] == {Stage.abstain.value: 1}
    assert summary["configs"]["v4"]["refusals"]["n_rows"] == 2
