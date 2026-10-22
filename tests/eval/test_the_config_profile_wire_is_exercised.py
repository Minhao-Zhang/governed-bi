"""Both decisions are pure functions so a test can reach them without a corpus, a database, or a model."""

from __future__ import annotations

import ast
import pathlib

import pytest

from governed_bi.eval.provenance import (
    config_startup_refusal,
    derived_question_set,
    reconciliation_lines,
)
from governed_bi.registry.config_profiles import ConfigProfile, config_profile

DIGEST = "86ed1dbfef8b325e188061229b665c4918ec8c86c65e39b619a5495b0abab6d5"
#: The shipped configs' question set, as ``question_subset`` records it.
SUBSET = "1351:423a3f4b65fb"


def _profile(
    name: str = "v4", digest: str | None = DIGEST, subset: str | None = SUBSET
) -> ConfigProfile:
    return ConfigProfile(
        name=name,
        description="",
        under_test=frozenset({"prompt_set"}),
        corpus_content_hash=digest,
        question_subset=subset,
    )


# ── before the first paid question ────────────────────────────────────────────


def test_a_run_labelled_with_an_config_it_did_not_measure_is_refused() -> None:
    """The check is worth something only here, before anything has been paid for."""
    refusal = config_startup_refusal(_profile(), {"corpus_content_hash": "deadbeef"})

    assert refusal, "a session on the wrong corpus must not reach the first question"
    assert "deadbeef" in refusal and "--config v4" in refusal
    assert "point --corpus-dir" in refusal, "the refusal has to say what to do about it"


def test_the_matching_corpus_passes_and_says_nothing() -> None:
    assert config_startup_refusal(_profile(), {"corpus_content_hash": DIGEST}) is None


def test_the_startup_check_reads_the_session_the_way_it_reads_a_row() -> None:
    """``reconcile`` takes a mapping shaped like a measurement row, which is what lets the
    driver ask the same question of the session before any row exists. Asserted because a
    reader that insisted on a full row would make the pre-flight check unreachable."""
    assert config_startup_refusal(_profile(), {}) is None, (
        "a session that has not written a corpus cannot contradict the profile"
    )


def test_the_startup_check_refuses_an_config_that_cannot_be_reconciled() -> None:
    """A profile with no digest must refuse. The loader now refuses that file; this reaches the check in code."""
    refusal = config_startup_refusal(_profile(digest=None), {"corpus_content_hash": DIGEST})

    assert refusal and "no corpus_content_hash" in refusal


# ── and again over the artifact, which a resume can make differ ───────────────


def test_the_report_names_each_disagreement_once_with_a_count() -> None:
    """1 351 copies of one sentence is a wall, not a finding."""
    # A row that records the question set is not the artifact-level fallback below.
    settings = {"settings_resolved": {"question_subset": SUBSET}}
    rows = [{"corpus_content_hash": "deadbeef", **settings} for _ in range(3)]
    rows.append({"corpus_content_hash": DIGEST, **settings})

    lines = reconciliation_lines(rows, _profile())

    assert lines[0] == "config v4: 3 row(s) contradict configs.toml"
    assert len(lines) == 2, "one line per distinct problem"
    assert "(3 rows)" in lines[1] and "deadbeef" in lines[1]


def test_an_artifact_that_agrees_is_reported_as_agreeing() -> None:
    row = {"corpus_content_hash": DIGEST, "settings_resolved": {"question_subset": SUBSET}}

    lines = reconciliation_lines([row] * 2, _profile())

    assert lines == ["config v4: every row agrees with the profile in configs.toml"]


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
            "settings_resolved": {"question_subset": SUBSET},
        }
    ] * 4

    assert reconciliation_lines(rows, _profile()) == [
        "config v4: every row agrees with the profile in configs.toml"
    ]


# ── the shipped profiles, through the same wire ───────────────────────────────


@pytest.mark.parametrize("name", ["v3_fold", "v4", "v5"])
def test_every_declared_config_is_checkable_through_the_driver_wire(name: str) -> None:
    """Not just constructible — reachable. ``v3_fold`` is the one that was not."""
    profile = config_profile(name)

    assert config_startup_refusal(profile, {"corpus_content_hash": DIGEST}) is None
    assert config_startup_refusal(profile, {"corpus_content_hash": "deadbeef"})
    assert reconciliation_lines([{"corpus_content_hash": "deadbeef"}], profile)[0].endswith(
        "row(s) contradict configs.toml"
    )


# ── the artifact that predates the writer, which is all three published configs ───


def _legacy_rows(qids, subset_recorded=None):
    """Rows shaped like the seven ``proxy_*`` artifacts: a question id, no scope settings."""
    settings = {} if subset_recorded is None else {"question_subset": subset_recorded}
    return [
        {"corpus_content_hash": DIGEST, "question_id": qid, "settings_resolved": dict(settings)}
        for qid in qids
    ]


def test_an_artifact_with_no_recorded_question_set_is_checked_against_its_own_ids() -> None:
    """The question ids are in the artifact. An agreement derived from them must not print the same sentence as a
    recorded set."""
    qids = [f"train_{i}" for i in range(5)]
    subset = derived_question_set(_legacy_rows(qids))

    lines = reconciliation_lines(_legacy_rows(qids), _profile(subset=subset))

    assert lines[0] == "config v4: every row agrees with the profile in configs.toml"
    assert len(lines) == 2 and "no row records question_subset" in lines[1], (
        "an agreeing derivation is a weaker claim than a recorded setting and must not print the "
        "same sentence"
    )


def test_a_legacy_artifact_holding_a_different_question_set_is_caught() -> None:
    """Same count and corpus digest with a different population must still contradict the profile."""
    lines = reconciliation_lines(
        _legacy_rows([f"train_{i}" for i in range(5)]), _profile(subset="5:000000000000")
    )

    assert lines[0] == "config v4: contradicts configs.toml", (
        "the header counts rows and this finding is about the artifact, so it must not read "
        "'0 row(s) contradict'"
    )
    assert any("derived from the question ids" in line for line in lines)


def test_a_recorded_question_set_is_preferred_over_the_derived_one() -> None:
    """A part-done run holds fewer ids than its population, so the derivation would disagree
    with the profile *correctly* and uselessly. The setting is what the run set out to cover, so
    when it is there it is the answer and the ids are not consulted."""
    rows = _legacy_rows([f"train_{i}" for i in range(5)], subset_recorded=SUBSET)

    assert reconciliation_lines(rows, _profile()) == [
        "config v4: every row agrees with the profile in configs.toml"
    ]


def test_an_artifact_that_names_no_questions_at_all_cannot_be_reconciled() -> None:
    """``question_id`` is ``Absence.never`` in the record register, so this is not an artifact
    of measurement rows. Reporting agreement about it is the F1 sentence one field over."""
    lines = reconciliation_lines([{"corpus_content_hash": DIGEST}] * 2, _profile())

    assert not any("every row agrees" in line for line in lines)
    assert "cannot be shown to be the config's question set" in lines[1]


def test_the_startup_check_refuses_an_config_that_declares_no_question_set() -> None:
    """The second lock at the wire, before the first paid question."""
    refusal = config_startup_refusal(_profile(subset=None), {"corpus_content_hash": DIGEST})

    assert refusal and "no question_subset" in refusal


def test_the_startup_check_refuses_a_session_on_the_wrong_question_set() -> None:
    """The pre-flight passes a bare mapping, so a top-level ``question_subset`` counts. Asking after the run is after
    the money."""
    refusal = config_startup_refusal(
        _profile(), {"corpus_content_hash": DIGEST, "question_subset": "1351:000000000000"}
    )

    assert refusal and "1351:000000000000" in refusal


def test_the_driver_supplies_both_locks_to_the_pre_flight() -> None:
    """The caller must pass both identities. The pure halves are covered above; this is the call site."""
    driver = pathlib.Path(__file__).resolve().parents[2] / "tools" / "run_datalake_eval.py"
    tree = ast.parse(driver.read_text(encoding="utf-8"))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "config_startup_refusal"
    ]
    assert len(calls) == 1, "one pre-flight, or two places disagree about what a config is"

    identity = calls[0].args[1]
    assert isinstance(identity, ast.Dict), "the identity mapping is built at the call site"
    keys = {k.value for k in identity.keys if isinstance(k, ast.Constant)}
    assert keys == {"corpus_content_hash", "question_subset", "corpus_release"}, (
        f"the pre-flight compares {sorted(keys)}; an identity it does not pass is an identity "
        "nothing checks until the artifact is finished"
    )
    # No ``settings_resolved``: a nested mapping would take the setting branch and read ``None``.
    # ``corpus_release`` stays flat here. This mapping is not a row.
    assert "settings_resolved" not in keys
