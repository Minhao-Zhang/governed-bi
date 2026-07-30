"""Schemas whose curator crashed must not be served, scored, or quietly dropped.

The incident: a paid 55-schema run hit the deep-agent recursion limit on 13 schemas.
``curator.pipeline._invoke_agent`` files that crash in the per-db ``run_manifest.json``
and lets the build finish, so 13 partially-authored corpora went on to be served,
scored, and ranked by the pooled router against the 42 complete ones. The pooled driver
*collected* those errors before the serve loop started and spent the knowledge on one
console warning; the only lasting consequence was that ``quotable()`` disqualified the
whole run, which cannot express "the other 42 are fine".

Two claims, and both have to hold or the fix is decorative: the schema does not reach
the serve pool, and it is still visible afterwards — a schema that vanishes from the
pool with nothing naming it is the failure ``dbs_absent_from_postgres`` exists to catch,
one layer in.
"""

from __future__ import annotations

import json

import pytest

from governed_bi.eval.harness import _collect_curator_errors
from governed_bi.eval.index import quotable, record_for_run
from governed_bi.eval.run_datalake import (
    _BUILD_COVERAGE_ABORT_FRACTION,
    _CURATOR_ERROR_QUARANTINE_ABORT_FRACTION,
    _CURATOR_ERROR_QUARANTINE_ABORT_MIN_DBS,
    _quarantine_curator_failures,
)

#: What ``_invoke_agent`` actually writes when the deep agent runs out of steps: the
#: short form, then the full traceback. ``_collect_curator_errors`` keeps the first line.
_RECURSION_ERROR = (
    "GraphRecursionError: Recursion limit of 100 reached without hitting a stop "
    "condition.\n  File \"pipeline.py\", line 366, in _invoke_agent\n"
)


def _errs(*dbs: str, arm: str = "curated") -> dict[str, dict]:
    return {db: {arm: {"error": _RECURSION_ERROR, "fix_pass_error": None}} for db in dbs}


# --------------------------------------------------------------------------- #
# The pool
# --------------------------------------------------------------------------- #


def test_a_curator_errored_schema_is_withheld_from_the_pool_and_named():
    """The whole fix in one assertion: the broken schema leaves the pool the serve loop
    is paid to iterate, and the reason survives as data rather than as a warning."""
    built = ["cs_semester", "restaurant", "beer_factory"]

    servable, reasons = _quarantine_curator_failures(
        built, _errs("restaurant"), n_requested=len(built)
    )

    assert servable == ["cs_semester", "beer_factory"]
    assert "restaurant" not in servable
    assert set(reasons) == {"restaurant"}
    # The reason has to say which arm and what happened, or an operator reading
    # summary.json learns only that something went wrong somewhere.
    assert "curated" in reasons["restaurant"]
    assert "Recursion limit" in reasons["restaurant"]
    # One line, not a pasted traceback: this lands in summary.json and the ledger.
    assert "\n" not in reasons["restaurant"]


def test_the_order_of_the_surviving_pool_is_left_alone():
    """``built`` comes back from ``run_build_phase`` and every downstream derivation —
    questions, gold, corpora, census — is keyed off it. Re-sorting here would silently
    reorder the serve stream and make two otherwise identical runs' stage_events differ.
    """
    built = ["zebra", "apple", "monkey"]
    servable, _ = _quarantine_curator_failures(
        built, _errs("apple"), n_requested=len(built)
    )
    assert servable == ["zebra", "monkey"]


def test_a_clean_build_changes_nothing():
    built = [f"db{i}" for i in range(9)]
    servable, reasons = _quarantine_curator_failures(built, {}, n_requested=9)
    assert servable == built
    assert reasons == {}


def test_an_error_recorded_for_a_schema_that_never_built_is_ignored():
    """``curator_errors`` is collected over ``built``, but a resumed directory can hold a
    sidecar for a schema this invocation is not serving. Quarantining on it would report
    attrition that did not happen in this run."""
    servable, reasons = _quarantine_curator_failures(
        ["a", "b"], _errs("c"), n_requested=2
    )
    assert servable == ["a", "b"]
    assert reasons == {}


def test_the_incident_itself_withholds_thirteen_and_still_serves_fortytwo():
    """13 of 55 is 24%, just under the ceiling — deliberately. That run should hand back
    the 42 intact schemas rather than throw away a paid build; the 13 are named, and the
    ledger refuses to quote it either way."""
    built = [f"db{i}" for i in range(55)]
    servable, reasons = _quarantine_curator_failures(
        built, _errs(*[f"db{i}" for i in range(13)]), n_requested=55
    )
    assert len(servable) == 42
    assert len(reasons) == 13


# --------------------------------------------------------------------------- #
# The abort fraction
# --------------------------------------------------------------------------- #


def test_a_systematic_curator_failure_aborts_before_the_serve_loop_spends():
    """Above the ceiling the curator is misconfigured rather than unlucky, and the
    surviving pool is a much smaller benchmark than the one the run names. Aborting
    costs the serve budget nothing; serving costs it everything and produces a number
    nobody may quote."""
    built = [f"db{i}" for i in range(20)]
    with pytest.raises(RuntimeError) as err:
        _quarantine_curator_failures(
            built, _errs(*[f"db{i}" for i in range(12)]), n_requested=20
        )
    msg = str(err.value)
    assert "12 of 20" in msg
    assert "60%" in msg
    assert "--max-agent-steps" in msg, "the message must say what to do next"
    assert "+9 more" in msg, "a truncated list must say how much it hid"


def test_nothing_left_to_serve_aborts_whatever_the_share_says():
    """Ahead of the proportional test on purpose. An empty pool divides by zero in every
    downstream aggregate, and the small-pool floor below would otherwise wave a
    one-of-one wipeout straight through."""
    with pytest.raises(RuntimeError, match="no intact corpus left to serve"):
        _quarantine_curator_failures(["only"], _errs("only"), n_requested=1)


def test_one_bad_schema_in_a_smoke_run_does_not_abort_it():
    """The runbook's own ``--limit-dbs 3`` smoke: one quarantine is 33%, over the
    ceiling, and no evidence of anything systematic. The count floor is what keeps the
    share from making every small run unrunnable — the same reason its gold twin has
    one."""
    servable, reasons = _quarantine_curator_failures(
        ["a", "b", "c"], _errs("a"), n_requested=3
    )
    assert servable == ["b", "c"]
    assert len(reasons) == 1 < _CURATOR_ERROR_QUARANTINE_ABORT_MIN_DBS


def test_the_share_is_measured_against_what_was_requested_not_what_built():
    """Same denominator as the gold guard, so the two shares of one requested pool can be
    read side by side. Measured against ``built`` instead, a quarantine following a lossy
    build would get a flattering smaller denominator and slip under the ceiling: 4 of the
    10 that built is 40% and aborts, 4 of 40 requested is 10% and does not."""
    built = [f"db{i}" for i in range(10)]
    bad = _errs(*[f"db{i}" for i in range(4)])

    servable, reasons = _quarantine_curator_failures(built, bad, n_requested=40)
    assert len(servable) == 6

    with pytest.raises(RuntimeError, match="4 of 10"):
        _quarantine_curator_failures(built, bad, n_requested=10)


def test_the_quarantine_ceiling_is_its_own_number():
    """Pinned so a later tweak cannot collapse it into the build-coverage floor. They
    answer different questions — "did it build" versus "did the curator finish" — and
    one must not be tuned as a proxy for the other."""
    assert 0 < _CURATOR_ERROR_QUARANTINE_ABORT_FRACTION < _BUILD_COVERAGE_ABORT_FRACTION


# --------------------------------------------------------------------------- #
# The on-disk layout the incident actually arrives in
# --------------------------------------------------------------------------- #


def test_a_recursion_limit_on_disk_reaches_the_quarantine(tmp_path):
    """End to end over the real files, because that join is where this failed before: the
    driver relocates each db's ``run_manifest.json`` into ``<root>/<db>/_build/``, and a
    reader looking at the arm root sees nothing. The crash record and the gate have to
    meet on the same path or the quarantine is unreachable in a real run.
    """
    arms = ("baseline", "curated")
    roots = {arm: tmp_path / f"corpus_{arm}" for arm in arms}
    built = ["restaurant", "beer_factory"]
    for arm in arms:
        for db in built:
            build = roots[arm] / db / "_build"
            build.mkdir(parents=True)
            crashed = arm == "curated" and db == "restaurant"
            (build / "run_manifest.json").write_text(
                json.dumps(
                    {"error": _RECURSION_ERROR if crashed else None,
                     "fix_pass_error": None}
                ),
                encoding="utf-8",
            )

    # Exactly the expression the driver runs, per db.
    curator_errors = {
        db: errs
        for db in built
        if (errs := _collect_curator_errors({a: roots[a] / db / "_build" for a in arms}))
    }
    assert set(curator_errors) == {"restaurant"}

    servable, reasons = _quarantine_curator_failures(
        built, curator_errors, n_requested=len(built)
    )
    assert servable == ["beer_factory"]
    assert "Recursion limit" in reasons["restaurant"]


def test_an_unpromoted_diagnostic_marker_also_withholds_the_schema(tmp_path):
    """A db whose curator diagnostics could not be promoted has an UNKNOWN curator
    verdict, and ``_collect_curator_errors`` reports that as an error precisely so
    "absent" cannot read as "clean". It must withhold the schema for the same reason a
    recorded crash does: nobody can say what that corpus contains."""
    build = tmp_path / "corpus_curated" / "restaurant" / "_build"
    build.mkdir(parents=True)
    (build / "UNPROMOTED_SIDECARS.json").write_text(
        json.dumps({"unpromoted": ["run_manifest.json"]}), encoding="utf-8"
    )
    errs = _collect_curator_errors({"curated": build})

    servable, reasons = _quarantine_curator_failures(
        ["restaurant", "beer_factory"], {"restaurant": errs}, n_requested=2
    )
    assert servable == ["beer_factory"]
    assert "verdict is unknown" in reasons["restaurant"]


# --------------------------------------------------------------------------- #
# ...and it is still visible in the artifacts afterwards
# --------------------------------------------------------------------------- #


def _run_dir(tmp_path, **summary_extra):
    run_dir = tmp_path / "20260729T000000Z"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mode": "datalake",
                "manifest_schema_version": 1,
                "model": "gpt-5.6-luna",
                "split": "test",
                "route_top_k": 10,
                "route_llm_pick": True,
                "grade_semantic_failures": True,
            }
        ),
        encoding="utf-8",
    )
    arm = {
        "n": 1500,
        "ex_lenient": 0.33,
        "crash_rate": 0.0,
        "n_correct_with_empty_gold": 0,
        "n_correct_and_pred_has_no_from": 0,
        "n_correct_and_zero_table_overlap": 0,
    }
    summary = {
        "mode": "datalake",
        "split": "test",
        "n_questions": 1500,
        "n_dbs_built": 42,
        "n_dbs_requested": 55,
        "arms": {"baseline": dict(arm), "curated": dict(arm)},
        "build_errors": {},
        "curator_errors": {},
    }
    summary.update(summary_extra)
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run_dir


def test_a_withheld_schema_reaches_the_ledger_record_and_blocks_quoting(tmp_path):
    """``summary.json`` -> record -> ``quotable()``, the same path
    ``dbs_absent_from_postgres`` takes. A 42-schema result is not the 55-schema
    benchmark, however internally consistent it is."""
    record = record_for_run(
        _run_dir(
            tmp_path,
            dbs_quarantined_curator_error={
                "restaurant": "curated: GraphRecursionError: Recursion limit of 100",
                "cs_semester": "curated_sme: GraphRecursionError: Recursion limit of 100",
            },
            n_dbs_built_before_quarantine=44,
        )
    )

    assert record["dbs_quarantined_curator_error"] == ["cs_semester", "restaurant"]
    assert record["n_dbs_built_before_quarantine"] == 44
    ok, reasons = quotable(record)
    assert not ok
    joined = " | ".join(reasons)
    assert "withheld from serving" in joined
    assert "2 of 55" in joined, "the reason has to state the scale of what went unscored"
    assert "restaurant" in joined


def test_a_run_that_withheld_nothing_is_still_quotable(tmp_path):
    """The gate must not fire on the empty case, or every clean run reads as damaged."""
    record = record_for_run(
        _run_dir(tmp_path, dbs_quarantined_curator_error={})
    )
    ok, reasons = quotable(record)
    assert ok, reasons


def test_a_curator_error_still_blocks_quoting_on_its_own(tmp_path):
    """The pre-existing gate, preserved. Quarantining is not a way to launder a run back
    to quotable: the withheld schemas stay in ``curator_errors`` too, and either reason
    alone is enough."""
    record = record_for_run(
        _run_dir(tmp_path, curator_errors={"restaurant": {"curated": {"error": "boom"}}})
    )
    ok, reasons = quotable(record)
    assert not ok
    assert any("curator build errors" in r for r in reasons)


def test_a_record_written_before_the_field_existed_is_not_accused(tmp_path):
    """Absence of the key is a run that predates the quarantine, not a run that withheld
    schemas and hid it. Retro-flagging every archived run would bury the real cases."""
    record = record_for_run(_run_dir(tmp_path))
    assert record["dbs_quarantined_curator_error"] == []
    ok, _ = quotable(record)
    assert ok
