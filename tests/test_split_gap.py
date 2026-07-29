"""Train-vs-test gap: the diagnostic, and the traps in reporting it.

Scoring train is not a second result — the curator reads train gold SQL, so a
curated arm's train EX is partly recall of what it was built from, and
``eval.index.quotable`` already refuses a train-scored run. What the pair buys is
the gap: how much of an arm's score does not survive a new question.
"""

from __future__ import annotations

import json

import pytest

from governed_bi.eval.split_gap import (
    CONTROL_ARM,
    GAPPED_RATES,
    format_split_gap,
    split_gap,
    write_split_gap,
)


def _summary(**per_arm):
    return {"arms": {arm: dict(vals) for arm, vals in per_arm.items()}}


def test_the_gap_is_train_minus_test_per_arm():
    train = _summary(baseline={"n": 100, "ex_lenient": 0.40}, curated={"n": 100, "ex_lenient": 0.90})
    test = _summary(baseline={"n": 30, "ex_lenient": 0.38}, curated={"n": 30, "ex_lenient": 0.50})

    report = split_gap(train, test)
    assert report["arms"]["baseline"]["gap"]["ex_lenient"] == 0.40 - 0.38
    assert report["arms"]["curated"]["gap"]["ex_lenient"] == 0.90 - 0.50
    assert report["arms"]["curated"]["n_train"] == 100
    assert report["arms"]["curated"]["n_test"] == 30


def test_a_large_curated_gap_is_what_the_report_exists_to_surface():
    """The point of the whole module. ``curated`` scoring far higher on the questions
    the curator was built from than on held-out ones is the memorisation reading of a
    positive curated delta, and it should be visibly larger than baseline's."""
    train = _summary(baseline={"n": 9, "ex_lenient": 0.40}, curated={"n": 9, "ex_lenient": 0.95})
    test = _summary(baseline={"n": 9, "ex_lenient": 0.39}, curated={"n": 9, "ex_lenient": 0.45})

    gaps = split_gap(train, test)["arms"]
    assert gaps["curated"]["gap"]["ex_lenient"] > gaps["baseline"]["gap"]["ex_lenient"]


def test_an_unmeasured_side_gives_no_gap_rather_than_zero():
    """``None`` is not 0.0. An arm that measured nothing on one split has no gap, and
    rendering it as zero reads as "transferred perfectly" — the same absent-vs-zero
    trap the summary's rates document."""
    train = _summary(curated={"n": 0, "ex_lenient": None})
    test = _summary(curated={"n": 30, "ex_lenient": 0.50})
    assert split_gap(train, test)["arms"]["curated"]["gap"]["ex_lenient"] is None


def test_a_bool_is_not_a_rate():
    """``isinstance(True, int)`` is True in Python, so a flag that drifted into a
    gapped key would subtract as 1 and read as a 100-point gap."""
    train = _summary(curated={"n": 1, "ex_lenient": True})
    test = _summary(curated={"n": 1, "ex_lenient": 0.5})
    assert split_gap(train, test)["arms"]["curated"]["gap"]["ex_lenient"] is None


def test_an_arm_on_only_one_split_is_reported_not_dropped():
    train = _summary(baseline={"n": 5, "ex_lenient": 0.4}, curated={"n": 5, "ex_lenient": 0.6})
    test = _summary(baseline={"n": 5, "ex_lenient": 0.4})
    report = split_gap(train, test)
    assert "curated" not in report["arms"]
    assert report["arms_not_in_both"] == ["curated"]


def test_only_accuracy_like_rates_are_gapped():
    """Gapping ``crash_rate`` or ``refusal_rate`` would invite reading operational
    noise as overfitting, so the set is explicit rather than "every rate".

    ``routing_recall`` is in the set although the router INDEX is identical across the
    splits: the index is built from train material, so text that echoes train
    vocabulary retrieves better for train questions. It is a retrieval-side
    overfitting channel, and the module says so where the set is declared.
    """
    assert "crash_rate" not in GAPPED_RATES
    assert "refusal_rate" not in GAPPED_RATES
    assert "ex_lenient" in GAPPED_RATES
    assert "routing_recall" in GAPPED_RATES


def test_the_reading_note_says_train_is_never_quotable():
    """The artifact has to carry its own caveat: someone will open split_gap.json
    without having read the runbook."""
    reading = split_gap(_summary(), _summary())["reading"]
    assert "never quotable" in reading
    assert "train - test" in reading


def test_write_split_gap_emits_the_artifact(tmp_path):
    (tmp_path / "train").mkdir()
    (tmp_path / "test").mkdir()
    (tmp_path / "train" / "summary.json").write_text(
        json.dumps(_summary(curated={"n": 9, "ex_lenient": 0.9})), encoding="utf-8"
    )
    (tmp_path / "test" / "summary.json").write_text(
        json.dumps(_summary(curated={"n": 9, "ex_lenient": 0.5})), encoding="utf-8"
    )

    report = write_split_gap(tmp_path, tmp_path / "train", tmp_path / "test")
    on_disk = json.loads((tmp_path / "split_gap.json").read_text(encoding="utf-8"))
    assert on_disk == report
    assert on_disk["arms"]["curated"]["gap"]["ex_lenient"] == 0.4
    assert on_disk["train_dir"].endswith("/train")


def test_a_missing_summary_reports_an_error_instead_of_raising(tmp_path):
    """Both scored splits are already on disk by the time this runs. Losing them to a
    reporting fault would be the expensive failure, so the gap degrades instead."""
    (tmp_path / "train").mkdir()
    (tmp_path / "test").mkdir()
    report = write_split_gap(tmp_path, tmp_path / "train", tmp_path / "test")
    assert "error" in report
    assert (tmp_path / "split_gap.json").exists()
    assert "unavailable" in format_split_gap(report)


def test_the_stdout_line_names_both_splits_and_the_gap():
    train = _summary(curated={"n": 100, "ex_lenient": 0.9})
    test = _summary(curated={"n": 30, "ex_lenient": 0.5})
    line = format_split_gap(split_gap(train, test))
    assert "[curated]" in line
    assert "train=0.900(n=100)" in line
    assert "test=0.500(n=30)" in line
    assert "gap=0.400" in line


# --------------------------------------------------------------------------- #
# --split both: one build, two scored splits
# --------------------------------------------------------------------------- #


def _stub_run(calls):
    """Stand in for ``run_datalake``, recording how each split was invoked."""

    def run(**kwargs):
        calls.append(kwargs)
        return {
            "arms": {},
            "treatment_divergence": {},
            "comparisons": [],
            "deltas": {},
            # train is unquotable by design; the stub mirrors that
            "quotable": kwargs["split"] != "train",
        }

    return run


def _argv(tmp_path, split):
    return [
        "--bird-dir", str(tmp_path / "bird"),
        "--out", str(tmp_path / "runs"),
        "--split", split,
        "--skip-agent",
        "--dbs", "beer_factory",
    ]


def test_both_splits_share_one_corpus_build(monkeypatch, tmp_path):
    """The whole reason ``corpus_dir`` exists. The curator is stochastic, so a rebuild
    between splits would make the gap a mix of overfitting and curator variance —
    which is the confound the gap is supposed to measure."""
    from governed_bi.eval import run_datalake as rd

    calls: list[dict] = []
    monkeypatch.setattr(rd, "run_datalake", _stub_run(calls))
    monkeypatch.setattr(rd, "write_manifest_hook", None, raising=False)

    rd.main(_argv(tmp_path, "both"))

    assert [c["split"] for c in calls] == list(rd._SPLITS)
    corpus_dirs = {c["corpus_dir"] for c in calls}
    assert len(corpus_dirs) == 1, f"splits must share one corpus dir, got {corpus_dirs}"
    # ...and it is NOT either split's artifact dir, or the second pass would treat the
    # first pass's generations as its own.
    assert corpus_dirs.pop().name == "corpora"


def test_each_split_scores_into_its_own_directory(monkeypatch, tmp_path):
    """Per-split directories rather than per-split filenames: every downstream reader
    (analyse_run, index_run, quotable, the resume guard) is keyed to a run directory
    holding exactly one split's artifacts."""
    from governed_bi.eval import run_datalake as rd

    calls: list[dict] = []
    monkeypatch.setattr(rd, "run_datalake", _stub_run(calls))
    rd.main(_argv(tmp_path, "both"))

    by_split = {c["split"]: c["out_dir"] for c in calls}
    assert by_split["test"].name == "test"
    assert by_split["train"].name == "train"
    assert by_split["test"].parent == by_split["train"].parent


def test_a_single_split_run_keeps_the_flat_layout(monkeypatch, tmp_path):
    """No subdirectory and no gap report when only one split is asked for — the
    existing artifact layout is what every runbook and every prior run directory
    assumes."""
    from governed_bi.eval import run_datalake as rd

    calls: list[dict] = []
    monkeypatch.setattr(rd, "run_datalake", _stub_run(calls))
    rd.main(_argv(tmp_path, "test"))

    assert len(calls) == 1
    only = calls[0]
    assert only["corpus_dir"] == only["out_dir"], "one split scores into its own dir"
    assert not list((tmp_path / "runs").rglob("split_gap.json"))


def test_the_train_split_does_not_decide_the_exit_code(monkeypatch, tmp_path):
    """train is unquotable BY DESIGN, so letting it gate would make every combined run
    exit 2 and the signal stop meaning anything. Only the held-out split gates."""
    from governed_bi.eval import run_datalake as rd

    monkeypatch.setattr(rd, "run_datalake", _stub_run([]))
    assert rd.main(_argv(tmp_path, "both")) == 0

    # ...and a genuinely unquotable held-out split still fails.
    def unquotable(**kwargs):
        return {
            "arms": {}, "treatment_divergence": {}, "comparisons": [], "deltas": {},
            "quotable": False,
        }

    monkeypatch.setattr(rd, "run_datalake", unquotable)
    assert rd.main(_argv(tmp_path, "both")) == 2


def test_both_writes_the_gap_report(monkeypatch, tmp_path):
    from governed_bi.eval import run_datalake as rd

    monkeypatch.setattr(rd, "run_datalake", _stub_run([]))
    rd.main(_argv(tmp_path, "both"))

    gaps = list((tmp_path / "runs").rglob("split_gap.json"))
    assert len(gaps) == 1, "one combined report at the run root"
    assert gaps[0].parent.name not in ("train", "test")


def test_no_resume_still_builds_the_corpus_only_once(monkeypatch, tmp_path):
    """``--no-resume`` must mean "re-score the rows", never "rebuild the treatment".

    It used to mean both: one ``resume`` flag governed the row replay AND the
    corpus-build skip gate, so the second pass re-ran the stochastic deep-agent
    curator into the shared roots. Test then scored against corpus v1 and train
    against v2 — the gap became a mix of overfitting and curator variance, measuring
    neither — and the run paid twice for its dominant cost.
    """
    from governed_bi.eval import run_datalake as rd

    calls: list[dict] = []
    monkeypatch.setattr(rd, "run_datalake", _stub_run(calls))
    rd.main([*_argv(tmp_path, "both"), "--no-resume"])

    assert [c["resume"] for c in calls] == [False, False], "rows stay honestly clean"
    # The build gate is ``resume or reuse_corpus``: the first split builds, every
    # later one adopts what is on disk.
    assert [c["reuse_corpus"] for c in calls] == [False, True]
    assert len({c["corpus_dir"] for c in calls}) == 1


def test_a_single_split_run_never_reuses_a_corpus(monkeypatch, tmp_path):
    """Nothing to adopt, and ``--no-resume`` on one split must still rebuild."""
    from governed_bi.eval import run_datalake as rd

    calls: list[dict] = []
    monkeypatch.setattr(rd, "run_datalake", _stub_run(calls))
    rd.main([*_argv(tmp_path, "test"), "--no-resume"])
    assert calls[0]["reuse_corpus"] is False


def test_resuming_a_single_split_dir_as_both_is_refused(monkeypatch, tmp_path):
    """The two layouts are not compatible: a single-split run keeps its artifacts flat
    and ``both`` keeps them under ``train/`` and ``test/``. Resuming across the two
    leaves the flat ``summary.json`` / ``generations.*.jsonl`` beside the new
    subdirectories, where the resume guard cannot see them (it reads
    ``<split_dir>/manifest.json``, which does not exist yet) — so nothing compares
    knobs and nothing replays the rows already scored."""
    from governed_bi.eval import run_datalake as rd

    prior = tmp_path / "runs" / "20260101T000000Z"
    prior.mkdir(parents=True)
    (prior / "manifest.json").write_text(json.dumps({"split": "test"}), encoding="utf-8")

    calls: list[dict] = []
    monkeypatch.setattr(rd, "run_datalake", _stub_run(calls))
    with pytest.raises(SystemExit):
        rd.main([*_argv(tmp_path, "both"), "--resume-from", str(prior)])
    assert not calls, "refused before any serve"

    # ...and resuming a genuine ``--split both`` directory (no flat manifest) is fine.
    both_dir = tmp_path / "runs" / "20260102T000000Z"
    (both_dir / "test").mkdir(parents=True)
    rd.main([*_argv(tmp_path, "both"), "--resume-from", str(both_dir)])
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# The same-corpora invariant, made detectable
# --------------------------------------------------------------------------- #


def _split_dirs(tmp_path, *, train_hash=None, test_hash=None):
    for name, digest in (("train", train_hash), ("test", test_hash)):
        d = tmp_path / name
        d.mkdir()
        (d / "summary.json").write_text(
            json.dumps(
                _summary(curated={"n": 100, "ex_lenient": 0.9 if name == "train" else 0.5})
            ),
            encoding="utf-8",
        )
        if digest is not None:
            (d / "manifest.json").write_text(
                json.dumps({"split": name, "corpus_content_hash": digest}),
                encoding="utf-8",
            )
    return tmp_path / "train", tmp_path / "test"


def test_two_different_corpora_degrade_the_report_instead_of_gapping_them(tmp_path):
    """The failure mode that matters: a silent wrong number. Nothing here used to read
    the manifest at all, so a rebuild between the splits produced a confident gap
    across two curator draws."""
    train_dir, test_dir = _split_dirs(tmp_path, train_hash="bbbb", test_hash="aaaa")

    report = write_split_gap(tmp_path, train_dir, test_dir)
    assert "arms" not in report, "no gap is computed across two corpora"
    assert "aaaa" in report["error"] and "bbbb" in report["error"]
    assert "unavailable" in format_split_gap(report)
    # ...and it is on disk in the degraded shape, not missing.
    assert json.loads((tmp_path / "split_gap.json").read_text(encoding="utf-8")) == report


def test_one_corpus_records_its_hash_alongside_the_gap(tmp_path):
    train_dir, test_dir = _split_dirs(tmp_path, train_hash="aaaa", test_hash="aaaa")
    report = write_split_gap(tmp_path, train_dir, test_dir)
    assert report["corpus_content_hash"] == "aaaa"
    assert report["arms"]["curated"]["gap"]["ex_lenient"] == pytest.approx(0.4)
    assert "corpus_hash_unverified" not in report


def test_an_unrecorded_hash_says_so_rather_than_refusing(tmp_path):
    """Absent is not mismatched. A directory with no manifest cannot be checked, and
    degrading there would throw away a gap that is probably fine — so the report
    computes it and carries the caveat."""
    train_dir, test_dir = _split_dirs(tmp_path, train_hash="aaaa")
    report = write_split_gap(tmp_path, train_dir, test_dir)
    assert report["arms"]["curated"]["gap"]["ex_lenient"] == pytest.approx(0.4)
    assert "test" in report["corpus_hash_unverified"]
    assert "!" in format_split_gap(report)


# --------------------------------------------------------------------------- #
# What the gap is worth: the control arm's gap, and the noise floor
# --------------------------------------------------------------------------- #


def test_the_excess_gap_subtracts_the_control_arms_split_difficulty():
    """``baseline`` reads no train gold SQL, so it has nothing to memorise and its
    whole gap is split-composition difficulty. The honest memorisation quantity is
    therefore the difference of the two gaps, not the raw one."""
    train = _summary(
        baseline={"n": 1000, "ex_lenient": 0.40},
        curated={"n": 1000, "ex_lenient": 0.70},
    )
    test = _summary(
        baseline={"n": 1000, "ex_lenient": 0.30},
        curated={"n": 1000, "ex_lenient": 0.55},
    )
    arms = split_gap(train, test)["arms"]

    assert arms["curated"]["gap"]["ex_lenient"] == pytest.approx(0.15)
    # 0.15 raw looks like memorisation; 0.05 of it is what the control cannot explain.
    assert arms["curated"]["ex_excess_gap"] == pytest.approx(0.05)
    assert "ex_excess_gap" not in arms[CONTROL_ARM], "the control is the reference"


def test_a_gap_inside_the_noise_floor_is_flagged_as_such():
    """The report tells the reader to treat a sign as informative. At small n a sign is
    a coin flip, so the threshold ships with the number."""
    tiny = split_gap(
        _summary(baseline={"n": 20, "ex_lenient": 0.35}),
        _summary(baseline={"n": 20, "ex_lenient": 0.30}),
    )["arms"][CONTROL_ARM]
    assert tiny["ex_gap_within_noise"] is True
    assert tiny["ex_gap_noise_floor"] > 0.05

    big = split_gap(
        _summary(baseline={"n": 8000, "ex_lenient": 0.35}),
        _summary(baseline={"n": 2000, "ex_lenient": 0.30}),
    )["arms"][CONTROL_ARM]
    assert big["ex_gap_within_noise"] is False
    assert big["ex_gap_noise_floor"] < 0.05


def test_no_standard_error_is_claimed_for_a_conditional_rate():
    """``conditional_ex_lenient`` and friends are denominated on a subset that is not
    in this block, so an SE computed from ``n`` would overstate their precision."""
    block = split_gap(
        _summary(baseline={"n": 100, "ex_lenient": 0.4, "conditional_ex_lenient": 0.8}),
        _summary(baseline={"n": 100, "ex_lenient": 0.3, "conditional_ex_lenient": 0.5}),
    )["arms"][CONTROL_ARM]
    assert "ex_gap_se" in block
    assert not any(k.startswith("conditional_ex") for k in block if k.endswith("_se"))


def test_an_unmeasured_n_gives_no_standard_error_rather_than_a_perfect_one():
    """A missing denominator must not read as certainty: ``0.0`` would declare the gap
    exact, which is the opposite of what an absent ``n`` means."""
    block = split_gap(
        _summary(baseline={"ex_lenient": 0.4}),
        _summary(baseline={"ex_lenient": 0.3}),
    )["arms"][CONTROL_ARM]
    assert "ex_gap_se" not in block
    assert "ex_gap_within_noise" not in block


def test_the_reading_note_names_the_excess_gap_and_the_floor():
    reading = split_gap(_summary(), _summary())["reading"]
    assert "ex_excess_gap" in reading
    assert "ex_gap_noise_floor" in reading


def test_the_stdout_line_rejects_a_bool_the_way_the_gap_does():
    """``_gap`` deliberately refuses a bool; the formatter used to accept one, so a
    flag that drifted into a gapped key printed as ``1.000`` beside a ``None`` gap —
    a rate and its gap disagreeing about whether the thing was measurable."""
    line = format_split_gap(
        split_gap(
            _summary(curated={"n": 1, "ex_lenient": True}),
            _summary(curated={"n": 1, "ex_lenient": 0.5}),
        )
    )
    assert "train=n/a" in line
    assert "1.000" not in line


# --------------------------------------------------------------------------- #
# Summary blocks the same audit touched. They live here because this is the test
# file that owns them; the computations are in ``run_datalake``.
# --------------------------------------------------------------------------- #


def _row(qid, **over):
    r = {
        "arm": "curated", "question_id": qid, "db_id": "beer", "correct": True,
        "outcome": "answered", "generated_sql": "SELECT 1 FROM t",
        "tier": "governed", "semantic_assurance": "unflagged",
        "n_notes_injected": 1, "by_guardrail_layer": {"syntax": 0},
    }
    r.update(over)
    return r


def test_conditional_ex_is_drawn_from_the_rows_it_divides_by():
    """The identical population mix ``cond_ex_given_routing`` was rewritten to remove:
    the numerator counted EVERY correct row while the denominator counted only rows
    that emitted SQL. It was safe solely via the unasserted ``not sql -> not correct``
    invariant, and it exceeds 1.0 the moment anything marks a row correct without SQL —
    a grading free pass, a replayed row from an older shape, a future oracle rung."""
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [
        _row("q1", correct=False),  # produced SQL, wrong
        _row("q2", generated_sql=None, outcome="refused", refused_by="refuse_gate"),
    ]
    s = _summarise_rows("curated", rows)
    assert s["conditional_ex_lenient"] == 0.0, "0 of the 1 row that produced SQL"
    assert s["conditional_ex_lenient"] <= 1.0


def test_the_calibration_blocks_count_the_rows_they_excluded():
    """A confident calibration line over an unknown fraction of the arm is the defect:
    excluding unstamped rows is right, saying nothing about how many there were is
    not. The count sits INSIDE the block so it travels with the numbers it qualifies."""
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [_row("q1"), _row("q2", semantic_assurance=None, tier=None)]
    s = _summarise_rows("curated", rows)
    assert s["ex_by_semantic_assurance"]["n_unstamped"] == 1
    assert s["ex_by_tier"]["n_unstamped"] == 1
    assert s["ex_by_semantic_assurance"]["unflagged"]["n"] == 1


def test_the_guardrail_ceiling_counts_the_rows_that_recorded_a_layer():
    """``n_blocked: 0`` has two readings — nothing was blocked, or nothing was
    instrumented — and only the first is a governance result."""
    from governed_bi.eval.run_datalake import _guardrail_ceiling

    block = _guardrail_ceiling([_row("q1"), _row("q2", by_guardrail_layer=None)])
    assert block["n_observed"] == 1
    assert block["n_blocked"] == 0


def test_share_with_a_note_publishes_its_own_denominator():
    """It is declared over all scored rows and computed over the rows that recorded
    injection. Without the count, three measured rows out of two thousand publish a
    share indistinguishable from one measured over the whole arm."""
    from governed_bi.eval.run_datalake import _summarise_rows

    rows = [_row("q1")] + [_row(f"q{i}", n_notes_injected=None) for i in range(2, 6)]
    s = _summarise_rows("curated", rows)
    assert s["share_with_a_note"] == 1.0
    assert s["n_notes_observed"] == 1, "...over one row out of five"


def test_the_stamp_and_split_helpers_are_testable_without_a_summary():
    """Lifted out of the 677-line ``_summarise_rows`` for the same reason
    ``_guardrail_ceiling`` was: a block whose denominator is the whole point should be
    checkable without building a whole summary."""
    from governed_bi.eval.run_datalake import _ex_by_stamp, _positive, _split

    rows = [_row("q1"), _row("q2", tier="lineage", correct=False), _row("q3", tier=None)]
    by_tier = _ex_by_stamp(rows, "tier")
    assert by_tier["governed"] == {"n": 1, "ex_lenient": 1.0}
    assert by_tier["lineage"]["ex_lenient"] == 0.0
    assert by_tier["n_unstamped"] == 1

    block = _split(rows, _positive("n_notes_injected"), "correct")
    assert (block["n_with"], block["n_without"], block["n_unstamped"]) == (3, 0, 0)
    assert _positive("n_notes_injected")({}) is None, "absent is unstamped, not False"
    assert _positive("n_notes_injected")({"n_notes_injected": 0}) is False
