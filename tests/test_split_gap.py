"""Train-vs-test gap: the diagnostic, and the traps in reporting it.

Scoring train is not a second result — the curator reads train gold SQL, so a
curated arm's train EX is partly recall of what it was built from, and
``eval.index.quotable`` already refuses a train-scored run. What the pair buys is
the gap: how much of an arm's score does not survive a new question.
"""

from __future__ import annotations

import json

from governed_bi.eval.split_gap import (
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
    noise as overfitting, so the set is explicit rather than "every rate"."""
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
