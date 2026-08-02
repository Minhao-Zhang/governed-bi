"""``scripts/load_run_db.py`` survives the run directories that actually exist.

The loader's whole value is that it reads *degenerate* runs. A loader that only
handles the happy path would be useless precisely when it matters: the runs worth
querying are the ones that crashed, resumed, or lost half their artifacts. Two of
the eleven run directories on disk have stage events and no generations at all,
because a worktree cleanup deleted the ``generations.*.jsonl`` — and
``stage_events.jsonl`` alone reconstructed the crash diagnosis.

So these tests pin the failure modes, not the feature list:

* re-loading must replace, never duplicate (an analyst reloads after every run);
* a missing arm, a missing ``questions.jsonl``, an empty ``stage_events.jsonl``, an
  ``arms: []`` manifest and an unfinished run must all load and self-report;
* the per-turn ``seq`` must be read when present and derived when absent, with
  ``seq_derived`` saying which — deriving silently is how an ordering bug hides;
* ``stage`` must stay an open vocabulary (the 20260801 runs emit
  ``sql_normalisation``, which ``governed_bi.stages.Stage`` does not declare);
* fields the loader does not promote must survive in ``row_json``, because the
  emitted field set differs per run (73 fields on one run, 78 on another).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "load_run_db.py"


@pytest.fixture(scope="module")
def loader():
    spec = importlib.util.spec_from_file_location("load_run_db", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def _gen_row(qid: str, arm: str, **overrides):
    row = {
        "question_id": qid,
        "db_id": "address",
        "arm": arm,
        "split": "test",
        "generated_sql": f"SELECT {qid}",
        "correct": True,
        "outcome": "answered",
        "failed_stage": None,
        "refused_by": None,
        "latency_sec": 1.5,
        "pick_hit": True,
        "routed_hit": True,
        "token_sum": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        "n_tool_calls": {"run_query": 2, "inspect_schema": 1},
    }
    row.update(overrides)
    return row


def _event(qid: str, arm: str, stage: str, *, turn_id: str = "t1", **overrides):
    event = {
        "question_id": qid,
        "arm": arm,
        "db_id": "address",
        "run_id": "r1",
        "turn_id": turn_id,
        "stage": stage,
        "status": "ok",
        "ms": 1.0,
        "detail": {},
    }
    event.update(overrides)
    return event


def make_run(
    root: Path,
    name: str,
    *,
    arms=("baseline",),
    manifest_arms=None,
    questions: bool = True,
    events=None,
    gen_rows=None,
    completed: str | None = "20260801T000000Z",
    model: str = "test-model",
) -> Path:
    """A minimal but structurally faithful run directory."""
    run = root / name
    run.mkdir(parents=True, exist_ok=True)
    manifest = {
        "manifest_schema_version": 3,
        "mode": "datalake",
        "arms": list(arms if manifest_arms is None else manifest_arms),
        "model": model,
        "split": "test",
        "corpus_content_hash": "cafe1234",
        "prompt_set_hash": "beef5678",
        "created_at_utc": "20260801T000000Z",
        "resumes": [{"created_at_utc": "20260801T001000Z", "arms": list(arms)}],
    }
    if completed is not None:
        manifest["completed_at_utc"] = completed
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    if questions:
        _write_jsonl(
            run / "questions.jsonl",
            [
                {
                    "question_id": "q1",
                    "db_id": "address",
                    "question": "how many?",
                    "gold_sql": "SELECT COUNT(*) FROM t",
                    "evidence": "count means COUNT(*)",
                    "difficulty": "",
                    "split": "test",
                },
                {
                    "question_id": "q2",
                    "db_id": "address",
                    "question": "how few?",
                    "gold_sql": "SELECT MIN(x) FROM t",
                    "evidence": "",
                    "difficulty": "",
                    "split": "test",
                },
            ],
        )

    for arm in arms:
        rows = gen_rows[arm] if gen_rows and arm in gen_rows else [
            _gen_row("q1", arm),
            _gen_row("q2", arm, correct=False, outcome="crashed", generated_sql=None),
        ]
        _write_jsonl(run / f"generations.{arm}.jsonl", rows)

    if events is None:
        events = [
            _event("q1", arms[0] if arms else "baseline", stage)
            for stage in ("route", "schema_pick", "assemble", "agent_core")
        ]
    _write_jsonl(run / "stage_events.jsonl", events)
    return run


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


def test_reloading_the_same_run_replaces_rather_than_duplicates(loader, tmp_path):
    run = make_run(tmp_path / "runs", "r1")
    db = tmp_path / "a.db"
    conn = loader.connect(db)
    try:
        first = loader.load_run(conn, run, index={})
        counts_first = _counts(conn)
        second = loader.load_run(conn, run, index={})
        counts_second = _counts(conn)
    finally:
        conn.close()

    assert first.n_turns == second.n_turns == 2
    assert counts_first == counts_second == (1, 2, 4)


def test_reload_drops_rows_that_no_longer_exist_on_disk(loader, tmp_path):
    """A shrinking artifact must shrink the table, not leave orphans behind.

    A loader that only INSERT-OR-REPLACEd would keep the deleted arm's turns forever,
    and every per-arm aggregate would quietly include a run that no longer has it.
    """
    root = tmp_path / "runs"
    run = make_run(root, "r1", arms=("baseline", "curated"))
    db = tmp_path / "a.db"
    conn = loader.connect(db)
    try:
        loader.load_run(conn, run, index={})
        assert _counts(conn)[1] == 4
        (run / "generations.curated.jsonl").unlink()
        report = loader.load_run(conn, run, index={})
        arms = [r[0] for r in conn.execute("SELECT DISTINCT arm FROM turns")]
    finally:
        conn.close()
    assert arms == ["baseline"]
    assert report.arms_missing == ["curated"]


# --------------------------------------------------------------------------- #
# Degenerate runs
# --------------------------------------------------------------------------- #


def test_partial_run_with_events_but_no_generations_still_loads(loader, tmp_path):
    """The case that actually happened: generations lost, stage events intact.

    Two provider-b ladders on disk are exactly this. The events alone carry the crash
    diagnosis (``agent_core``/``error``/``APIStatusError``), so refusing the run — or
    loading it with no note that its turns are absent — both destroy the only record.
    """
    root = tmp_path / "runs"
    run = make_run(
        root,
        "partial",
        arms=(),
        manifest_arms=["baseline", "seeded"],
        completed=None,
        events=[
            _event("q1", "baseline", "route"),
            _event("q1", "baseline", "agent_core", status="error",
                   detail={"error_type": "APIStatusError"}),
            _event("q2", "seeded", "route"),
        ],
    )
    conn = loader.connect(tmp_path / "a.db")
    try:
        report = loader.load_run(conn, run, index={})
        n_turns, n_events = conn.execute(
            "SELECT n_turns, n_events FROM runs"
        ).fetchone()
        errors = conn.execute(
            "SELECT stage, json_extract(detail_json,'$.error_type') FROM events WHERE status='error'"
        ).fetchall()
        notes = json.loads(conn.execute("SELECT notes FROM runs").fetchone()[0])
    finally:
        conn.close()

    assert (n_turns, n_events) == (0, 3)
    assert report.arms_missing == ["baseline", "seeded"]
    assert tuple(errors[0]) == ("agent_core", "APIStatusError")
    assert any("no generations file" in note for note in notes)
    assert any("did not finish" in note for note in notes)


def test_empty_stage_events_file_is_a_note_not_a_failure(loader, tmp_path):
    """The 20260802 oracle run ships a 0-byte ``stage_events.jsonl``."""
    run = make_run(tmp_path / "runs", "oracle", arms=("oracle_sql",))
    (run / "stage_events.jsonl").write_text("", encoding="utf-8")
    conn = loader.connect(tmp_path / "a.db")
    try:
        report = loader.load_run(conn, run, index={})
        seq_source = conn.execute("SELECT seq_source FROM runs").fetchone()[0]
        notes = json.loads(conn.execute("SELECT notes FROM runs").fetchone()[0])
    finally:
        conn.close()
    assert report.n_turns == 2
    assert report.n_events == 0
    assert seq_source is None
    assert any("0 bytes" in note for note in notes)


def test_arm_present_on_disk_but_absent_from_manifest_is_loaded(loader, tmp_path):
    """``arms: []`` with a ``generations.oracle_sql.jsonl`` next to it.

    The manifest's ``arms`` is the *plan*; the files are what happened. Trusting the
    plan would have dropped all 1351 graded oracle rows.
    """
    run = make_run(tmp_path / "runs", "oracle", arms=("oracle_sql",), manifest_arms=[])
    conn = loader.connect(tmp_path / "a.db")
    try:
        report = loader.load_run(conn, run, index={})
        arms = [r[0] for r in conn.execute("SELECT DISTINCT arm FROM turns")]
        notes = json.loads(conn.execute("SELECT notes FROM runs").fetchone()[0])
    finally:
        conn.close()
    assert arms == ["oracle_sql"]
    assert report.arms_missing == []
    assert any("not declared in the manifest" in note for note in notes)


def test_missing_questions_file_leaves_gold_null_and_says_so(loader, tmp_path):
    """Trap #2: ``gold_sql`` lives only in ``questions.jsonl``, which some runs lack."""
    run = make_run(tmp_path / "runs", "nogold", questions=False)
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        golds = [r[0] for r in conn.execute("SELECT gold_sql FROM turns")]
        notes = json.loads(conn.execute("SELECT notes FROM runs").fetchone()[0])
    finally:
        conn.close()
    assert golds == [None, None]
    assert any("no questions.jsonl" in note for note in notes)


def test_gold_is_left_joined_from_questions_when_present(loader, tmp_path):
    run = make_run(tmp_path / "runs", "withgold")
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        row = conn.execute(
            "SELECT gold_sql, question, evidence FROM turns WHERE question_id='q1'"
        ).fetchone()
    finally:
        conn.close()
    assert row["gold_sql"] == "SELECT COUNT(*) FROM t"
    assert row["question"] == "how many?"
    assert row["evidence"] == "count means COUNT(*)"


def test_one_unparseable_line_does_not_lose_the_rest_of_the_file(loader, tmp_path):
    """A run killed mid-append leaves a truncated final line."""
    run = make_run(tmp_path / "runs", "truncated")
    path = run / "generations.baseline.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"question_id": "q3", "arm', encoding="utf-8")
    conn = loader.connect(tmp_path / "a.db")
    try:
        report = loader.load_run(conn, run, index={})
        notes = json.loads(conn.execute("SELECT notes FROM runs").fetchone()[0])
    finally:
        conn.close()
    assert report.n_turns == 2
    assert any("unparseable" in note for note in notes)


def test_an_unparseable_manifest_is_a_note_not_a_load_failure(loader, tmp_path):
    root = tmp_path / "runs"
    broken = make_run(root, "broken")
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")
    conn = loader.connect(tmp_path / "a.db")
    try:
        report = loader.load_run(conn, broken, index={})
        model, notes = conn.execute("SELECT model, notes FROM runs").fetchone()
    finally:
        conn.close()
    assert report.n_turns == 2  # the generations still load
    assert model is None
    assert any("unparseable" in note for note in json.loads(notes))


def test_a_broken_run_does_not_sink_the_others_in_one_cli_invocation(loader, tmp_path):
    """One unreadable directory must not cost the analyst the other ten runs."""
    root = tmp_path / "runs"
    good = make_run(root, "good")
    not_a_run = root / "not_a_run"
    not_a_run.mkdir()
    db = tmp_path / "a.db"
    rc = loader.main(["--db", str(db), "--no-index", str(not_a_run), str(good)])
    conn = loader.connect(db)
    try:
        loaded = [r[0] for r in conn.execute("SELECT run_dir FROM runs")]
    finally:
        conn.close()
    assert rc == 1  # the exit code still reports that something failed
    assert loaded == [loader.canonical_run_dir(good)]


# --------------------------------------------------------------------------- #
# The seq trap
# --------------------------------------------------------------------------- #


def test_seq_is_derived_from_file_order_and_flagged_as_such(loader, tmp_path):
    run = make_run(tmp_path / "runs", "noseq")
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        rows = conn.execute(
            "SELECT seq, seq_derived, stage FROM events ORDER BY file_row"
        ).fetchall()
        seq_source = conn.execute("SELECT seq_source FROM runs").fetchone()[0]
    finally:
        conn.close()
    assert [r["seq"] for r in rows] == [0, 1, 2, 3]
    assert all(r["seq_derived"] == 1 for r in rows)
    assert seq_source == "derived"
    assert [r["stage"] for r in rows] == ["route", "schema_pick", "assemble", "agent_core"]


def test_an_explicit_seq_on_the_row_is_used_verbatim(loader, tmp_path):
    """A concurrent change is adding a per-turn sequence to newly-written rows.

    When it lands, the loader must stop deriving rather than overwrite it — and must
    say so, so a mixed database is still interpretable.
    """
    run = make_run(
        tmp_path / "runs",
        "withseq",
        events=[
            _event("q1", "baseline", "route", seq=7),
            _event("q1", "baseline", "assemble", seq=9),
        ],
    )
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        rows = conn.execute("SELECT seq, seq_derived FROM events ORDER BY file_row").fetchall()
        seq_source = conn.execute("SELECT seq_source FROM runs").fetchone()[0]
    finally:
        conn.close()
    assert [(r["seq"], r["seq_derived"]) for r in rows] == [(7, 0), (9, 0)]
    assert seq_source == "field"


def test_a_run_that_gains_seq_partway_reports_mixed(loader, tmp_path):
    run = make_run(
        tmp_path / "runs",
        "mixed",
        events=[
            _event("q1", "baseline", "route"),
            _event("q2", "baseline", "route", turn_id="t2", seq=0),
        ],
    )
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        seq_source = conn.execute("SELECT seq_source FROM runs").fetchone()[0]
    finally:
        conn.close()
    assert seq_source == "mixed"


def test_derived_seq_restarts_per_turn_so_a_resume_is_not_merged(loader, tmp_path):
    """A resume re-serves a question under a new ``turn_id``, later in the file.

    The 20260801 luna-max ladder has 4,163 turns over 4,053 arm-question pairs. If the
    derived ordinal were keyed on ``(arm, question_id)`` the resume's events would be
    numbered as a continuation of the original turn, and a "what happened on this
    turn" query would splice two different attempts together.
    """
    run = make_run(
        tmp_path / "runs",
        "resumed",
        events=[
            _event("q1", "baseline", "route", turn_id="w0:1"),
            _event("q1", "baseline", "agent_core", turn_id="w0:1", status="error"),
            _event("q2", "baseline", "route", turn_id="w0:2"),
            # the resume: same question, new turn, non-adjacent in the file
            _event("q1", "baseline", "route", turn_id="w3:9"),
            _event("q1", "baseline", "agent_core", turn_id="w3:9"),
        ],
    )
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        rows = conn.execute(
            "SELECT turn_id, seq, stage FROM events WHERE question_id='q1' ORDER BY file_row"
        ).fetchall()
    finally:
        conn.close()
    assert [(r["turn_id"], r["seq"]) for r in rows] == [
        ("w0:1", 0), ("w0:1", 1), ("w3:9", 0), ("w3:9", 1)
    ]


def test_events_without_turn_id_still_load(loader, tmp_path):
    """Runs written before ``turn_id`` existed (20260730) must not be rejected."""
    events = [
        {"question_id": "q1", "arm": "baseline", "db_id": "address",
         "stage": s, "status": "ok", "ms": 1.0, "detail": {}}
        for s in ("route", "assemble")
    ]
    run = make_run(tmp_path / "runs", "old", events=events)
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        rows = conn.execute("SELECT turn_id, seq FROM events ORDER BY file_row").fetchall()
    finally:
        conn.close()
    assert [(r["turn_id"], r["seq"]) for r in rows] == [(None, 0), (None, 1)]


# --------------------------------------------------------------------------- #
# Open vocabularies and per-run field sets
# --------------------------------------------------------------------------- #


def test_an_undeclared_stage_loads_and_is_reported(loader, tmp_path):
    """``sql_normalisation`` is emitted by the 20260801 runs and is not a ``Stage``.

    Rejecting it would make the loader break every time a new tool stage lands, which
    is the opposite of useful — so it loads, and the run says which stages were new.
    """
    run = make_run(
        tmp_path / "runs",
        "newstage",
        events=[_event("q1", "baseline", "sql_normalisation", status="skipped")],
    )
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        stages = [r[0] for r in conn.execute("SELECT stage FROM events")]
        notes = json.loads(conn.execute("SELECT notes FROM runs").fetchone()[0])
    finally:
        conn.close()
    assert stages == ["sql_normalisation"]
    assert any("sql_normalisation" in note for note in notes)


def test_unpromoted_fields_survive_in_row_json(loader, tmp_path):
    """Trap #3: the field set is per-run, so the raw row is the contract."""
    run = make_run(
        tmp_path / "runs",
        "wide",
        gen_rows={"baseline": [_gen_row("q1", "baseline", some_future_field={"a": 1})]},
    )
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        value = conn.execute(
            "SELECT json_extract(row_json, '$.some_future_field.a') FROM turns"
        ).fetchone()[0]
        tool = conn.execute(
            "SELECT json_extract(row_json, '$.n_tool_calls.inspect_schema') FROM turns"
        ).fetchone()[0]
    finally:
        conn.close()
    assert value == 1
    assert tool == 1


def test_two_runs_with_different_field_sets_coexist(loader, tmp_path):
    root = tmp_path / "runs"
    old = make_run(root, "old", gen_rows={"baseline": [_gen_row("q1", "baseline")]})
    new = make_run(
        root, "new",
        gen_rows={"baseline": [_gen_row("q1", "baseline", shortlist_channel="bm25")]},
    )
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, old, index={})
        loader.load_run(conn, new, index={})
        rows = conn.execute(
            "SELECT j.key, COUNT(DISTINCT t.run_dir) FROM turns t, json_each(t.row_json) j "
            "WHERE j.key = 'shortlist_channel' GROUP BY j.key"
        ).fetchall()
        n_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        conn.close()
    assert n_runs == 2
    assert tuple(rows[0]) == ("shortlist_channel", 1)


def test_booleans_are_stored_as_integers_so_sum_correct_works(loader, tmp_path):
    run = make_run(tmp_path / "runs", "bools")
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        total, correct = conn.execute(
            "SELECT COUNT(*), SUM(correct) FROM turns WHERE correct IS NOT NULL"
        ).fetchone()
        crashed = conn.execute(
            "SELECT COUNT(*) FROM turns WHERE outcome = 'crashed'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert (total, correct, crashed) == (2, 1, 1)


def test_token_totals_are_promoted_from_token_sum(loader, tmp_path):
    run = make_run(tmp_path / "runs", "tokens")
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        row = conn.execute(
            "SELECT input_tokens, output_tokens, total_tokens, n_tool_calls_total "
            "FROM turns WHERE question_id='q1'"
        ).fetchone()
    finally:
        conn.close()
    assert tuple(row) == (10, 2, 12, 3)


# --------------------------------------------------------------------------- #
# Discovery and the index join
# --------------------------------------------------------------------------- #


def test_discovery_never_descends_into_corpus_directories(loader, tmp_path):
    """``corpus_<arm>/`` holds ~11,768 small YAML files per ladder run.

    Walking into them turns a two-second load into a filesystem crawl, so discovery
    prunes them — and must not mistake anything inside one for a run.
    """
    root = tmp_path / "runs"
    run = make_run(root, "bundle/20260801T000000Z")
    trap = run / "corpus_baseline" / "nested"
    trap.mkdir(parents=True)
    (trap / "manifest.json").write_text("{}", encoding="utf-8")
    (run / "_staging").mkdir()
    (run / "_staging" / "manifest.json").write_text("{}", encoding="utf-8")

    found = loader.discover_runs(root)
    assert found == [run]


def test_discovery_handles_both_nesting_shapes(loader, tmp_path):
    root = tmp_path / "runs"
    bare = make_run(root, "20260801T000000Z")
    nested = make_run(root, "label/20260801T111111Z")
    assert set(loader.discover_runs(root)) == {bare, nested}


def test_index_row_supplies_quotable_and_headline(loader, tmp_path):
    run = make_run(tmp_path / "runs", "indexed")
    key = loader.canonical_run_dir(run)
    index = {
        key: {
            "run_dir": key,
            "quotable": False,
            "claim_ready": False,
            "headline": {"baseline": {"ex_no_twin": 0.42}},
            "not_quotable_because": ["arms crashed during serve"],
            "n_questions": 1351,
        }
    }
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index=index)
        row = conn.execute(
            "SELECT quotable, claim_ready, "
            "json_extract(headline_json,'$.baseline.ex_no_twin'), not_quotable_because "
            "FROM runs"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 0 and row[1] == 0
    assert row[2] == 0.42
    assert "crashed" in row[3]


def test_a_run_absent_from_the_index_loads_with_a_null_verdict(loader, tmp_path):
    run = make_run(tmp_path / "runs", "unindexed")
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, run, index={})
        quotable, notes = conn.execute("SELECT quotable, notes FROM runs").fetchone()
    finally:
        conn.close()
    assert quotable is None
    assert any("index.jsonl" in note for note in json.loads(notes))


def test_schema_version_mismatch_is_refused_not_migrated(loader, tmp_path):
    db = tmp_path / "a.db"
    conn = loader.connect(db)
    conn.execute("PRAGMA user_version = 999")
    conn.close()
    with pytest.raises(SystemExit):
        loader.connect(db)


# --------------------------------------------------------------------------- #
# Shipped example queries must actually run
# --------------------------------------------------------------------------- #


def test_every_shipped_example_query_executes(loader, tmp_path):
    """An example query that does not parse is worse than none — it is a wrong claim
    about what the schema supports, and nobody finds out until they paste it."""
    root = tmp_path / "runs"
    conn = loader.connect(tmp_path / "a.db")
    try:
        loader.load_run(conn, make_run(root, "a", arms=("baseline", "curated")), index={})
        loader.load_run(conn, make_run(root, "b"), index={})
        for title, sql in loader.EXAMPLES:
            conn.execute(sql).fetchall()  # must parse and run against a populated DB
            assert title.strip()
    finally:
        conn.close()


def _counts(conn) -> tuple[int, int, int]:
    return tuple(
        conn.execute(
            "SELECT (SELECT COUNT(*) FROM runs), (SELECT COUNT(*) FROM turns), "
            "(SELECT COUNT(*) FROM events)"
        ).fetchone()
    )
