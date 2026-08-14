"""ADR 0013 §2's consumer, built rather than described.

**What the ADR claimed and what was there.** §2 argues that putting the abstention reasons in
``register/stages.py::REFUSED_BY_TO_STAGE`` — rather than in a private set beside the policy —
is what makes them readable, and names three readers: ``classify_outcome``, "the refusal
histogram", and ``eval/report.py``. Measured on 2026-08-12:

* ``classify_outcome`` never consults the table. Any truthy ``refused_by`` returns
  ``Outcome.refused``, so ``test_the_abstention_policy_is_declared``'s assertion on it holds for
  ``'banana_not_declared_anywhere'`` too — a test that could not fail for any member of a
  vocabulary is not evidence the vocabulary is read.
* The one histogram that existed, ``tools/datalake_report.py::_refusal_layers``, counts
  ``attempt.reason_code`` off the **ledger**. A withheld turn writes no ledger row at all — ADR
  0013's own acceptance criterion 3 — so the four abstention reasons were structurally
  invisible to it, and it has never touched ``REFUSED_BY_TO_STAGE``.
* ``eval/report.py`` had zero references to ``refused_by``, ``terminal_reason`` or the
  vocabulary.

:func:`~governed_bi.eval.report.refusal_histogram` is the reader. These tests are what stop the
sentence being true only because someone wrote it down.
"""

from __future__ import annotations

from typing import Any

from governed_bi.eval.report import refusal_histogram, summarise
from governed_bi.register.stages import ABSTENTION_REASONS, REFUSED_BY_TO_STAGE, Stage


def _row(qid: str, *, outcome: str = "refused", **extra: Any) -> dict[str, Any]:
    return {"question_id": qid, "outcome": outcome, "correct": False, "crashed": False, **extra}


def test_every_declared_abstention_reason_is_attributed_to_the_abstain_stage() -> None:
    """One refused row per reason, through the reader, counted under ``abstain``.

    This is the assertion the ADR's argument actually needs and the one
    ``classify_outcome(...) is Outcome.refused`` cannot make: it fails if a reason is dropped
    from ``REFUSED_BY_TO_STAGE``, if it is mapped to another stage, or if the reader stops
    consulting the table.
    """
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
    """What "closed" has to mean once artifacts exist.

    Two import-time guards keep the *declarations* in step with each other, and neither can see
    a node writing a string that is in no register — a value they never meet. Here it is counted
    by name, outside ``by_stage``, so a histogram that no longer adds up says which string is
    why. Crediting it to a stage would be exactly the misattribution ADR 0012 §3 split the
    TABLES layer to end, one level up.
    """
    hist = refusal_histogram(
        [
            _row("q1", terminal_reason="nothing_licensed"),
            _row("q2", terminal_reason="banana_not_declared_anywhere"),
        ]
    )
    assert hist["by_stage"] == {Stage.abstain.value: 1}
    assert hist["unattributed"] == {"banana_not_declared_anywhere": 1}
    assert sum(hist["by_stage"].values()) + sum(hist["unattributed"].values()) == 2


def test_only_refused_turns_are_counted_and_the_coarser_channel_is_the_fallback() -> None:
    """``Outcome`` keeps a crash, a cap and a clarification apart from a refusal, so the
    histogram does too — a count over every row would answer a different question from the one
    it is named for.

    ``terminal_reason`` wins over ``refused_by`` on a row carrying both: ``route`` and the
    abstention policy write the *rule* there, while ``refused_by`` names the stage and is
    coarser. One decision, counted once.
    """
    hist = refusal_histogram(
        [
            _row("q1", outcome="answered", terminal_reason="nothing_licensed"),
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
    """The whole table, not only the abstention half.

    ``REFUSED_BY_TO_STAGE`` is the inventory of legal ``refused_by`` values; a key nothing can
    attribute is the free-text refusal the table exists to replace, and until this reader landed
    the only thing checking any of it was two guards comparing declarations to declarations.
    """
    rows = [_row(f"q{i}", refused_by=key) for i, key in enumerate(sorted(REFUSED_BY_TO_STAGE))]
    hist = refusal_histogram(rows)
    assert hist["unattributed"] == {}
    assert sum(hist["by_stage"].values()) == len(REFUSED_BY_TO_STAGE)


def test_the_arm_summary_carries_the_histogram() -> None:
    """``summarise`` is what the drivers and the report read, so the wire ends there."""
    summary = summarise(
        {"v4": [_row("q1", terminal_reason="nothing_licensed"), _row("q2", outcome="answered")]}
    )
    assert summary["arms"]["v4"]["refusals"]["by_stage"] == {Stage.abstain.value: 1}
    assert summary["arms"]["v4"]["refusals"]["n_rows"] == 2
