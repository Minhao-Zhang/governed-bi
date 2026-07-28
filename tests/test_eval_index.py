"""The run ledger: quotability, comparability, and upsert-by-run_dir.

These tests encode the two mistakes that cost a set of results: quoting a number
from a run that had crashed, and comparing two runs whose knobs differed.
"""

import json

import pytest

from governed_bi.eval.index import (
    COMPARABILITY_KEYS,
    RESUME_DRIFT_KEYS,
    append_run,
    comparable,
    index_run,
    load_index,
    quotable,
    record_for_run,
    render_index,
)


def _write_run(
    tmp_path,
    *,
    name="20260725T000000Z",
    split="test",
    arms=None,
    build_errors=None,
    curator_errors=None,
    manifest_extra=None,
    summary_extra=None,
):
    run_dir = tmp_path / name
    run_dir.mkdir(parents=True)
    manifest = {
        "mode": "datalake",
        "created_at_utc": name,
        "model": "gpt-5.6-luna",
        "split": split,
        "route_top_k": 10,
        "route_llm_pick": True,
        "schema_pick_max_columns": 12,
        "use_embedder": True,
        "serve_workers": 1,
    }
    manifest.update(manifest_extra or {})
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = {
        "mode": "datalake",
        "split": split,
        "n_questions": 72,
        "n_dbs_built": 5,
        "arms": arms
        if arms is not None
        else {
            "baseline": {"n": 72, "ex_lenient": 0.2, "crash_rate": 0.0},
            "curated": {"n": 72, "ex_lenient": 0.33, "crash_rate": 0.0},
        },
        "build_errors": build_errors or {},
        "curator_errors": curator_errors or {},
    }
    summary.update(summary_extra or {})
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run_dir


# --------------------------------------------------------------------------- #
# quotable
# --------------------------------------------------------------------------- #


def test_a_clean_test_split_run_is_quotable(tmp_path):
    record = record_for_run(_write_run(tmp_path))
    assert record["quotable"], record["not_quotable_because"]


def test_a_crashed_arm_makes_a_run_unquotable(tmp_path):
    run = _write_run(
        tmp_path,
        arms={
            "baseline": {"n": 72, "ex_lenient": 0.2, "crash_rate": 0.0},
            "curated": {"n": 72, "ex_lenient": 0.33, "crash_rate": 0.04},
        },
    )
    ok, reasons = quotable(record_for_run(run))
    assert not ok
    assert any("crashed" in r for r in reasons)
    # The arm that crashed must be named — "some arm crashed" is not actionable.
    assert any("curated" in r for r in reasons)


def test_re_served_crashed_turns_make_a_run_unquotable(tmp_path):
    """Resume that deletes crashed rows and re-serves them launders crash_rate to 0
    (audit E1). ``n_re_served`` in the arm summary must block quotability."""
    run = _write_run(
        tmp_path,
        arms={
            "baseline": {"n": 72, "ex_lenient": 0.2, "crash_rate": 0.0, "n_re_served": 0},
            "curated": {
                "n": 72,
                "ex_lenient": 0.33,
                "crash_rate": 0.0,
                "n_re_served": 12,
            },
        },
    )
    record = record_for_run(run)
    assert record["n_re_served_by_arm"] == {"curated": 12}
    ok, reasons = quotable(record)
    assert not ok
    assert any("re-served" in r for r in reasons)
    assert any("curated" in r for r in reasons)


def test_an_unmeasured_crash_rate_fails_closed(tmp_path):
    # A run predating the crash/refusal split cannot claim it had no crashes: its
    # refusal_rate silently absorbed them. Absence of evidence is not evidence.
    run = _write_run(
        tmp_path, arms={"baseline": {"n": 72, "ex_lenient": 0.2}}
    )
    ok, reasons = quotable(record_for_run(run))
    assert not ok
    assert any("crash rate not recorded" in r for r in reasons)


def test_train_split_is_never_quotable(tmp_path):
    ok, reasons = quotable(record_for_run(_write_run(tmp_path, split="train")))
    assert not ok
    assert any("train split" in r for r in reasons)


def test_curator_build_errors_make_a_run_unquotable(tmp_path):
    run = _write_run(tmp_path, curator_errors={"curated/app_store": {"error": "TPM cap"}})
    ok, reasons = quotable(record_for_run(run))
    assert not ok
    assert any("app_store" in r for r in reasons)


def test_a_resume_under_a_changed_knob_makes_a_run_unquotable(tmp_path):
    # `_merge_resume_manifest` keeps the ORIGINAL knobs at the top level and files
    # each resume's under `resumes`. Without reading that, a directory half-scored
    # on one model and half on another presents itself as a clean single-config run
    # and gets quoted beside one.
    run = _write_run(
        tmp_path,
        manifest_extra={
            "resumes": [{"model": "other-model", "created_at_utc": "20260726T000000Z"}]
        },
    )
    record = record_for_run(run)
    assert record["resumed_with_drift"] == ["model"]
    ok, reasons = quotable(record)
    assert not ok
    assert any("resumed under changed model" in r for r in reasons)


def test_a_resume_with_no_knob_change_is_still_quotable(tmp_path):
    # An interrupted run continued with identical settings is the normal case and
    # must not be penalised, or the check trains people to ignore it.
    run = _write_run(
        tmp_path,
        manifest_extra={"resumes": [{"model": "gpt-5.6-luna", "route_top_k": 10}]},
    )
    record = record_for_run(run)
    assert record["resumed_with_drift"] == []
    assert record["quotable"], record["not_quotable_because"]


def test_a_malformed_resumes_entry_is_ignored_not_fatal(tmp_path):
    run = _write_run(tmp_path, manifest_extra={"resumes": ["not-a-dict", None]})
    assert record_for_run(run)["resumed_with_drift"] == []


def test_a_run_with_no_summary_is_not_quotable(tmp_path):
    empty = tmp_path / "20260725T999999Z"
    empty.mkdir()
    record = record_for_run(empty)
    assert not record["quotable"]
    assert any("no per-arm summary" in r for r in record["not_quotable_because"])


# --------------------------------------------------------------------------- #
# comparable
# --------------------------------------------------------------------------- #


def test_identical_configuration_is_comparable(tmp_path):
    a = record_for_run(_write_run(tmp_path, name="a"))
    b = record_for_run(_write_run(tmp_path, name="b"))
    ok, diffs = comparable(a, b)
    assert ok, diffs


@pytest.mark.parametrize(
    "extra,label",
    [
        ({"model": "other-model"}, "model"),
        ({"route_top_k": 3}, "route_top_k"),
        ({"route_llm_pick": False}, "llm_pick"),
        ({"schema_pick_max_columns": 0}, "schema_pick_max_columns"),
        ({"use_embedder": False}, "embedder"),
        ({"prompt_set_hash": "deadbeef"}, "prompt set"),
    ],
)
def test_any_changed_knob_makes_two_runs_incomparable(tmp_path, extra, label):
    a = record_for_run(_write_run(tmp_path, name="a"))
    b = record_for_run(_write_run(tmp_path, name="b", manifest_extra=extra))
    ok, diffs = comparable(a, b)
    assert not ok
    assert any(label in d for d in diffs), diffs


def test_a_different_split_makes_two_runs_incomparable(tmp_path):
    a = record_for_run(_write_run(tmp_path, name="a", split="test"))
    b = record_for_run(_write_run(tmp_path, name="b", split="train"))
    ok, diffs = comparable(a, b)
    assert not ok
    assert any("split" in d for d in diffs)


def test_a_knob_absent_from_both_runs_is_not_a_difference():
    # Two runs that both predate a knob did not differ in it.
    ok, diffs = comparable({"model": "m"}, {"model": "m"})
    assert ok, diffs


def test_a_knob_recorded_on_only_one_side_is_a_difference():
    # One side is unknown, so the pair cannot be asserted comparable.
    ok, diffs = comparable({"prompt_set_hash": "abc"}, {})
    assert not ok
    assert any("prompt set" in d for d in diffs)


# --------------------------------------------------------------------------- #
# ledger I/O
# --------------------------------------------------------------------------- #


def test_indexing_the_same_run_twice_leaves_one_row(tmp_path):
    # A resumed run is indexed twice; the ledger must count runs, not invocations.
    run = _write_run(tmp_path)
    ledger = tmp_path / "index.jsonl"
    index_run(run, ledger)
    index_run(run, ledger)
    assert len(load_index(ledger)) == 1


def test_indexing_updates_an_existing_row(tmp_path):
    run = _write_run(tmp_path)
    ledger = tmp_path / "index.jsonl"
    index_run(run, ledger)
    # Re-score the run with a crash and re-index it.
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    summary["arms"]["curated"]["crash_rate"] = 0.5
    (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    index_run(run, ledger)
    rows = load_index(ledger)
    assert len(rows) == 1
    assert not rows[0]["quotable"]


def test_a_torn_ledger_line_does_not_block_indexing(tmp_path):
    ledger = tmp_path / "index.jsonl"
    ledger.write_text('{"run_dir": "ok"}\n{"run_dir": "trunca', encoding="utf-8")
    append_run({"run_dir": "new"}, ledger)
    dirs = {r["run_dir"] for r in load_index(ledger)}
    assert dirs == {"ok", "new"}


def test_load_index_of_a_missing_file_is_empty(tmp_path):
    assert load_index(tmp_path / "nope.jsonl") == []


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def test_render_names_the_reason_a_run_is_not_quotable(tmp_path):
    run = _write_run(tmp_path, split="train")
    text = render_index([record_for_run(run)])
    assert "Not ledger_ok" in text
    assert "train split" in text


def test_render_flags_an_incomparable_pair(tmp_path):
    a = record_for_run(_write_run(tmp_path, name="a"))
    b = record_for_run(_write_run(tmp_path, name="b", manifest_extra={"route_top_k": 3}))
    text = render_index([a, b])
    assert "NOT comparable" in text
    assert "route_top_k" in text


def test_render_of_an_empty_ledger_says_so():
    assert "no runs indexed" in render_index([])


def test_render_lists_one_row_per_arm(tmp_path):
    text = render_index([record_for_run(_write_run(tmp_path))])
    assert "baseline" in text
    assert "curated" in text


# --------------------------------------------------------------------------- #
# Concurrency and unreadable-manifest handling
# --------------------------------------------------------------------------- #


def test_concurrent_appends_do_not_lose_records(tmp_path):
    """Two runs finishing at once is ordinary; the upsert is a read-modify-rewrite.

    Unsynchronised, the loser's stale snapshot overwrote the winner's record and
    everything indexed in between — measured at 16 of 17 records destroyed under 12
    concurrent writers, with no exception raised anywhere.
    """
    import threading

    ledger = tmp_path / "index.jsonl"
    for i in range(5):
        append_run({"run_dir": f"runs/old_{i}"}, ledger)

    n = 12
    barrier = threading.Barrier(n)
    errors: list[str] = []

    def writer(i: int) -> None:
        barrier.wait()
        try:
            append_run({"run_dir": f"runs/new_{i}"}, ledger)
        except Exception as err:  # noqa: BLE001 - surfaced in the assert below
            errors.append(repr(err))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    on_disk = {r["run_dir"] for r in load_index(ledger)}
    expected = {f"runs/old_{i}" for i in range(5)} | {f"runs/new_{i}" for i in range(n)}
    assert on_disk == expected


def test_the_lock_is_released_and_leaves_no_temp_files(tmp_path):
    ledger = tmp_path / "index.jsonl"
    append_run({"run_dir": "runs/a"}, ledger)
    append_run({"run_dir": "runs/b"}, ledger)
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.name != "index.jsonl")
    assert leftovers == [], f"stale lock/tmp left behind: {leftovers}"


def test_a_stale_lock_fails_loudly_rather_than_losing_the_record(tmp_path):
    import pytest

    ledger = tmp_path / "index.jsonl"
    (tmp_path / "index.jsonl.lock").write_text("", encoding="utf-8")
    with pytest.raises(TimeoutError, match="stale lock"):
        append_run({"run_dir": "runs/a"}, ledger, lock_timeout_s=0.2)


def test_an_unreadable_manifest_makes_a_run_unquotable(tmp_path):
    run = _write_run(tmp_path)
    (run / "manifest.json").write_text("{ truncated", encoding="utf-8")
    record = record_for_run(run)
    assert record["manifest_readable"] is False
    ok, reasons = quotable(record)
    assert not ok
    assert any("unreadable" in r for r in reasons)


def test_two_configuration_unknown_runs_are_not_comparable(tmp_path):
    # Every knob is None on both sides, so a naive None == None compare would call
    # them the same experiment.
    a = record_for_run(_write_run(tmp_path, name="a"))
    b = record_for_run(_write_run(tmp_path, name="b"))
    for r in (a, b):
        r["manifest_readable"] = False
    ok, diffs = comparable(a, b)
    assert not ok
    assert any("unreadable" in d for d in diffs)


def test_a_record_predating_the_flag_is_not_penalised():
    # `manifest_readable` absent means "this record is old", not "unreadable".
    ok, _ = comparable({"model": "m"}, {"model": "m"})
    assert ok


# --------------------------------------------------------------------------- #
# The gate's remaining conditions, each one an incident this project already had.
#
# Seven of `quotable()`'s conditions were pinned; these five were not. Each was
# reachable only by the author having read the code correctly — which is the same
# footing the conditions themselves exist to replace. Every one is driven through
# `record_for_run` rather than a hand-built record, so the lift out of summary.json
# and the gate's reading of it are both exercised: a field renamed on either side
# would otherwise leave the gate silently reading a key nobody writes.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key,label", RESUME_DRIFT_KEYS, ids=[k for k, _ in RESUME_DRIFT_KEYS])
def test_every_resume_drift_key_is_actually_checked(tmp_path, key, label):
    """Parametrized over the tuple itself, so a key added to it without a
    corresponding check fails here rather than going quietly unenforced.

    `git_sha` and `skip_agent` were exactly that: both were named in the driver's
    resume-knob list with comments explaining why changing them mid-run was
    dangerous, while the ledger check iterated the *comparability* list and saw
    neither. A run resumed after a code edit warned once on the console — which
    scrolls past in a multi-hour run — then recorded no drift and stayed quotable.
    """
    # `split` is refused outright at resume time rather than recorded as drift, but
    # the ledger still has to flag it for a directory that predates that check.
    original = {"split": "test", "model": "gpt-5.6-luna", "git_sha": "abc123",
                "skip_agent": False, "prompt_set_hash": "h0", "route_top_k": 10,
                "route_llm_pick": True, "schema_pick_max_columns": 12,
                "use_embedder": True, "corpus_content_hash": "c0"}
    changed = dict(original)
    was = original[key]
    changed[key] = (not was) if isinstance(was, bool) else f"{was}-changed"

    run = _write_run(
        tmp_path,
        manifest_extra={**original, "resumes": [changed]},
    )
    record = record_for_run(run)
    assert record["resumed_with_drift"] == [label], (
        f"a resume under a changed {key} was not recorded as drift"
    )
    ok, reasons = quotable(record)
    assert not ok
    assert any(label in r for r in reasons)


def test_resume_drift_keys_are_a_superset_of_comparability_keys(tmp_path):
    """The two lists answer different questions and must not be collapsed.

    Two runs built at different commits are the *normal* case for comparison —
    that is what comparing a change against its baseline is — so `git_sha` in
    `COMPARABILITY_KEYS` would declare almost every pair in the ledger
    incomparable. Inside one directory the same difference is corrupting.
    """
    comparability = {k for k, _ in COMPARABILITY_KEYS}
    drift = {k for k, _ in RESUME_DRIFT_KEYS}
    assert comparability < drift, "drift keys must be a strict superset"
    assert drift - comparability == {"git_sha", "skip_agent"}

    # And the distinction has to hold in behaviour, not just in the tuples: two runs
    # differing only by commit stay comparable.
    a = record_for_run(_write_run(tmp_path, name="a", manifest_extra={"git_sha": "aaa"}))
    b = record_for_run(_write_run(tmp_path, name="b", manifest_extra={"git_sha": "bbb"}))
    ok, diffs = comparable(a, b)
    assert ok, f"runs at different commits must stay comparable, got {diffs}"


def test_a_database_that_failed_to_build_makes_a_run_unquotable(tmp_path):
    """An arm short a database is not that arm measured on fewer questions — the
    pooled router still ranks against a corpus the run could not build."""
    run = _write_run(tmp_path, build_errors={"beer_factory": {"error": "connection refused"}})
    record = record_for_run(run)
    assert record["build_errors"] == ["beer_factory"]
    ok, reasons = quotable(record)
    assert not ok
    assert any("beer_factory" in r and "failed to build" in r for r in reasons)


def test_corpus_reference_integrity_findings_make_a_run_unquotable(tmp_path):
    """The 9,154-notes incident: findings were computed, written to summary.json,
    printed as a warning, and read by nothing that could stop the number."""
    run = _write_run(
        tmp_path,
        summary_extra={
            "corpus_validation": {
                "curated": {"finding_count": 9154, "findings": ["dangling-ref"]},
                "baseline": {"finding_count": 0},
            }
        },
    )
    record = record_for_run(run)
    # Only the arm with findings is carried; a zero count is not a finding.
    assert record["corpus_finding_counts"] == {"curated": 9154}
    ok, reasons = quotable(record)
    assert not ok
    assert any("curated=9154" in r for r in reasons)


def test_a_clean_corpus_validation_block_does_not_block_quoting(tmp_path):
    """Zero findings must read as a pass, not as a missing field that fails closed —
    otherwise every healthy run trips the gate and people learn to ignore it."""
    run = _write_run(tmp_path, summary_extra={
        "corpus_validation": {"curated": {"finding_count": 0}, "baseline": {"finding_count": 0}}
    })
    record = record_for_run(run)
    assert record["corpus_finding_counts"] == {}
    assert quotable(record)[0]


def test_an_sme_arm_that_folded_nothing_makes_a_run_unquotable(tmp_path):
    """The incident that ran for weeks. A byte-identical corpus means the SME delta
    on that database is not a measurement — its EX equals `curated` by
    construction — and the detector for it used to be a print statement."""
    run = _write_run(
        tmp_path,
        summary_extra={
            "sme_fold": {
                "beer_factory": {"identical_to_curated": True},
                "restaurant": {"identical_to_curated": False},
            }
        },
    )
    record = record_for_run(run)
    assert record["sme_noop_dbs"] == ["beer_factory"]
    ok, reasons = quotable(record)
    assert not ok
    assert any("folded nothing" in r and "beer_factory" in r for r in reasons)


def test_train_test_overlap_makes_a_run_unquotable(tmp_path):
    """Scored questions inside the curator's own input. No downstream metric can
    see this, which is why it has to stop the run here."""
    run = _write_run(tmp_path, summary_extra={
        "leakage": {"train_test_disjoint": False, "n_overlap": 3}
    })
    record = record_for_run(run)
    ok, reasons = quotable(record)
    assert not ok
    assert any("train and test question ids overlap" in r for r in reasons)


def test_an_unrecorded_leakage_check_is_not_an_accusation(tmp_path):
    """Explicit negative only. A run that never ran the check must not be reported
    as having failed it, or archived runs all read as contaminated."""
    assert quotable(record_for_run(_write_run(tmp_path)))[0]
    passed = _write_run(tmp_path, name="passed",
                        summary_extra={"leakage": {"train_test_disjoint": True}})
    assert quotable(record_for_run(passed))[0]


def test_an_arm_that_served_notes_and_injected_none_makes_a_run_unquotable(tmp_path):
    """`_undelivered`'s second loop: the corpus holds notes and the prompt got none.

    Distinct from the pairwise `treatment_divergence` check above, which compares
    two arms' delivered context. This one catches a *single* arm whose treatment was
    built and then not delivered — the arms can still diverge from each other while
    the thing being measured never reached a prompt.
    """
    run = _write_run(
        tmp_path,
        arms={
            "baseline": {"n": 72, "ex_lenient": 0.2, "crash_rate": 0.0},
            "curated_sme": {
                "n": 72, "ex_lenient": 0.33, "crash_rate": 0.0,
                "treatment": {"corpus_note_assets": 412, "n_notes_injected": 0},
            },
        },
    )
    ok, reasons = quotable(record_for_run(run))
    assert not ok
    assert any("curated_sme served a corpus of 412 notes and injected none" in r
               for r in reasons)


def test_an_arm_that_injected_the_notes_it_holds_is_quotable(tmp_path):
    """The complementary branch, so the check above is not passing for the wrong
    reason. Also covers the arm holding no notes at all: `baseline` legitimately has
    none, and flagging that would make every run unquotable."""
    run = _write_run(
        tmp_path,
        arms={
            "baseline": {"n": 72, "ex_lenient": 0.2, "crash_rate": 0.0,
                         "treatment": {"corpus_note_assets": 0, "n_notes_injected": 0}},
            "curated_sme": {"n": 72, "ex_lenient": 0.33, "crash_rate": 0.0,
                            "treatment": {"corpus_note_assets": 412,
                                          "n_notes_injected": 37}},
        },
    )
    ok, reasons = quotable(record_for_run(run))
    assert ok, reasons


def test_schemas_whose_gold_would_not_execute_make_a_run_unquotable(tmp_path):
    """The pre-flight's abort threshold is proportional on purpose — one query crossing
    the gateway timeout must not make the whole split unrunnable. But a score for a
    schema whose gold nothing ever confirmed is not a number to quote, and the warning
    that said so went to a console that scrolls past in a multi-hour run."""
    run = _write_run(
        tmp_path,
        summary_extra={
            "gold_hash_self_check": {
                "n_checked": 66, "agree_rate": 1.0, "n_dbs": 69,
                "exec_error_dbs": {
                    "slow_db": "q: exec timeout",
                    "odd_db": 'q: exec relation "x" does not exist',
                },
                "partial_exec_error_dbs": {},
                "dbs_without_usable_gold": [],
            }
        },
    )
    record = record_for_run(run)
    assert record["gold_unverified_dbs"] == ["odd_db", "slow_db"]
    ok, reasons = quotable(record)
    assert not ok
    assert any("gold would not execute on" in r and "odd_db" in r for r in reasons)


def test_a_schema_that_verified_on_a_retry_does_not_block_quoting(tmp_path):
    """`partial_exec_error_dbs` is the redundancy case: a sampled row failed but
    another executed and agreed, so the grader is demonstrably working there. Blocking
    on it would punish raising `--gold-per-db`, which is the opposite of the point."""
    run = _write_run(
        tmp_path,
        summary_extra={
            "gold_hash_self_check": {
                "n_checked": 69, "agree_rate": 1.0, "n_dbs": 69,
                "exec_error_dbs": {},
                "partial_exec_error_dbs": {"slow_db": "q: exec timeout"},
                "dbs_without_usable_gold": [],
            }
        },
    )
    record = record_for_run(run)
    assert record["gold_unverified_dbs"] == []
    assert quotable(record)[0]


def test_a_run_predating_the_gold_selfcheck_is_not_accused(tmp_path):
    """Absent field means "never recorded", not "failed"."""
    record = record_for_run(_write_run(tmp_path))
    assert record["gold_unverified_dbs"] == []
    assert quotable(record)[0]


def test_schemas_missing_from_postgres_make_a_run_unquotable(tmp_path):
    """A third kind of attrition, and the one with no gate in front of it: a schema
    absent from Postgres never enters `wanted`, so neither the build-coverage check nor
    the gold share can see it — both measure against the already-filtered list. It used
    to produce one truncated console warning and nothing durable, so a default run
    against a partially-loaded Postgres scored 40 of 69 schemas and reported full
    coverage of what it attempted."""
    run = _write_run(
        tmp_path,
        summary_extra={
            "dbs_absent_from_postgres": ["missing_a", "missing_b"],
            "n_dbs_requested": 69,
        },
    )
    record = record_for_run(run)
    assert record["dbs_absent_from_postgres"] == ["missing_a", "missing_b"]
    assert record["n_dbs_requested"] == 69
    ok, reasons = quotable(record)
    assert not ok
    assert any(
        "2 of 69 requested schema(s) were not on Postgres" in r and "missing_a" in r
        for r in reasons
    )


def test_a_run_whose_schemas_were_all_present_is_not_penalised(tmp_path):
    run = _write_run(
        tmp_path,
        summary_extra={"dbs_absent_from_postgres": [], "n_dbs_requested": 69},
    )
    record = record_for_run(run)
    assert record["dbs_absent_from_postgres"] == []
    assert quotable(record)[0]


def test_a_run_predating_the_presence_check_is_not_accused(tmp_path):
    record = record_for_run(_write_run(tmp_path))
    assert record["dbs_absent_from_postgres"] == []
    assert record["n_dbs_requested"] is None
    assert quotable(record)[0]


def test_the_three_kinds_of_attrition_are_reported_separately(tmp_path):
    """Absent from Postgres, failed to build, and gold-unverified are three different
    faults with three different fixes. Collapsing them into one count is how "the run
    covered 40 schemas" came to mean three unrelated things."""
    run = _write_run(
        tmp_path,
        build_errors={"broken_build": {"error": "curator crash"}},
        summary_extra={
            "dbs_absent_from_postgres": ["not_loaded"],
            "n_dbs_requested": 69,
            "gold_hash_self_check": {
                "n_checked": 60, "agree_rate": 1.0, "n_dbs": 67,
                "exec_error_dbs": {"bad_gold": "q: exec timeout"},
                "partial_exec_error_dbs": {}, "dbs_without_usable_gold": [],
            },
        },
    )
    record = record_for_run(run)
    assert record["dbs_absent_from_postgres"] == ["not_loaded"]
    assert record["build_errors"] == ["broken_build"]
    assert record["gold_unverified_dbs"] == ["bad_gold"]

    ok, reasons = quotable(record)
    assert not ok
    joined = " | ".join(reasons)
    assert "not on Postgres" in joined
    assert "failed to build" in joined
    assert "gold would not execute" in joined


def test_a_skip_agent_run_is_never_quotable(tmp_path):
    """`--skip-agent` serves a refuse-all solver, so every arm scores 0 by construction.
    The runbook asks for one immediately before every real run, and they were landing in
    the ledger marked `quotable: true` — a row that looks like a result and is a smoke
    test."""
    run = _write_run(tmp_path, manifest_extra={"skip_agent": True})
    record = record_for_run(run)
    assert record["skip_agent"] is True
    ok, reasons = quotable(record)
    assert not ok
    assert any("--skip-agent" in r for r in reasons)


def test_a_real_run_is_not_penalised_for_the_flag_being_recorded(tmp_path):
    """The complementary case: `skip_agent: False` is the normal state and must not block."""
    run = _write_run(tmp_path, manifest_extra={"skip_agent": False})
    record = record_for_run(run)
    assert record["skip_agent"] is False
    assert quotable(record)[0]


def test_a_run_predating_the_skip_agent_field_is_not_accused(tmp_path):
    record = record_for_run(_write_run(tmp_path))
    assert record["skip_agent"] is None
    assert quotable(record)[0]


def test_a_skip_agent_smoke_run_is_not_comparable_to_a_real_one():
    """The quotability gate stops a smoke run being *quoted*; this stops it being
    *paired*. `run_experiment` wrote a real model name even under `--skip-agent`, so a
    run that never called a model matched a real one on every comparability key."""
    smoke = {
        "split": "test", "model": None, "prompt_set_hash": "h",
        "route_top_k": 10, "route_llm_pick": True,
        "schema_pick_max_columns": 12, "use_embedder": True,
    }
    real = {**smoke, "model": "gpt-5.6-luna"}
    ok, diffs = comparable(smoke, real)
    assert not ok
    assert any("model" in d for d in diffs)


def test_both_drivers_record_no_model_under_skip_agent():
    """The pooled driver already did; `run_experiment` did not, and the two manifests feed
    the same ledger — so a `run_experiment` smoke run matched a real one on every
    comparability key.

    The two drivers spelled the guard differently and drifted apart, so the rule now has
    one definition (`index.manifest_model`) and this asserts on its behaviour plus the
    fact that both drivers route through it. The previous version of this test read the
    two call sites out of `inspect.getsource` — a reformat broke it and an equivalent
    rewrite defeated it, which is the shape of tautology it was written to replace.
    """
    from governed_bi.eval import run_datalake, run_experiment
    from governed_bi.eval.index import manifest_model as shared

    assert shared("gpt-5.6-luna", skip_agent=True) is None
    assert shared("gpt-5.6-luna", skip_agent=False) == "gpt-5.6-luna"

    # Asserted on the manifest each driver actually BUILDS. Sharing the helper is not
    # enough on its own: keeping the import and inlining
    # `"model": settings.models.llm_model` restores the drift with the suite green,
    # which is what an `is shared` identity check alone lets through.
    from pathlib import Path

    from governed_bi.config import Environment, Settings

    settings = Settings.for_env(Environment.dev)

    pooled = run_datalake._build_manifest(
        bird_dir=Path("."),
        split="test",
        model_name=settings.models.llm_model,
        prompt_variants={},
        route_top_k=3,
        route_llm_pick=False,
        schema_pick_max_columns=8,
        use_embedder=False,
        skip_agent=True,
        serve_workers=1,
    )
    single = run_experiment.build_manifest(
        db_id="restaurant",
        bird_dir=Path("."),
        pg_dsn="host=127.0.0.1 port=5435",
        max_agent_steps=8,
        skip_agent=True,
        model_name=settings.models.llm_model,
        resolved_prompts={},
    )
    for name, built in (("run_datalake", pooled), ("run_experiment", single)):
        assert built["model"] is None, (
            f"{name} claims a model for a run that never called one, so its smoke "
            "runs are comparable to real ones"
        )
        assert built["skip_agent"] is True

    # ...and both report the real name when a model IS used, or the gate would be
    # satisfied by a driver that simply never records one.
    assert (
        run_experiment.build_manifest(
            db_id="restaurant",
            bird_dir=Path("."),
            pg_dsn="host=127.0.0.1 port=5435",
            max_agent_steps=8,
            skip_agent=False,
            model_name="gpt-5.6-luna",
            resolved_prompts={},
        )["model"]
        == "gpt-5.6-luna"
    )


def test_the_ledger_lock_retries_a_windows_permission_error(tmp_path, monkeypatch):
    """On Windows, opening a lock file another writer is unlinking raises
    `PermissionError` — the delete is pending, so the name resolves but the open is
    refused — not `FileExistsError`. The retry loop caught only the latter, so it escaped
    `append_run` and a finished run was never indexed: the exact loss the ledger exists to
    prevent, on the platform this is developed on.

    Forced deterministically rather than left to the concurrency test, which is
    intermittently red and passed while the bug was present.
    """
    import os as _os

    from governed_bi.eval import index as index_mod

    ledger = tmp_path / "index.jsonl"
    real_open = _os.open
    calls = {"n": 0}

    def flaky_open(path, flags, *a, **kw):
        if str(path).endswith(".lock") and (flags & _os.O_EXCL):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(13, "Access is denied")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(index_mod.os, "open", flaky_open)
    index_mod.append_run({"run_dir": "r1", "quotable": False}, path=ledger)

    assert calls["n"] >= 3, "the lock was not contended, so the retry path never ran"
    rows = [r for r in ledger.read_text(encoding="utf-8").splitlines() if r.strip()]
    assert len(rows) == 1, "the run was lost rather than retried"


def test_the_ledger_swap_retries_a_reader_holding_the_file_open(tmp_path, monkeypatch):
    """The lock serialises WRITERS. On Windows a *reader* blocks the swap.

    ``os.replace`` over a file any process holds open raises ``PermissionError:
    [WinError 5]``, and it sat outside the retry loop that the lock fix added — so
    opening ``runs/index.jsonl`` in an editor, or a virus scanner touching it, or the
    reader the runbook itself tells the operator to run, lost the record. Measured at
    8 writers x 40 appends with one concurrent reader: 8 of 320 survived.

    The paid run calls ``append_run`` once, so this is a single-shot race — but it
    fires at the end of a multi-hour run, and the traceback is the whole notification
    that the run is not in the ledger.
    """
    from governed_bi.eval import index as index_mod

    ledger = tmp_path / "index.jsonl"
    real_replace = index_mod.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(index_mod.os, "replace", flaky_replace)
    index_mod.append_run({"run_dir": "r1", "quotable": False}, path=ledger)

    assert calls["n"] >= 3, "the swap was not contended, so the retry path never ran"
    rows = [r for r in ledger.read_text(encoding="utf-8").splitlines() if r.strip()]
    assert len(rows) == 1, "the finished run was lost rather than retried"


def test_a_failed_swap_does_not_leave_a_tmp_file_beside_the_ledger(tmp_path, monkeypatch):
    """One orphaned ``.tmp<pid>`` per failure, where ``load_index`` never reads it."""
    from governed_bi.eval import index as index_mod

    ledger = tmp_path / "index.jsonl"
    monkeypatch.setattr(
        index_mod.os,
        "replace",
        lambda *a, **kw: (_ for _ in ()).throw(PermissionError(13, "Access is denied")),
    )
    with pytest.raises(PermissionError):
        index_mod.append_run(
            {"run_dir": "r1", "quotable": False}, path=ledger, lock_timeout_s=0.2
        )
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == [], leftovers


def test_a_run_too_small_to_ever_reach_significance_is_not_quotable():
    """The ledger was advertising 2- and 4-question smoke runs as quotable, and
    ``comparable()`` matched three of them against a real run on every configuration
    key — so the run you are about to pay for would be reported comparable to a
    4-question scratch run carrying the same model.

    The floor is derived, not chosen: the paired test is an exact two-sided binomial
    on the discordant pairs, ``p = 2 * 0.5**d``, which first clears 0.05 at ``d = 6``.
    Discordant pairs cannot exceed the question count.
    """
    from governed_bi.eval.index import MIN_QUOTABLE_QUESTIONS, quotable

    base = {
        "manifest_readable": True,
        "split": "test",
        "headline": {"curated": {"crash_rate": 0.0}},
    }
    ok, reasons = quotable({**base, "n_questions": MIN_QUOTABLE_QUESTIONS - 1})
    assert not ok
    assert any("arithmetic floor" in r for r in reasons), reasons

    ok, reasons = quotable({**base, "n_questions": MIN_QUOTABLE_QUESTIONS})
    assert ok, reasons

    # Fails closed on absence, like every other gate in this module.
    ok, reasons = quotable(base)
    assert not ok
    assert any("no question count" in r for r in reasons), reasons

    # And the floor is the one the arithmetic gives, not a taste call — derived
    # against the rule the pre-quote checklist actually applies, which is
    # `p_value_holm`, not the raw p. The default ladder is four arms, so Holm's
    # multiplier on the most significant of C(4,2)=6 tests is 6. Against the RAW p
    # the floor would be 6 questions, and a 6- or 7-question run would clear the gate
    # while still being incapable of the significance the checklist demands.
    n_tests = 6
    assert 2 * 0.5**MIN_QUOTABLE_QUESTIONS * n_tests < 0.05
    assert 2 * 0.5 ** (MIN_QUOTABLE_QUESTIONS - 1) * n_tests >= 0.05


def test_prune_can_drop_scratch_runs_before_their_directories_vanish(tmp_path, monkeypatch):
    """A ledger record outlives a temp-dir run, and then cannot be verified by anyone.

    A review left this ledger at 116 records of which 78 pointed into a session
    scratchpad — so the file the runbook sends an operator to read was mostly other
    people's throwaway smoke runs. Anticipating the deletion is the same rule as the
    existence check, applied before the collection rather than after.

    Opt-in, because a legitimate run can live outside the repo (a mounted volume, a
    scratch disk on a big box), and dropping those silently would be worse.
    """
    from governed_bi.eval.index import append_run, load_index, prune_index

    repo = tmp_path / "repo"
    inside = repo / "runs" / "datalake" / "20260727T000000Z"
    outside = tmp_path / "scratch" / "20260727T000001Z"
    for d in (inside, outside):
        d.mkdir(parents=True)
    monkeypatch.chdir(repo)

    ledger = repo / "index.jsonl"
    for d in (inside, outside):
        append_run({"run_dir": str(d).replace("\\", "/"), "quotable": False}, path=ledger)
    assert len(load_index(ledger)) == 2

    # Default prune keeps both: both directories still exist.
    assert prune_index(ledger) == []
    assert len(load_index(ledger)) == 2

    dropped = prune_index(ledger, drop_outside_repo=True)
    assert len(dropped) == 1
    assert "scratch" in dropped[0]
    remaining = load_index(ledger)
    assert len(remaining) == 1
    assert "datalake" in remaining[0]["run_dir"]
