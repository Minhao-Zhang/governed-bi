"""Question subsetting (E5) and the capped replicate (E6): the cheap-run flags.

Both exist for the same arithmetic. Observed McNemar discordance between adjacent arms
runs 16-20%, which puts a 1351-question run's minimum detectable effect at 3.23% and
the ENTIRE 2030-question BIRD test split at 2.64% — while the interventions under test
move 1-2pp. Serving 1351 questions to learn something about 131 of them is the waste
these two flags remove.

What is tested here is not the plumbing but the two ways each flag could lie:

- ``--questions``: a 131-question run must not compare against a 1351-question run as
  if they were the same experiment. The subset is chosen for a REASON (the questions an
  intervention could plausibly move), so its EX is a biased sample of the split's, and
  the ledger has to refuse the pair rather than average two different quantities.
- ``--replicate-limit``: a capped replicate must not make small deltas read
  ``resolvable: true``. The failure is named in ``power.detectable_effect_for``'s own
  docstring — evaluate the minimum detectable effect at the REPLICATE's size instead of
  the comparison's and a 300-question replicate turns a 32.6-question threshold into a
  15.3-question one, stamping every delta in between as resolved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governed_bi.eval import metrics
from governed_bi.eval.index import comparable, record_for_run
from governed_bi.eval.power import (
    McNemarResult,
    NoiseFloor,
    comparison_report,
    detectable_effect_for,
)
from governed_bi.eval.run_datalake import (
    _apply_question_subset,
    _check_resume_manifest,
    _narrow_dbs_to_subset,
    _pooled_items,
    _write_question_sample,
    load_question_ids,
    main,
    question_subset_id,
    stratified_sample,
)


def _write_split(dataset_dir: Path, split: str, rows: list[dict]) -> None:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / f"{split}_final.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def _row(db_id: str, qid: str, difficulty: str = "simple") -> dict:
    return {
        "db_id": db_id,
        "question_id": qid,
        "question": f"q for {qid}",
        "difficulty": difficulty,
        "sql_rename": f"SELECT 1 /* {qid} */",
    }


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    """Three schemas, 30 questions, deliberately unbalanced (18 / 9 / 3)."""
    d = tmp_path / "eval_dataset"
    rows = (
        [_row("wide_db", f"w{i}", "simple" if i % 2 else "moderate") for i in range(18)]
        + [_row("mid_db", f"m{i}", "challenging") for i in range(9)]
        + [_row("thin_db", f"t{i}", "simple") for i in range(3)]
    )
    _write_split(d, "test", rows)
    _write_split(d, "train", [_row("wide_db", "train_only_1")])
    return d


# --------------------------------------------------------------------------- #
# E5 — reading a subset
# --------------------------------------------------------------------------- #


def test_the_id_file_tolerates_comments_annotations_and_duplicates(tmp_path):
    path = tmp_path / "probe.txt"
    path.write_text(
        "# a fixed probe set\n"
        "\n"
        "q1\n"
        "q2\t# wide_db, attributed to schema_pick\n"
        "q1\n"
        "   q3   \n"
        "   # indented comment\n",
        encoding="utf-8",
    )
    # Sorted, deduped: the pool is a set, and a repeated id would make
    # ``correct_by_question`` raise on the arm's own rows.
    assert load_question_ids(path) == ("q1", "q2", "q3")


def test_an_id_file_that_names_nothing_is_refused(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("# only a comment\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no question ids"):
        load_question_ids(path)
    with pytest.raises(FileNotFoundError):
        load_question_ids(tmp_path / "absent.txt")


def test_the_subset_identity_is_stable_and_order_independent():
    a = question_subset_id(["q3", "q1", "q2"])
    b = question_subset_id(["q1", "q2", "q3", "q1"])
    assert a == b
    assert a is not None and a.startswith("3 ids @ ")
    # The count is in the string on purpose: this is what a reader sees in
    # ``comparable()``'s refusal, and two bare digests say nothing.
    assert question_subset_id(["q1", "q2"]) != a
    assert question_subset_id(None) is None
    assert question_subset_id([]) is None


def test_pooled_items_serves_exactly_the_listed_ids(dataset):
    subset = frozenset({"w0", "m3", "t2"})
    pairs = _pooled_items(
        dataset, ["wide_db", "mid_db", "thin_db"], limit=None, split="test",
        question_ids=subset,
    )
    assert {str(item.question_id) for item, _db in pairs} == subset
    # And no subset means no filter.
    assert len(
        _pooled_items(dataset, ["wide_db"], limit=None, split="test")
    ) == 18


def test_a_subset_narrows_the_schemas_built(dataset):
    """The larger saving, and the reason this is not just a serve-loop filter.

    The build phase is one deep-agent curator pass per schema per arm and dominates a
    scale run's cost, so a probe set that touches one schema must not pay for three.
    """
    kept = _narrow_dbs_to_subset(
        dataset,
        ["wide_db", "mid_db", "thin_db"],
        frozenset({"m0", "m1"}),
        split="test",
    )
    assert kept == ["mid_db"]


def test_an_id_that_is_not_in_the_split_stops_the_run(dataset):
    """Serving 2 of 3 requested questions and reporting a number for the result is
    exactly the silent-scope failure --questions exists to remove."""
    with pytest.raises(ValueError, match="not in the 'test' split"):
        _narrow_dbs_to_subset(
            dataset,
            ["wide_db", "mid_db", "thin_db"],
            frozenset({"w0", "typo_9999"}),
            split="test",
        )


def test_a_subset_that_disagrees_with_dbs_stops_the_run(dataset):
    """Scoring the intersection would make the manifest's ``question_subset`` a claim
    about questions the run never served."""
    with pytest.raises(ValueError, match="excluded"):
        _narrow_dbs_to_subset(
            dataset, ["wide_db"], frozenset({"w0", "m0"}), split="test"
        )


# --------------------------------------------------------------------------- #
# E5 — the stratified draw
# --------------------------------------------------------------------------- #


def test_the_draw_is_reproducible_and_proportional(dataset):
    pairs = _pooled_items(
        dataset, ["wide_db", "mid_db", "thin_db"], limit=None, split="test"
    )
    first = stratified_sample(pairs, size=10, seed=7, by="db")
    second = stratified_sample(pairs, size=10, seed=7, by="db")
    assert [str(i.question_id) for i, _ in first] == [
        str(i.question_id) for i, _ in second
    ]
    assert len(first) == 10
    counts = {}
    for _item, db in first:
        counts[db] = counts.get(db, 0) + 1
    # 18 / 9 / 3 of 30, ten drawn -> 6 / 3 / 1 exactly.
    assert counts == {"wide_db": 6, "mid_db": 3, "thin_db": 1}
    # Stratified, not "first N": a flat head of the pool would be all wide_db, and a
    # discordance rate measured on one schema is not the pool's rate.
    assert counts["thin_db"] > 0


def test_a_different_seed_draws_a_different_sample(dataset):
    pairs = _pooled_items(dataset, ["wide_db"], limit=None, split="test")
    a = {str(i.question_id) for i, _ in stratified_sample(pairs, size=6, seed=1)}
    b = {str(i.question_id) for i, _ in stratified_sample(pairs, size=6, seed=2)}
    assert a != b
    assert len(a) == len(b) == 6


def test_asking_for_everything_returns_everything(dataset):
    pairs = _pooled_items(dataset, ["thin_db"], limit=None, split="test")
    assert len(stratified_sample(pairs, size=99, seed=0)) == 3
    assert stratified_sample(pairs, size=0, seed=0) == []


def test_difficulty_stratification_balances_on_difficulty(dataset):
    pairs = _pooled_items(dataset, ["wide_db", "mid_db"], limit=None, split="test")
    drawn = stratified_sample(pairs, size=9, seed=3, by="difficulty")
    seen = {str(i.difficulty) for i, _ in drawn}
    assert seen == {"simple", "moderate", "challenging"}


def test_an_unknown_stratification_is_refused(dataset):
    pairs = _pooled_items(dataset, ["thin_db"], limit=None, split="test")
    with pytest.raises(ValueError, match="unknown stratification"):
        stratified_sample(pairs, size=1, seed=0, by="phase_of_the_moon")


def test_the_generator_writes_a_file_the_subset_loader_reads_back(tmp_path, dataset):
    """The round trip is the point: a probe set is only reusable if the thing that
    writes it and the thing that consumes it agree on the format."""
    out = tmp_path / "probes" / "probe.txt"
    code = _write_question_sample(
        bird_dir=dataset.parent,
        out_path=out,
        split="test",
        size=10,
        seed=7,
        stratify="db",
        db_ids=None,
        limit_dbs=None,
    )
    assert code == 0
    ids = load_question_ids(out)
    assert len(ids) == 10
    # The header records how to regenerate it, and the identity the manifest will
    # carry, so a reviewer can tell a principled sample from a hand-edited list.
    header = out.read_text(encoding="utf-8")
    assert "seed=7" in header and "stratify=db" in header
    assert question_subset_id(ids) in header
    # Same flags, same file.
    again = tmp_path / "again.txt"
    _write_question_sample(
        bird_dir=dataset.parent, out_path=again, split="test", size=10, seed=7,
        stratify="db", db_ids=None, limit_dbs=None,
    )
    assert again.read_text(encoding="utf-8") == out.read_text(encoding="utf-8")


def test_a_sample_of_everything_is_refused(tmp_path, dataset):
    with pytest.raises(ValueError, match="exceeds"):
        _write_question_sample(
            bird_dir=dataset.parent, out_path=tmp_path / "x.txt", split="test",
            size=999, seed=0, stratify="db", db_ids=None, limit_dbs=None,
        )


# --------------------------------------------------------------------------- #
# E5 — comparability. The part that must fail loud.
# --------------------------------------------------------------------------- #


def _run_record(tmp_path: Path, name: str, **manifest_over) -> dict:
    """A ledger record for a run whose manifest is built through the real register."""
    base = dict(
        mode="datalake",
        bird_dir="/data/bird",
        split="test",
        model_name="gpt-5.6-luna",
        llm_reasoning_effort="high",
        embedding_model="text-embedding-3-large",
        embedding_dimensions=None,
        prompt_variants={},
        created_at_utc=name,
        route_top_k=10,
        route_llm_pick=True,
        schema_pick_max_columns=12,
        use_embedder=True,
        llm_temperature=None,
        question_pool_hash="pool_full",
        question_subset=None,
        always_note_global_max=8,
        always_note_char_max=2000,
        pin_triggers_enabled=False,
        pin_require_certified=True,
        pin_max=3,
        arms=("baseline", "curated"),
        oracles=(),
        replicate_of="curated",
        replicate_limit=None,
        replicate_sample_seed=None,
        db_ids=None,
        limit=None,
        limit_dbs=None,
        question_scope_hash="scope_full",
    )
    base.update(manifest_over)
    manifest = metrics.build_manifest(**base)  # type: ignore[arg-type]
    manifest["corpus_content_hash"] = "corpus0"
    run_dir = tmp_path / name
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "mode": "datalake",
                "split": "test",
                "n_questions": 1351,
                "arms": {"baseline": {"n": 1351, "ex_lenient": 0.4, "crash_rate": 0.0}},
            }
        ),
        encoding="utf-8",
    )
    return record_for_run(run_dir)


def test_a_subset_run_is_not_comparable_to_a_full_run(tmp_path):
    """The headline case. 131 questions picked because an intervention could move them
    is a biased sample of 1351, so the two runs' EX are different quantities."""
    full = _run_record(tmp_path, "20260801T000000Z")
    subset = _run_record(
        tmp_path,
        "20260801T010000Z",
        question_subset=question_subset_id([f"q{i}" for i in range(131)]),
        # A real subset run's pool hash and scope hash move too — recorded here so the
        # test reflects what the driver writes rather than an isolated key.
        question_pool_hash="pool_subset",
        question_scope_hash="scope_subset",
    )
    ok, diffs = comparable(full, subset)
    assert not ok
    # Both gates fire, and that is deliberate: ``question pool`` refuses the pair on
    # content, ``question subset`` says in words WHY, which is what a reader can act on.
    assert any(d.startswith("question subset:") for d in diffs), diffs
    assert any("131 ids @" in d for d in diffs), diffs
    assert any(d.startswith("question pool:") for d in diffs), diffs


def test_two_runs_over_the_same_probe_set_stay_comparable(tmp_path):
    """The other half. A fixed probe set is worth having only if successive runs over
    it can be compared — that is the whole reason to commit the file."""
    probe = question_subset_id([f"q{i}" for i in range(131)])
    a = _run_record(
        tmp_path, "20260801T020000Z", question_subset=probe,
        question_pool_hash="pool_subset", question_scope_hash="scope_subset",
    )
    b = _run_record(
        tmp_path, "20260801T030000Z", question_subset=probe,
        question_pool_hash="pool_subset", question_scope_hash="scope_subset",
    )
    ok, diffs = comparable(a, b)
    assert ok, diffs


def test_two_different_probe_sets_of_the_same_size_are_not_comparable(tmp_path):
    """The count alone is not the identity: 131 questions attributed to ``schema_pick``
    and 131 attributed to note retrieval are different experiments."""
    a = _run_record(
        tmp_path, "20260801T040000Z",
        question_subset=question_subset_id([f"a{i}" for i in range(131)]),
        question_pool_hash="pool_a", question_scope_hash="scope_a",
    )
    b = _run_record(
        tmp_path, "20260801T050000Z",
        question_subset=question_subset_id([f"b{i}" for i in range(131)]),
        question_pool_hash="pool_b", question_scope_hash="scope_b",
    )
    ok, diffs = comparable(a, b)
    assert not ok
    assert any(d.startswith("question subset:") for d in diffs), diffs


def test_a_capped_replicate_does_not_make_two_runs_incomparable(tmp_path):
    """``replicate_limit`` is SCOPE, not a knob, and the distinction is load-bearing.

    It changes how precisely the run estimated its own noise; it changes nothing about
    what any fair arm served. Gating on it would refuse a cheap run against the
    expensive baseline it exists to be compared with — which would remove the entire
    saving the flag was added for.
    """
    full = _run_record(tmp_path, "20260801T060000Z")
    capped = _run_record(
        tmp_path, "20260801T070000Z", replicate_limit=300, replicate_sample_seed=0
    )
    ok, diffs = comparable(full, capped)
    assert ok, diffs


def test_the_manifest_records_the_cap_and_its_seed(tmp_path):
    record = _run_record(
        tmp_path, "20260801T080000Z", replicate_limit=300, replicate_sample_seed=11
    )
    manifest = json.loads(
        (tmp_path / "20260801T080000Z" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["replicate_limit"] == 300
    assert manifest["replicate_sample_seed"] == 11
    assert record["question_subset"] is None
    # A cap with no replicate describes a draw that never happened.
    no_replicate = metrics.build_manifest(
        **{
            **dict(
                mode="datalake", bird_dir="/d", split="test", model_name="m",
                llm_reasoning_effort=None, embedding_model=None,
                embedding_dimensions=None, prompt_variants={},
                created_at_utc="20260801T090000Z", route_top_k=3, route_llm_pick=False,
                schema_pick_max_columns=12, use_embedder=True, llm_temperature=None,
                question_pool_hash="p", question_subset=None, always_note_global_max=8,
                always_note_char_max=2000, pin_triggers_enabled=False,
                pin_require_certified=True, pin_max=3, arms=("baseline",), oracles=(),
                replicate_of=None, replicate_limit=300, replicate_sample_seed=4,
                db_ids=None, limit=None, limit_dbs=None, question_scope_hash=None,
            )
        }  # type: ignore[arg-type]
    )
    assert no_replicate["replicate_limit"] is None
    assert no_replicate["replicate_sample_seed"] is None


# --------------------------------------------------------------------------- #
# E6 — a capped replicate must not soften the resolution bar
# --------------------------------------------------------------------------- #


def test_a_capped_replicate_is_evaluated_at_the_comparisons_population():
    """The exact failure ``power.detectable_effect_for``'s docstring names.

    A 300-question replicate at 18% discordance has 54 discordant pairs, so evaluating
    the threshold there gives ``2.80 * sqrt(54) = 20.6`` questions. The honest threshold
    for a 1351-question comparison at the same RATE is ``2.80 * sqrt(243.2) = 43.7``.
    Every delta between those two numbers is one that would read ``resolvable: true``
    off the wrong population.
    """
    import math

    pooled = McNemarResult(
        arm_a="baseline", arm_b="curated", n_shared=1351,
        n_b_only=140, n_a_only=110, p_value=0.06,
    )
    capped = NoiseFloor(n_pairs=300, n_discordant=54, net=2)
    mde = detectable_effect_for(pooled, capped)
    assert mde is not None
    # Evaluated at the COMPARISON's population.
    assert mde.n_pairs == 1351
    assert mde.questions == pytest.approx(2.8016 * math.sqrt(1351 * 0.18), rel=1e-3)
    # A full replicate at the same rate gives the same threshold — the rate travels,
    # the population does not.
    full = NoiseFloor(n_pairs=1351, n_discordant=243, net=3)
    assert detectable_effect_for(pooled, full).questions == pytest.approx(
        mde.questions, rel=2e-3
    )


def test_a_delta_inside_the_noise_stays_unresolvable_under_a_cap():
    """The consequence, stated as the verdict a reader acts on. A net of +30 questions
    clears the replicate-sized threshold (20.6) and not the honest one (43.7)."""
    pooled = McNemarResult(
        arm_a="baseline", arm_b="curated", n_shared=1351,
        n_b_only=140, n_a_only=110, p_value=0.06,
    )
    capped = NoiseFloor(n_pairs=300, n_discordant=54, net=2)
    report = comparison_report(pooled, detectable_effect_for(pooled, capped), capped)
    assert report["net_questions"] == 30
    assert report["resolvable"] is False
    assert "below resolution" in report["reading"]


def test_a_capped_floor_says_so_in_the_artifact_and_in_words():
    """``floor_n_pairs`` alone was not enough: it is one field among twenty, and the
    sentence a reader quotes is ``reading``."""
    pooled = McNemarResult(
        arm_a="baseline", arm_b="curated", n_shared=1351,
        n_b_only=200, n_a_only=110, p_value=0.001,
    )
    capped = NoiseFloor(n_pairs=300, n_discordant=54, net=2)
    report = comparison_report(pooled, detectable_effect_for(pooled, capped), capped)
    detectable = report["detectable"]
    assert detectable["floor_n_pairs"] == 300
    assert detectable["floor_coverage"] == pytest.approx(300 / 1351, abs=1e-4)
    assert detectable["floor_is_subsampled"] is True
    assert report["resolvable"] is True
    # ...and the caveat travels with the verdict, not only with the field.
    assert "300 replicate questions" in report["reading"]
    assert "1351" in report["reading"]
    # The noise floor block still reports the replicate's own size, so a reader can
    # tell a 300-question rate estimate from a 1351-question one without arithmetic.
    assert report["noise_floor"]["n_pairs"] == 300


def test_a_full_replicate_carries_no_caveat():
    pooled = McNemarResult(
        arm_a="baseline", arm_b="curated", n_shared=1351,
        n_b_only=200, n_a_only=110, p_value=0.001,
    )
    full = NoiseFloor(n_pairs=1351, n_discordant=243, net=3)
    report = comparison_report(pooled, detectable_effect_for(pooled, full), full)
    assert report["detectable"]["floor_is_subsampled"] is False
    assert report["reading"] == "resolvable"


def test_a_replicate_larger_than_a_stratum_is_not_called_subsampled():
    """``no_twin`` is 1085 shared rows against a 1351-question replicate. The floor
    covers MORE than the stratum, which is the conservative direction and not a
    caveat — flagging it would train readers to ignore the flag."""
    stratum = McNemarResult(
        arm_a="baseline", arm_b="curated", n_shared=1085,
        n_b_only=150, n_a_only=120, p_value=0.08,
    )
    full = NoiseFloor(n_pairs=1351, n_discordant=243, net=3)
    mde = detectable_effect_for(stratum, full)
    assert mde.n_pairs == 1085
    assert mde.floor_is_subsampled is False


# --------------------------------------------------------------------------- #
# Resume: neither flag may change mid-directory
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key,value,flag",
    [
        ("question_subset", "131 ids @ deadbeefdeadbeef", "--questions"),
        ("replicate_limit", 300, "--replicate-limit"),
        ("replicate_sample_seed", 9, "--sample-seed"),
    ],
)
def test_changing_either_flag_on_a_resume_is_fatal(tmp_path, key, value, flag):
    """Rows served under a 300-question replicate cap and rows served under none sit in
    one generations file with nothing distinguishing them, and the top-level manifest
    shows only the first configuration."""
    prior = {
        "split": "test", "arms": ["baseline"], "oracles": [], "replicate_of": "baseline",
        "db_ids": None, "limit": None, "limit_dbs": None,
        "question_scope_hash": "scope0", "question_subset": None,
        "replicate_limit": None, "replicate_sample_seed": None,
    }
    (tmp_path / "manifest.json").write_text(json.dumps(prior), encoding="utf-8")
    with pytest.raises(RuntimeError, match=flag):
        _check_resume_manifest(tmp_path, {**prior, key: value})


def test_an_unchanged_scope_resumes_cleanly(tmp_path):
    prior = {
        "split": "test", "arms": ["baseline"], "oracles": [], "replicate_of": "baseline",
        "db_ids": None, "limit": None, "limit_dbs": None,
        "question_scope_hash": "scope0", "question_subset": "3 ids @ abcd",
        "replicate_limit": 300, "replicate_sample_seed": 0,
    }
    (tmp_path / "manifest.json").write_text(json.dumps(prior), encoding="utf-8")
    _check_resume_manifest(tmp_path, dict(prior))


# --------------------------------------------------------------------------- #
# CLI: the combinations that must be refused, and the help string that must render
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "argv,fragment",
    [
        (["--questions", "p.txt", "--limit", "5"], "contradictory"),
        (["--replicate-limit", "300"], "needs --replicate"),
        (["--replicate", "curated", "--replicate-limit", "0"], "at least 1"),
        (["--sample-size", "10"], "only applies"),
        (["--write-question-sample", "s.txt"], "needs --sample-size"),
        (
            ["--write-question-sample", "s.txt", "--sample-size", "5", "--split", "both"],
            "ONE split",
        ),
    ],
)
def test_the_refused_flag_combinations(argv, fragment, capsys):
    with pytest.raises(SystemExit):
        main(argv)
    assert fragment in capsys.readouterr().err


def test_help_renders(capsys):
    """A bare ``%`` in a help string makes argparse's interpolation blow up at --help,
    which is a crash in the one command an operator reaches for first. It has happened."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--questions", "--replicate-limit", "--write-question-sample"):
        assert flag in out


def test_the_subset_filter_is_a_no_op_without_a_subset(dataset):
    pairs = _pooled_items(dataset, ["thin_db"], limit=None, split="test")
    assert _apply_question_subset(pairs, None) == pairs
    assert _apply_question_subset(pairs, frozenset({"t0"})) != pairs
