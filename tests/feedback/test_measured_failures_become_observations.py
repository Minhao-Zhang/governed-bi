"""The importer: an eval artifact in, observations out, and the populations it refuses.

**Two kinds of test here, and the split is forced rather than chosen.** ``runs/`` is gitignored and
carries **zero tracked files**, so the real artifacts are local-only and a test that reads one
cannot run in CI. The logic is therefore driven by a synthetic artifact built in ``tmp_path``, and
the one test that reads the real v4 arm **skips when it is absent** and asserts the published
figure when it is present.

That is the same situation ``docs/failure-modes.md`` is in, and the reason its §1 table carries a
"hand-run, no producer in the tree" warning. This module is the producer: the partition it computes
reproduces that table's cross-cutting coverage total exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governed_bi.eval.feedback_import import (
    DATASET_DEFECT_FLAGS,
    external_key,
    import_failures,
    partition_failures,
)
from governed_bi.feedback.cluster import cluster_key, clusters
from governed_bi.feedback.events import Category, Kind, ObservationState, Source
from governed_bi.feedback.store import FeedbackStore
from governed_bi.paths import REPO_ROOT

#: The published arm and its split. Skipped when absent, which is every checkout but this one.
_REAL_ARTIFACT = REPO_ROOT / "runs/eval/proxy_v4_corpus30872d3.jsonl"
_REAL_DATASET = REPO_ROOT / "../BIRD-Data-Obfuscation/eval_dataset/test_final.jsonl"


def _row(**over: object) -> dict[str, object]:
    """One artifact row, in the shape ``eval/projection.py::project_turn`` writes.

    Deliberately does **not** carry ``question``, ``turn_id`` or ``thread_id``: measured, those are
    absent from all 1,351 rows of the real arm, and a fixture that supplies them would test an
    artifact nobody produces.
    """
    base: dict[str, object] = {
        "arm": "v_test",
        "question_id": "q1",
        "db_id": "beer_factory",
        "outcome": "answered",
        "correct": False,
        "crashed": False,
        "refused_by": None,
        "generated_sql": "SELECT count(*) FROM beer_factory.brauerei",
        "gold_sql": "SELECT count(*) FROM beer_factory.wurzelbier",
        "gold_fingerprint": "gf",
        "pred_fingerprint": "pf",
        "licensed": ["beer_factory.brauerei"],
        "schemas": ["beer_factory"],
        "quality_flags": [],
        "corpus_content_hash": "corpus-a",
        "prompt_set_hash": "prompt-a",
        "knobs_resolved": {"git_sha": "abc123"},
    }
    base.update(over)
    return base


def _artifact(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "arm.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _dataset(tmp_path: Path, ids: list[str]) -> Path:
    path = tmp_path / "test_final.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "question_id": qid,
                    "db_id": "beer_factory",
                    "question": f"question text for {qid}",
                    "sql_rename": "SELECT 1",
                    "difficulty": "simple",
                }
            )
            for qid in ids
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _store(tmp_path: Path) -> FeedbackStore:
    return FeedbackStore(tmp_path / "feedback.sqlite")


# ── the partition ─────────────────────────────────────────────────────────────


def test_a_correct_turn_is_not_a_failure() -> None:
    assert sum(len(v) for v in partition_failures([_row(correct=True)]).values()) == 0


def test_the_five_buckets_partition_the_failures() -> None:
    """Every failure lands in exactly one bucket, and the order of the tests is what makes that
    true: a frozen-literal gold whose tables were also unlicensed is a dataset defect *first*,
    because patching a corpus cannot win a question no query can answer."""
    rows = [
        _row(question_id="miss"),  # gold table not licensed
        _row(question_id="full", licensed=["beer_factory.wurzelbier"]),
        _row(question_id="frozen", quality_flags=["degenerate"]),
        _row(question_id="both", quality_flags=["degenerate"], licensed=[]),
        _row(question_id="boom", crashed=True),
        _row(question_id="unparsed", gold_sql="this is not sql ((("),
    ]
    buckets = partition_failures(rows)
    assert [r["question_id"] for r in buckets["coverage_miss"]] == ["miss"]
    assert [r["question_id"] for r in buckets["full_coverage"]] == ["full"]
    assert {r["question_id"] for r in buckets["dataset_defect"]} == {"frozen", "both"}
    assert [r["question_id"] for r in buckets["crashed"]] == ["boom"]
    assert [r["question_id"] for r in buckets["gold_unparsed"]] == ["unparsed"]
    assert sum(len(v) for v in buckets.values()) == len(rows)


def test_a_case_difference_is_not_a_coverage_miss() -> None:
    """``licensed`` carries the corpus's spelling and a gold statement the dataset's."""
    row = _row(gold_sql="SELECT 1 FROM Beer_Factory.Brauerei", licensed=["beer_factory.brauerei"])
    assert partition_failures([row])["full_coverage"] == [row]


# ── what is imported, and what is refused ─────────────────────────────────────


def test_only_the_coverage_misses_are_imported(tmp_path: Path) -> None:
    """The decision the whole cut rests on: the imported population is the one T3 can verify per
    question at no cost, so the input set and the free ladder are the same set."""
    artifact = _artifact(
        tmp_path,
        [
            _row(question_id="miss"),
            _row(question_id="full", licensed=["beer_factory.wurzelbier"]),
            _row(question_id="frozen", quality_flags=["degenerate"]),
        ],
    )
    report = import_failures(
        artifact,
        dataset=_dataset(tmp_path, ["miss", "full", "frozen"]),
        store=_store(tmp_path),
        dry_run=False,
    )
    assert report.inserted == 1
    assert report.skipped_full_coverage == 1
    assert report.skipped_dataset_defect == 1


def test_a_dataset_defect_is_counted_and_not_filed(tmp_path: Path) -> None:
    """Unwinnable by design, and the engine matches a third of them by luck — so they oscillate
    between correct and failed across arms and would churn the store. Counted, not hidden."""
    assert DATASET_DEFECT_FLAGS == {"degenerate", "exec_failed"}
    store = _store(tmp_path)
    report = import_failures(
        _artifact(tmp_path, [_row(question_id="frozen", quality_flags=["degenerate"])]),
        dataset=_dataset(tmp_path, ["frozen"]),
        store=store,
        dry_run=False,
    )
    assert report.inserted == 0
    assert report.skipped_dataset_defect == 1
    assert store.queue().total == 0
    assert "dataset defect" in report.render()


def test_a_dataset_defect_can_be_imported_deliberately(tmp_path: Path) -> None:
    report = import_failures(
        _artifact(tmp_path, [_row(question_id="frozen", quality_flags=["degenerate"])]),
        dataset=_dataset(tmp_path, ["frozen"]),
        store=_store(tmp_path),
        dry_run=False,
        include_flags=frozenset({"degenerate"}),
    )
    assert report.inserted == 1


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    report = import_failures(
        _artifact(tmp_path, [_row()]),
        dataset=_dataset(tmp_path, ["q1"]),
        store=store,
        dry_run=True,
    )
    assert report.inserted == 1
    assert store.queue().total == 0


def test_a_missing_question_join_raises_rather_than_filing_a_blank(tmp_path: Path) -> None:
    """A row that does not carry the question cannot be reviewed, so a broken artifact/dataset
    pairing must be loud rather than 73 unreviewable rows."""
    with pytest.raises(KeyError, match="no question text"):
        import_failures(
            _artifact(tmp_path, [_row(question_id="absent")]),
            dataset=_dataset(tmp_path, ["something-else"]),
            store=_store(tmp_path),
            dry_run=False,
        )


# ── idempotency ───────────────────────────────────────────────────────────────


def test_re_reading_one_artifact_files_nothing_new(tmp_path: Path) -> None:
    store = _store(tmp_path)
    args = dict(dataset=_dataset(tmp_path, ["q1"]), store=store, dry_run=False)
    artifact = _artifact(tmp_path, [_row()])

    first = import_failures(artifact, **args)  # type: ignore[arg-type]
    second = import_failures(artifact, **args)  # type: ignore[arg-type]

    assert (first.inserted, first.already_present) == (1, 0)
    assert (second.inserted, second.already_present) == (0, 1)
    assert store.queue().total == 1


def test_the_same_question_under_a_new_treatment_is_a_second_observation(tmp_path: Path) -> None:
    """A new arm is new information about a different treatment, with different evidence. The key
    digests both hashes for exactly this reason."""
    store = _store(tmp_path)
    dataset = _dataset(tmp_path, ["q1"])
    import_failures(
        _artifact(tmp_path, [_row()]), dataset=dataset, store=store, dry_run=False
    )
    import_failures(
        _artifact(tmp_path, [_row(arm="v_next", corpus_content_hash="corpus-b")]),
        dataset=dataset,
        store=store,
        dry_run=False,
    )
    assert store.queue().total == 2


def test_the_key_is_not_the_run_id(tmp_path: Path) -> None:
    """``run_id`` is constant per arm, so keying on it would collapse every question into one."""
    a, b = _row(question_id="q1"), _row(question_id="q2")
    assert external_key(a) != external_key(b)
    assert external_key(a) == external_key(dict(a, run_id="a-different-run"))


# ── the derivation that replaces a guess ──────────────────────────────────────


@pytest.mark.parametrize(
    ("over", "kind", "category"),
    [
        ({}, Kind.wrong_answer, Category.wrong_scope),
        ({"outcome": "refused", "refused_by": "guardrail"}, Kind.from_refusal, Category.false_refusal),
        ({"outcome": "capped", "refused_by": "attempt_cap"}, Kind.from_refusal, Category.attempt_capped),
        ({"outcome": "clarification"}, Kind.from_refusal, Category.bad_clarification),
    ],
)
def test_the_category_is_derived_from_the_outcome(
    tmp_path: Path, over: dict[str, object], kind: Kind, category: Category
) -> None:
    """The design's weakest assumption — a reader guessing which of nine sentences fits — replaced
    by a mapping small enough to read."""
    store = _store(tmp_path)
    import_failures(
        _artifact(tmp_path, [_row(**over)]),
        dataset=_dataset(tmp_path, ["q1"]),
        store=store,
        dry_run=False,
    )
    obs = store.queue().rows[0]
    assert (obs.kind, obs.category) == (kind, category)
    assert obs.source is Source.eval
    assert obs.state is ObservationState.open


def test_an_imported_row_carries_the_evidence_and_no_turn_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    import_failures(
        _artifact(tmp_path, [_row()]),
        dataset=_dataset(tmp_path, ["q1"]),
        store=store,
        dry_run=False,
    )
    obs = store.queue().rows[0]
    assert obs.turn_id is None and obs.thread_id is None
    assert obs.question == "question text for q1"
    assert obs.gold_sql == "SELECT count(*) FROM beer_factory.wurzelbier"
    assert obs.missing_tables == ("beer_factory.wurzelbier",)
    assert obs.git_sha == "abc123"
    assert obs.corpus_content_hash == "corpus-a"


# ── clustering, and the negative result ───────────────────────────────────────


def test_the_cluster_key_is_category_and_schema_and_not_the_missing_tables() -> None:
    """Measured on the real arm: keying on the absence too leaves 92% of the queue as singletons,
    because 56 of 73 rows miss exactly one table and those tables are mostly different ones."""
    from governed_bi.feedback.events import Observation

    def obs(qid: str, missing: tuple[str, ...]) -> Observation:
        return Observation(
            observation_id=f"obs-{qid}",
            filed_at="2026-08-23T00:00:00+00:00",
            source=Source.eval,
            kind=Kind.wrong_answer,
            state=ObservationState.open,
            category=Category.wrong_scope,
            question=qid,
            db_id="airline",
            missing_tables=missing,
            external_key=qid,
            arm="v4",
            question_id=qid,
        )

    a, b = obs("q1", ("airline.carriers",)), obs("q2", ("airline.flights",))
    assert cluster_key(a) == cluster_key(b) == "wrong_scope|airline"

    grouped = clusters([a, b])
    assert len(grouped) == 1 and grouped[0].n == 2
    # Different missing tables, so the shared set is empty -- a real answer, and the reason the
    # intersection is reported rather than the union.
    assert grouped[0].missing_tables == ()
    assert grouped[0].n_distinct_questions == 2


def test_clusters_are_ordered_oldest_first_and_not_by_size() -> None:
    """A five-row cluster from this morning is not more urgent than one that waited a month, and
    sorting by size makes the long tail permanently invisible."""
    from governed_bi.feedback.events import Observation

    def obs(qid: str, when: str, schema: str) -> Observation:
        return Observation(
            observation_id=f"obs-{qid}",
            filed_at=when,
            source=Source.eval,
            kind=Kind.wrong_answer,
            state=ObservationState.open,
            category=Category.wrong_scope,
            question=qid,
            db_id=schema,
            external_key=qid,
            arm="v4",
            question_id=qid,
        )

    grouped = clusters(
        [
            obs("new1", "2026-08-23T00:00:00+00:00", "big"),
            obs("new2", "2026-08-23T00:00:01+00:00", "big"),
            obs("old", "2026-07-01T00:00:00+00:00", "small"),
        ]
    )
    assert [c.key for c in grouped] == ["wrong_scope|small", "wrong_scope|big"]


# ── the real arm, when it is here ─────────────────────────────────────────────


@pytest.mark.skipif(
    not _REAL_ARTIFACT.exists() or not _REAL_DATASET.exists(),
    reason="runs/ is gitignored and the dataset is a sibling checkout; both are local-only",
)
def test_the_partition_reproduces_the_published_coverage_total() -> None:
    """``docs/failure-modes.md`` §1: "71 failures had incomplete table coverage".

    That figure and the whole §1 table are hand-run in the published documents — the page says so.
    This is the producer, and it agrees to the question.

    **It said 73 until 2026-08-24, and the two rows are a metric fix, not a run.** The artifact has
    not moved. ``_missing_tables`` compared the gold's identifier against ``licensed``, which holds
    asset **ids**; ``airline."Air Carriers"`` is ``airline.Air_Carriers_66c534`` and so read as
    never licensed on the five rows whose gold names it. Two of those five are failures, and they
    were filed as ``coverage_miss`` — "the turn was not allowed to read this table" — about a table
    the turn was licensed for. They are ``full_coverage`` now, which is the bucket that needs T4/T5
    rather than corpus curation. The total (438) is unchanged, because no row left the population.
    """
    rows = [json.loads(line) for line in _REAL_ARTIFACT.read_text(encoding="utf-8").splitlines() if line.strip()]
    buckets = partition_failures(rows)

    assert len(rows) == 1351
    assert sum(len(v) for v in buckets.values()) == 438, "the published failure count"
    assert len(buckets["coverage_miss"]) == 71, "the published cross-cutting coverage total"
    assert len(buckets["crashed"]) == 0
    assert len(buckets["gold_unparsed"]) == 0
