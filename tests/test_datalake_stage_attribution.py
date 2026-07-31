"""Failure attribution in the pooled data-lake driver.

A three-arm run's numbers had to be thrown away because the harness could not say
*where* anything failed: a solver crash was recorded identically to a deliberate
refusal, so ``refusal_rate`` absorbed the crashes and EX absorbed the loss — by a
different amount per arm, since the arms do not crash equally. These tests pin the
crash/refusal/cap split, the per-stage event file, and the resume-time guards that
keep two runs of "the same" configuration actually measuring the same thing.

Driven with a scripted solver and an in-memory echo gateway: no model, no graph, no
Postgres, so a failure here is arithmetic or plumbing, never nondeterminism.
"""

from __future__ import annotations

import json
import shutil

import pytest

from governed_bi.corpus import load_corpus, write_corpus
from governed_bi.corpus.schemas import Column, LogicalType, TableAsset
from governed_bi.eval import run_datalake as rd
from governed_bi.eval.arms import Arm
from governed_bi.eval.dataset import EvalItem
from governed_bi.eval.hash_grade import GoldHash
from governed_bi.gateway import Identity
from governed_bi.gateway.connectors.base import QueryResult

IDENTITY = Identity(user="eval", all_access=True)


class _EchoGateway:
    def execute(self, sql: str, identity: Identity) -> QueryResult:
        return QueryResult(columns=["v"], rows=[(sql,)], row_count=1)


class _ScriptedSolver:
    """Returns a scripted ``(sql, meta)`` per question, or raises when the script
    holds an exception — the crash path the old harness scored as a refusal."""

    def __init__(self, script: dict) -> None:
        self._script = script
        self.served: list[str] = []

    def solve_with_meta(self, question: str) -> tuple[str | None, dict]:
        self.served.append(question)
        scripted = self._script[question]
        if isinstance(scripted, Exception):
            raise scripted
        return scripted

    def solve(self, question: str) -> str | None:
        return self.solve_with_meta(question)[0]


def _items(script: dict, *, difficulty: str | None = None) -> list[EvalItem]:
    return [
        EvalItem(question=q, sql="SELECT 1", question_id=f"id_{q}", difficulty=difficulty)
        for q in script
    ]


def _gold(items: list[EvalItem]) -> dict[str, GoldHash]:
    """Gold that never matches: these tests are about attribution, not EX."""
    return {
        str(it.question_id): GoldHash(
            str(it.question_id), hash_lenient="nope", hash_strict="nope", nrows=1
        )
        for it in items
    }


def _run(tmp_path, script, *, items=None, solver=None, **over):
    items = items if items is not None else _items(script)
    kwargs = dict(
        arm="curated",
        solver=solver if solver is not None else _ScriptedSolver(script),
        pairs=[(it, "db_a") for it in items],
        gold_hashes=_gold(items),
        gateway=_EchoGateway(),
        identity=IDENTITY,
        bird_dir=None,
        suspect_by_db={},
        arm_corpus=None,
        dialect="postgres",
        twin_ids=frozenset(),
        ungradeable_ids=frozenset(),
        out_path=tmp_path / "generations.curated.jsonl",
    )
    kwargs.update(over)
    return rd._run_pool_arm(**kwargs)


# --------------------------------------------------------------------------- #
# Outcome + failing stage (the headline fix)
# --------------------------------------------------------------------------- #


def test_a_crash_is_not_counted_as_a_refusal(tmp_path):
    """The bug that invalidated a run: both arrived as "no SQL" and were one number.

    ``refusal_rate`` here must be 0.25 (one deliberate refuse-gate hit), not the
    0.75 the old ``n - n_produced`` arithmetic reported.
    """
    script = {
        "answered": ("SELECT 1", {}),
        "refused": (None, {"refused_by": "refuse_gate"}),
        "raised": RuntimeError("boom"),
        "degraded": (None, {"refused_by": "model_error"}),
    }
    rows, summary = _run(tmp_path, script)

    assert [r["outcome"] for r in rows] == [
        "answered",
        "refused",
        "crashed",
        "crashed",
    ]
    assert (summary["n_answered"], summary["n_refused"], summary["n_crashed"]) == (1, 1, 2)
    assert summary["refusal_rate"] == pytest.approx(0.25)
    assert summary["crash_rate"] == pytest.approx(0.5)
    # A raised exception has no attributable stage; ``model_error`` is stamped inside
    # agent_core. Guessing a stage for the first would put weight in a bucket
    # nothing observed.
    assert summary["by_failed_stage"] == {"refuse_gate": 1, "agent_core": 1}
    assert sum(summary["by_outcome"].values()) == summary["n"]


def test_the_crash_message_survives_into_the_row(tmp_path):
    rows, _summary = _run(tmp_path, {"raised": RuntimeError("boom")})
    # Class name AND message: this string is the only record of the crash that
    # reaches the row, and a bare `str(err)` on the common cases (KeyError,
    # IndexError) yields a quoted key with no indication of what went wrong.
    assert rows[0]["error"] == "RuntimeError: boom"
    assert rows[0]["failed_stage"] is None


def test_an_execution_failure_is_an_answer_not_a_crash(tmp_path):
    """SQL that the database rejects is a wrong answer. Only the *solver* raising is
    a crash — ``error`` carries both by the time the row is written, so classifying
    from it instead of from the exception would report every bad query as our bug."""

    class _AngryGateway:
        def execute(self, sql, identity):
            raise RuntimeError("relation does not exist")

    rows, summary = _run(
        tmp_path, {"answered": ("SELECT nope", {})}, gateway=_AngryGateway()
    )
    assert rows[0]["outcome"] == "answered"
    assert rows[0]["correct"] is False
    assert summary["crash_rate"] == 0.0


def test_guardrail_and_cap_land_on_their_own_stages(tmp_path):
    script = {
        "blocked": (None, {"refused_by": "guardrail"}),
        "capped": (None, {"refused_by": "exhausted"}),
    }
    rows, summary = _run(tmp_path, script)
    assert [r["failed_stage"] for r in rows] == ["guardrail", "agent_core"]
    # A cap is neither a refusal nor a crash: the run stopped us, we did not decline.
    assert summary["by_outcome"] == {"refused": 1, "capped": 1}
    assert summary["refusal_rate"] == pytest.approx(0.5)


def test_an_unknown_refused_by_is_counted_not_invented(tmp_path, capsys):
    """``refused_by`` is free text. A typo must show up as an unmapped count rather
    than mint a stage bucket no report will ever mention."""
    rows, summary = _run(tmp_path, {"odd": (None, {"refused_by": "brand_new_reason"})})
    assert summary["n_unmapped_refused_by"] == 1
    assert summary["by_failed_stage"] == {}
    assert rows[0]["outcome"] == "refused"
    assert "unrecognised refused_by" in capsys.readouterr().out


def test_summary_classifies_rows_read_back_from_disk(tmp_path):
    """A resumed run summarises replayed rows through the same classifier, so an
    interrupted run's crash count is not silently zero."""
    script = {"raised": RuntimeError("boom"), "refused": (None, {"refused_by": "guardrail"})}
    items = _items(script)
    _run(tmp_path, script, items=items)

    replayed = rd._summarise_rows("curated", rd._read_rows(tmp_path / "generations.curated.jsonl"))
    assert (replayed["n_crashed"], replayed["n_refused"]) == (1, 1)


# --------------------------------------------------------------------------- #
# stage_events.jsonl
# --------------------------------------------------------------------------- #


_EVENTS = [
    {"stage": "route", "status": "ok", "ms": 1.5, "detail": {"intent": "sql"}},
    {"stage": "execute", "status": "error", "ms": 2.0, "detail": {}},
]


def test_stage_events_are_written_one_record_per_stage(tmp_path):
    path = tmp_path / "stage_events.jsonl"
    sink = rd._RowSink(path)
    try:
        _run(
            tmp_path,
            {"answered": ("SELECT 1", {"stage_events": _EVENTS})},
            stage_sink=sink,
        )
        # Read before close: an interrupted run must keep what it already served.
        written = rd._read_rows(path)
    finally:
        sink.close()

    assert [r["stage"] for r in written] == ["route", "execute"]
    assert written[0] == {
        "question_id": "id_answered",
        "arm": "curated",
        "db_id": "db_a",
        "run_id": None,
        "turn_id": None,
        "stage": "route",
        "status": "ok",
        "ms": 1.5,
        "detail": {"intent": "sql"},
    }


def test_a_replayed_row_contributes_no_stage_events(tmp_path):
    """A replayed row has no fresh timings. Re-emitting its old ones — or spreading
    the row's total latency over stages — would put a fabricated number in the one
    file whose whole purpose is attributing time."""
    script = {"answered": ("SELECT 1", {"stage_events": _EVENTS})}
    items = _items(script)

    first_path = tmp_path / "first.jsonl"
    first = rd._RowSink(first_path)
    _run(tmp_path, script, items=items, stage_sink=first)
    first.close()
    assert len(rd._read_rows(first_path)) == 2  # the events did land the first time

    solver = _ScriptedSolver(script)
    second_path = tmp_path / "second.jsonl"
    second = rd._RowSink(second_path)
    rows, _summary = _run(tmp_path, script, items=items, solver=solver, resume=True, stage_sink=second)
    second.close()

    assert solver.served == []  # nothing re-served
    assert len(rows) == 1
    assert rd._read_rows(second_path) == []


def test_absent_stage_events_are_tolerated(tmp_path):
    """The serve-side producer may be older than this reader; a turn that reported
    no stages simply has none."""
    assert rd._stage_event_rows({}, question_id="q", arm="curated", db_id="db_a") == []


@pytest.mark.parametrize(
    "payload, expected_n",
    [({"stage_events": "route"}, 0), ({"stage_events": ["route", {"stage": "route"}]}, 1)],
)
def test_malformed_stage_events_are_dropped_loudly(payload, expected_n, capsys):
    out = rd._stage_event_rows(payload, question_id="q", arm="curated", db_id="db_a")
    assert len(out) == expected_n
    assert "WARNING" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Fields that were computed and dropped
# --------------------------------------------------------------------------- #


def test_ledger_and_tool_counts_reach_the_row_and_the_summary(tmp_path):
    meta = {
        "ledger_len": 3,
        "governance_ledger": [
            {
                "action": "run_query",
                "verdict": "block",
                "layer": "term_semantics",
                "sql": "SELECT 1",
                "allowed": [],
            },
            {
                "action": "run_query",
                "verdict": "pass",
                "layer": None,
                "sql": "SELECT 1",
                "allowed": ["beer_factory.transaction"],
                "row_count": 1,
            },
            {
                "action": "sample_rows",
                "verdict": "pass",
                "layer": None,
                "sql": None,
                "allowed": ["beer_factory.transaction"],
                "row_count": 5,
            },
        ],
        "n_tool_calls": {"search_corpus": 2, "run_query": 1},
        "by_guardrail_layer": {"ast_column_allowlist": 1},
    }
    rows, summary = _run(tmp_path, {"answered": ("SELECT 1", meta)})
    assert rows[0]["ledger_len"] == 3
    assert rows[0]["governance_ledger"] == meta["governance_ledger"]
    assert all("result" not in e for e in rows[0]["governance_ledger"])
    assert rows[0]["n_tool_calls"] == {"search_corpus": 2, "run_query": 1}
    assert summary["tool_calls"] == {"run_query": 1, "search_corpus": 2}
    assert summary["by_guardrail_layer"] == {"ast_column_allowlist": 1}
    assert summary["mean_ledger_len"] == pytest.approx(3.0)


def test_unreported_counters_are_none_not_empty(tmp_path):
    """``{}`` would assert the arm made zero tool calls; nothing observed that."""
    _rows, summary = _run(tmp_path, {"answered": ("SELECT 1", {})})
    assert summary["tool_calls"] is None
    assert summary["by_guardrail_layer"] is None
    assert summary["mean_ledger_len"] is None


def test_a_measured_zero_cost_is_not_reported_as_unmeasured():
    """The ``_total(...) or None`` idiom collapsed a real 0.0 into "no data"."""
    free = rd._summarise_rows(
        "curated", [{"latency_sec": 0.0, "cost_est_usd": 0.0, "usage": {"total_tokens": 0}}]
    )["cost"]
    assert free["total_cost_est_usd"] == 0.0
    assert free["total_latency_sec"] == 0.0
    assert free["total_tokens"] == 0

    unmeasured = rd._summarise_rows("curated", [{"correct": True}])["cost"]
    assert unmeasured["total_cost_est_usd"] is None
    assert unmeasured["mean_latency_sec"] is None


def test_an_all_unknown_difficulty_bucket_reads_as_empty(tmp_path):
    """``by_difficulty`` is ~85% "unknown" on this dataset; without a count of the
    rows that carried one, that reads as a uniform spread across difficulties."""
    _rows, degenerate = _run(tmp_path, {"answered": ("SELECT 1", {})})
    assert degenerate["by_difficulty"] == {"unknown": 0.0}
    assert degenerate["n_with_difficulty"] == 0

    script = {"answered": ("SELECT 1", {})}
    _rows, graded = _run(
        tmp_path, script, items=_items(script, difficulty="simple"),
        out_path=tmp_path / "generations.two.jsonl",
    )
    assert graded["n_with_difficulty"] == 1


def test_arms_come_from_the_enum():
    """Two spellings of the same three-value taxonomy drift apart."""
    assert rd._ARMS == tuple(a.value for a in Arm)


# --------------------------------------------------------------------------- #
# Resume guards: what is served must be what is scored
# --------------------------------------------------------------------------- #


def _table(schema: str) -> TableAsset:
    return TableAsset(
        id=f"tbl_{schema}_orders",
        schema=schema,
        physical_name="orders",
        columns=[
            Column(
                physical_name="order_id",
                physical_type="INTEGER",
                logical_type=LogicalType.integer,
                nullable=True,
                is_unique=False,
            )
        ],
    )


def test_only_the_dbs_being_scored_enter_the_served_corpus(tmp_path):
    """A shared corpus root is cumulative. A db dropped from ``built`` this attempt
    leaves its YAML behind, and a directory-wide load readmits it as a router
    candidate for every other db's question — changing the routing problem's
    difficulty between two runs of the same db set."""
    root = tmp_path / "corpus_baseline"
    write_corpus(root, "db_a", [_table("db_a")])
    write_corpus(root, "dropped_db", [_table("dropped_db")])

    assert {t.schema for t in load_corpus(root).tables()} == {"db_a", "dropped_db"}
    scoped = rd._load_built_corpus(root, ["db_a"])
    assert {t.schema for t in scoped.tables()} == {"db_a"}


def test_a_fully_built_db_is_not_rebuilt_or_re_probed(tmp_path, monkeypatch):
    """The other half of the same bug: re-probing Postgres for a db that is already
    built means a transient blip drops it from ``built`` while its corpus stays on
    disk."""
    arms = ("baseline", "curated", "curated_sme")
    roots = {arm: tmp_path / f"corpus_{arm}" for arm in arms}
    for arm in arms:
        target = roots[arm] / "db_a" / "tables"
        target.mkdir(parents=True)
        (target / "tbl_db_a_orders.yaml").write_text("id: tbl_db_a_orders\n", encoding="utf-8")
        rd._mark_build_complete(roots[arm], "db_a")

    def _boom(*_a, **_k):
        raise RuntimeError("postgres touched")

    monkeypatch.setattr(rd, "PostgresConnector", _boom)
    build = dict(
        db_id="db_a",
        pg_dsn="host=nowhere",
        bird_dir=tmp_path,
        roots=roots,
        arms=arms,
        chat_client=None,
        lc_model=None,
        max_agent_steps=1,
        resume=True,
    )
    rd._build_db_corpora(**build)  # no connection, no build, no drop

    # ...but a db that is only partly built must still be finished, so the skip
    # cannot silently score a half-built corpus.
    shutil.rmtree(roots["curated"] / "db_a")
    with pytest.raises(RuntimeError, match="postgres touched"):
        rd._build_db_corpora(**build)


def test_a_corrupt_manifest_is_not_treated_as_a_missing_one(tmp_path):
    """A manifest torn by a kill mid-write silently disabled the split and
    knob-drift guards, which is how two configurations end up averaged as one."""
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        rd._read_manifest(tmp_path)
    with pytest.raises(RuntimeError, match="unreadable"):
        rd._check_resume_manifest(tmp_path, {"split": "test"})


def test_a_manifest_that_is_json_but_not_an_object_is_loud(tmp_path):
    (tmp_path / "manifest.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not an object"):
        rd._read_manifest(tmp_path)


def test_a_manifest_with_no_split_is_not_a_wildcard(tmp_path):
    """A pre-``split``-field run directory is test-only by construction; letting its
    silence match ``--split train`` mixes two disjoint question pools in one file."""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"route_top_k": 10}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="refusing to resume"):
        rd._check_resume_manifest(tmp_path, {"split": "train"})


def test_resuming_after_a_code_change_is_fatal(tmp_path):
    """Two harness versions in one arm's rows is drift no field in the row records.

    Always fatal (M3 N10): the smoke warn / ``--allow-git-sha-drift`` dual track is gone.
    """
    (tmp_path / "manifest.json").write_text(
        json.dumps({"split": "test", "git_sha": "aaaa"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="git_sha"):
        rd._check_resume_manifest(
            tmp_path, {"split": "test", "git_sha": "bbbb"}
        )


def test_rows_with_no_recorded_split_block_a_resume(tmp_path):
    path = tmp_path / "generations.curated.jsonl"
    path.write_text(
        json.dumps({"question_id": "legacy_1", "correct": True}) + "\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="split"):
        _run(tmp_path, {"answered": ("SELECT 1", {})}, resume=True, out_path=path)


# --------------------------------------------------------------------------- #
# Leakage: the curator's input must not be the score (C4)
# --------------------------------------------------------------------------- #


def _dataset(tmp_path, train_ids, test_ids, db="db_a"):
    dataset_dir = tmp_path / "eval_dataset"
    dataset_dir.mkdir(exist_ok=True)
    for split, ids in (("train", train_ids), ("test", test_ids)):
        (dataset_dir / f"{split}_final.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "db_id": db,
                        "question_id": qid,
                        "question": f"q{qid}",
                        "sql_rename": "SELECT 1",
                    }
                )
                + "\n"
                for qid in ids
            ),
            encoding="utf-8",
        )
    return dataset_dir


def test_train_test_overlap_fails_before_any_serving(tmp_path):
    dataset_dir = _dataset(tmp_path, ["a", "b"], ["b", "c"])
    with pytest.raises(AssertionError, match="overlap"):
        rd._assert_train_test_disjoint(dataset_dir, ["db_a"])


def test_disjoint_splits_report_their_sizes(tmp_path):
    dataset_dir = _dataset(tmp_path, ["a", "b"], ["c"])
    assert rd._assert_train_test_disjoint(dataset_dir, ["db_a"]) == {
        "train_test_disjoint": True,
        "n_train_ids": 2,
        "n_test_ids": 1,
        # Byte-identical question text across splits — id-disjointness does not
        # cover it, and nothing checked it before (AUDIT E5).
        "n_train_test_text_overlap": 0,
        "train_test_text_overlap_examples": {},
    }


def test_byte_identical_question_text_across_splits_is_reported(tmp_path):
    """Ids differ, words are the same — the form the id check cannot see."""
    dataset_dir = _dataset(tmp_path, ["a"], ["b"])
    # Give the test question the train question's exact text (ids stay distinct).
    path = dataset_dir / "test_final.jsonl"
    path.write_text(
        path.read_text(encoding="utf-8").replace('"question": "qb"', '"question": "qa"'),
        encoding="utf-8",
    )

    out = rd._assert_train_test_disjoint(dataset_dir, ["db_a"])
    assert out["train_test_disjoint"] is True  # ids are still disjoint
    assert out["n_train_test_text_overlap"] == 1
    assert out["train_test_text_overlap_examples"]["db_a"] == ["qa"]


def test_the_oracle_rung_stamp_reaches_the_row(tmp_path):
    """Found by the first live oracle run, where every row came back unstamped.

    An oracle rung reads the answer key, so its EX is a headroom bound and never
    system performance. The only thing keeping the two apart in an archived
    artifact is this per-row stamp, and the solver was setting it in ``meta``
    while the row builder dropped it on the floor.
    """
    script = {"q1": ("SELECT 1", {"oracle_rung": "oracle_tables", "oracle_applied": True})}
    rows, _summary = _run(tmp_path, script, arm="oracle_tables")
    assert rows[0]["oracle_rung"] == "oracle_tables"
    assert rows[0]["oracle_applied"] is True


def test_a_fair_arm_carries_no_oracle_stamp(tmp_path):
    """``None``, so a fair row can never be filtered in as a diagnostic."""
    rows, _summary = _run(tmp_path, {"q1": ("SELECT 1", {})})
    assert rows[0]["oracle_rung"] is None


def test_a_single_schema_pool_does_not_charge_every_wrong_answer_to_routing(tmp_path):
    """The router does not engage when there is nothing to route between.

    It stamps no provenance, so the row reads `routed_hit=False` on a question
    routing was never asked. Read literally, a one-schema pool reports that the
    picker caused every failure in the run.
    """
    script = {"q1": ("SELECT 1", {"total_schemas": 1})}
    rows, summary = _run(tmp_path, script)
    assert rows[0]["routing_bypassed"] is True
    assert "schema_pick" not in (summary["errors"] or {}).get("by_error_stage", {})


def test_a_real_pool_still_attributes_routing_misses(tmp_path):
    """The bypass guard must not swallow a genuine miss."""
    script = {"q1": ("SELECT 1", {"total_schemas": 69, "routed_schemas": ["other"]})}
    rows, summary = _run(tmp_path, script)
    assert rows[0]["routing_bypassed"] is False
    assert (summary["errors"] or {})["by_error_stage"] == {"schema_pick": 1}


# --------------------------------------------------------------------------- #
# The row builder's absent-vs-zero contract
# --------------------------------------------------------------------------- #


def test_a_meta_with_no_routing_provenance_writes_none_not_a_miss(tmp_path):
    """Pins the fix at its SOURCE, which the aggregation tests could not reach.

    ``_summarise_rows`` and ``rank_report`` are tested on hand-built rows, so both
    stayed green with the row builder reverted to::

        routed = meta.get("routed_schemas") or []
        shortlisted = meta.get("shortlisted_schemas") or []

    and that revert is what regenerates the artifact this was all written to kill:
    ``routing_recall 0.0`` over every row with ``n_routing_bypassed 0``, and
    ``by_gold_rank {"miss": {"n": ..., "ex_lenient": 1.0}}`` — a bucket meaning "fix
    the embedder" at a perfect score. Only a test that drives ``_run_pool_arm`` with a
    provenance-free meta closes it.

    ``[]`` and ``None`` are different facts and must produce different rows: an empty
    shortlist that was RECORDED is a real retrieval miss.
    """
    script = {
        "silent": ("SELECT 1", {}),  # no routing keys at all
        "recorded_miss": ("SELECT 1", {"routed_schemas": [], "shortlisted_schemas": []}),
        "recorded_hit": (
            "SELECT 1",
            {"routed_schemas": ["db_a"], "shortlisted_schemas": ["db_a"]},
        ),
    }
    rows, summary = _run(tmp_path, script)
    by_q = {r["question_id"]: r for r in rows}

    silent = by_q["id_silent"]
    assert silent["routed_schemas"] is None
    assert silent["routed_hit"] is None, "absent routing became a miss"
    assert silent["shortlisted_schemas"] is None
    assert silent["gold_schema_rank"] is None

    miss = by_q["id_recorded_miss"]
    assert miss["routed_schemas"] == []
    assert miss["routed_hit"] is False, "a recorded empty route is a real miss"
    assert miss["shortlisted_schemas"] == []

    hit = by_q["id_recorded_hit"]
    assert hit["routed_hit"] is True
    assert hit["gold_schema_rank"] == 1

    # And the aggregate reads off those rows: one hit, one miss, one not measured.
    assert summary["n_routing_observed"] == 2
    assert summary["n_routing_unrecorded"] == 1
    assert summary["routing_recall"] == pytest.approx(0.5)


def test_the_no_shortlist_bucket_is_reachable_from_a_real_run(tmp_path):
    """The other half of the same revert: `rank_report` over rows the driver wrote."""
    from governed_bi.eval.analysis import rank_report

    rows, _summary = _run(
        tmp_path,
        {
            "silent": ("SELECT 1", {}),
            "recorded_miss": ("SELECT 1", {"shortlisted_schemas": ["other"]}),
        },
    )
    report = rank_report(rows)
    assert report["no_shortlist"]["n"] == 1
    assert report["miss"]["n"] == 1
