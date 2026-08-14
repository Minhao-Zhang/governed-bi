"""The wire that makes ``reconcile`` non-vacuous, driven without a database.

``reconcile`` spent its whole life with no caller but its own tests, and when a caller was
finally added the caller itself had none: ``_reconciliation`` and the ``--arm`` startup block
sat inside ``main``, past a corpus, a dataset, a database and four built models, so a grep of
``tests/`` found nothing that reached them. That is how a vacuous ``reconcile`` survived being
"fixed" — the arm the control is measured against declared no digest, the guard was skipped,
and the driver printed *arm v3_fold: every row agrees with the profile in arms.toml* about a
comparison it had not made.

So both decisions are pure functions in ``eval/provenance.py`` now, which is the shape
``append_refusal`` already established in that file and for the same stated reason: a branch
left in ``main`` is a branch no test can run.
"""

from __future__ import annotations

import pytest

from governed_bi.eval.provenance import arm_startup_refusal, reconciliation_lines
from governed_bi.register.arm_profiles import ArmProfile, arm_profile

DIGEST = "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5"


def _profile(name: str = "v4", digest: str | None = DIGEST) -> ArmProfile:
    return ArmProfile(
        name=name, description="", treatment=frozenset({"prompt_set"}), corpus_content_hash=digest
    )


# ── before the first paid question ────────────────────────────────────────────


def test_a_run_labelled_with_an_arm_it_did_not_measure_is_refused() -> None:
    """The check is worth something only here, before anything has been paid for."""
    refusal = arm_startup_refusal(_profile(), {"corpus_content_hash": "deadbeef"})

    assert refusal, "a session on the wrong corpus must not reach the first question"
    assert "deadbeef" in refusal and "--arm v4" in refusal
    assert "point --corpus-dir" in refusal, "the refusal has to say what to do about it"


def test_the_matching_corpus_passes_and_says_nothing() -> None:
    assert arm_startup_refusal(_profile(), {"corpus_content_hash": DIGEST}) is None


def test_the_startup_check_reads_the_session_the_way_it_reads_a_row() -> None:
    """``reconcile`` takes a mapping shaped like a measurement row, which is what lets the
    driver ask the same question of the session before any row exists. Asserted because a
    reader that insisted on a full row would make the pre-flight check unreachable."""
    assert arm_startup_refusal(_profile(), {}) is None, (
        "a session that has not stamped a corpus cannot contradict the profile"
    )


def test_the_startup_check_refuses_an_arm_that_cannot_be_reconciled() -> None:
    """The F1 failure, at the wire. A profile with no digest used to sail through here.

    Every shipped arm now declares one and the loader refuses a file that does not, so this
    can only be reached by a profile constructed in code — which is exactly where the last one
    came from.
    """
    refusal = arm_startup_refusal(_profile(digest=None), {"corpus_content_hash": DIGEST})

    assert refusal and "no corpus_content_hash" in refusal


# ── and again over the artifact, which a resume can make differ ───────────────


def test_the_report_names_each_disagreement_once_with_a_count() -> None:
    """1 351 copies of one sentence is a wall, not a finding."""
    rows = [{"corpus_content_hash": "deadbeef"} for _ in range(3)]
    rows.append({"corpus_content_hash": DIGEST})

    lines = reconciliation_lines(rows, _profile())

    assert lines[0] == "arm v4: 3 row(s) contradict arms.toml"
    assert len(lines) == 2, "one line per distinct problem"
    assert "(3 rows)" in lines[1] and "deadbeef" in lines[1]


def test_an_artifact_that_agrees_is_reported_as_agreeing() -> None:
    lines = reconciliation_lines([{"corpus_content_hash": DIGEST}] * 2, _profile())

    assert lines == ["arm v4: every row agrees with the profile in arms.toml"]


def test_an_unreconcilable_profile_cannot_produce_the_agreement_sentence() -> None:
    """**The sentence F1 is about.** A profile with nothing to compare must not print
    "every row agrees" — it examined nothing, and the reader cannot tell the two apart."""
    lines = reconciliation_lines([{"corpus_content_hash": DIGEST}] * 2, _profile(digest=None))

    assert not any("every row agrees" in line for line in lines)
    assert "contradict" in lines[0] and "no corpus_content_hash" in lines[1]


def test_a_clarification_that_ended_before_routing_is_not_a_contradiction() -> None:
    """open-work 3.6a: 4 rows on v4, 6 on v3-fold. Counting them would put a complaint into
    every report of a correct run, which is how a reader learns to skip the section."""
    rows = [{"corpus_content_hash": None, "outcome": "clarification"}] * 4

    assert reconciliation_lines(rows, _profile()) == [
        "arm v4: every row agrees with the profile in arms.toml"
    ]


# ── the shipped profiles, through the same wire ───────────────────────────────


@pytest.mark.parametrize("name", ["v3_fold", "v4", "v5"])
def test_every_declared_arm_is_checkable_through_the_driver_wire(name: str) -> None:
    """Not just constructible — reachable. ``v3_fold`` is the one that was not."""
    profile = arm_profile(name)

    assert arm_startup_refusal(profile, {"corpus_content_hash": DIGEST}) is None
    assert arm_startup_refusal(profile, {"corpus_content_hash": "deadbeef"})
    assert reconciliation_lines([{"corpus_content_hash": "deadbeef"}], profile)[0].endswith(
        "row(s) contradict arms.toml"
    )
