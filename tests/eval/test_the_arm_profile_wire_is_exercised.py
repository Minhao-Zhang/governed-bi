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

import ast
import pathlib

import pytest

from governed_bi.eval.provenance import (
    arm_startup_refusal,
    derived_question_set,
    reconciliation_lines,
)
from governed_bi.register.arm_profiles import ArmProfile, arm_profile

DIGEST = "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5"
#: The three shipped arms' question set, in the ``question_subset`` knob's format. Measured off
#: ``runs/eval/proxy_v{3_fold,4,5}_*.jsonl`` on 2026-08-20 and equal to the digest of
#: ``BIRD-Data-Obfuscation@22fe2a6:eval_dataset/test_final.jsonl``'s 1 351 ids.
SUBSET = "1351:423a3f4b65fb"


def _profile(
    name: str = "v4", digest: str | None = DIGEST, subset: str | None = SUBSET
) -> ArmProfile:
    return ArmProfile(
        name=name,
        description="",
        treatment=frozenset({"prompt_set"}),
        corpus_content_hash=digest,
        question_subset=subset,
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
    # ``knobs_resolved`` carries the question set because a modern row does: the writer landed
    # 2026-08-12 and every row a run produces now records it. A row without one exercises the
    # artifact-level fallback instead, which is a different test below.
    knobs = {"knobs_resolved": {"question_subset": SUBSET}}
    rows = [{"corpus_content_hash": "deadbeef", **knobs} for _ in range(3)]
    rows.append({"corpus_content_hash": DIGEST, **knobs})

    lines = reconciliation_lines(rows, _profile())

    assert lines[0] == "arm v4: 3 row(s) contradict arms.toml"
    assert len(lines) == 2, "one line per distinct problem"
    assert "(3 rows)" in lines[1] and "deadbeef" in lines[1]


def test_an_artifact_that_agrees_is_reported_as_agreeing() -> None:
    row = {"corpus_content_hash": DIGEST, "knobs_resolved": {"question_subset": SUBSET}}

    lines = reconciliation_lines([row] * 2, _profile())

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
    rows = [
        {
            "corpus_content_hash": None,
            "outcome": "clarification",
            "knobs_resolved": {"question_subset": SUBSET},
        }
    ] * 4

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


# ── the artifact that predates the writer, which is all three published arms ───


def _legacy_rows(qids, subset_recorded=None):
    """Rows shaped like the seven ``proxy_*`` artifacts: a question id, no scope knobs."""
    knobs = {} if subset_recorded is None else {"question_subset": subset_recorded}
    return [
        {"corpus_content_hash": DIGEST, "question_id": qid, "knobs_resolved": dict(knobs)}
        for qid in qids
    ]


def test_an_artifact_with_no_recorded_question_set_is_checked_against_its_own_ids() -> None:
    """**The fork's method, encoded so nobody has to invent it twice.**

    ``scope_identity``'s writer landed 2026-08-12, so no row of the three published arms records
    a ``question_subset`` -- and identifying which questions they ran cost a downstream fork a
    schema-filtered count across four versions of ``BIRD-Data-Obfuscation``. It need not have:
    every row carries its own ``question_id``, so the set is *in* the artifact. ``reconcile``
    cannot make this call, because one row names one question; the artifact-level reader can.
    """
    qids = [f"train_{i}" for i in range(5)]
    subset = derived_question_set(_legacy_rows(qids))

    lines = reconciliation_lines(_legacy_rows(qids), _profile(subset=subset))

    assert lines[0] == "arm v4: every row agrees with the profile in arms.toml"
    assert len(lines) == 2 and "no row records question_subset" in lines[1], (
        "an agreeing derivation is a weaker claim than a recorded knob and must not print the "
        "same sentence"
    )


def test_a_legacy_artifact_holding_a_different_question_set_is_caught() -> None:
    """The 1 351-for-1 351 substitution, on an artifact that records no knob at all. This is the
    case that used to pass every gate: same n, same corpus digest, different population."""
    lines = reconciliation_lines(
        _legacy_rows([f"train_{i}" for i in range(5)]), _profile(subset="5:000000000000")
    )

    assert lines[0] == "arm v4: contradicts arms.toml", (
        "the header counts rows and this finding is about the artifact, so it must not read "
        "'0 row(s) contradict'"
    )
    assert any("derived from the question ids" in line for line in lines)


def test_a_recorded_question_set_is_preferred_over_the_derived_one() -> None:
    """A part-done run holds fewer ids than its population, so the derivation would disagree
    with the profile *correctly* and uselessly. The knob is what the run set out to cover, so
    when it is there it is the answer and the ids are not consulted."""
    rows = _legacy_rows([f"train_{i}" for i in range(5)], subset_recorded=SUBSET)

    assert reconciliation_lines(rows, _profile()) == [
        "arm v4: every row agrees with the profile in arms.toml"
    ]


def test_an_artifact_that_names_no_questions_at_all_cannot_be_reconciled() -> None:
    """``question_id`` is ``Absence.never`` in the record register, so this is not an artifact
    of measurement rows. Reporting agreement about it is the F1 sentence one field over."""
    lines = reconciliation_lines([{"corpus_content_hash": DIGEST}] * 2, _profile())

    assert not any("every row agrees" in line for line in lines)
    assert "cannot be shown to be the arm's question set" in lines[1]


def test_the_startup_check_refuses_an_arm_that_declares_no_question_set() -> None:
    """The second lock at the wire, before the first paid question."""
    refusal = arm_startup_refusal(_profile(subset=None), {"corpus_content_hash": DIGEST})

    assert refusal and "no question_subset" in refusal


def test_the_startup_check_refuses_a_session_on_the_wrong_question_set() -> None:
    """The driver's pre-flight passes a bare identity mapping with no ``knobs_resolved``, which
    is why ``reconcile`` accepts a top-level ``question_subset`` as well.

    **This was a stated gap until 2026-08-20 and is now wired.** The driver's call site sat
    before ``--dataset`` was loaded, so it passed the corpus alone and this branch was
    unreachable from there — the question-set check first fired at report time, over the
    finished artifact, which is after the money is spent. The call moved below ``covered_qids``
    and supplies both keys; :func:`test_the_driver_supplies_both_locks_to_the_pre_flight` is what
    keeps it there.
    """
    refusal = arm_startup_refusal(
        _profile(), {"corpus_content_hash": DIGEST, "question_subset": "1351:000000000000"}
    )

    assert refusal and "1351:000000000000" in refusal


def test_the_driver_supplies_both_locks_to_the_pre_flight() -> None:
    """The driver hands ``arm_startup_refusal`` both reconcilable identities, not one.

    **A source assertion because the branch cannot be reached any other way.** ``main`` gets here
    only after a corpus, a dataset, a database and four models are built, which is the same reason
    the two decisions were lifted into ``eval/provenance.py`` as pure functions (module docstring).
    The pure halves are covered above; what no other test can see is whether the *caller* still
    passes what it used to. It stopped passing the question set for the first eight days the field
    existed, and the cost of that regression is a paid run whose label is wrong -- caught at report
    time, after the money.

    Read as an AST rather than a regex on purpose: the mapping spans several lines and a
    ``re``-based check on it would break on reformatting and pass on a key that had been renamed.
    """
    driver = pathlib.Path(__file__).resolve().parents[2] / "tools" / "run_datalake_eval.py"
    tree = ast.parse(driver.read_text(encoding="utf-8"))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "arm_startup_refusal"
    ]
    assert len(calls) == 1, "one pre-flight, or two places disagree about what an arm is"

    identity = calls[0].args[1]
    assert isinstance(identity, ast.Dict), "the identity mapping is built at the call site"
    keys = {k.value for k in identity.keys if isinstance(k, ast.Constant)}
    assert keys == {"corpus_content_hash", "question_subset", "corpus_release"}, (
        f"the pre-flight compares {sorted(keys)}; an identity it does not pass is an identity "
        "nothing checks until the artifact is finished"
    )
    # No `knobs_resolved`, which is the shape `recorded_question_subset` falls through for. A
    # nested mapping here would send it down the knob branch and read `None` -- the pre-flight
    # would pass on every dataset and say nothing about it.
    #
    # `corpus_release` joined the set on 2026-08-23 and is flat for that reason. A real row carries
    # it inside the knob mapping, where `recorded_corpus_release` reads it from; this identity is
    # not a row, and nesting one key to be faithful to a row's shape would risk the defect above
    # for the sake of it.
    assert "knobs_resolved" not in keys
