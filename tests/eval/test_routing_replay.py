"""Pin a prior shortlist so one setting is measured. An empty shortlist is a decline, not a pin."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from governed_bi.eval.configs import stub_config
from governed_bi.eval.harness import run_config
from governed_bi.eval.replay import (
    PINNED_SCHEMAS_KEY,
    attach_pinned_routing,
    drift_against,
    pin_realised,
    retrieved_tables_baseline,
    retrieved_tables_drift,
    routing_from_artifact,
)


def _artifact(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "prior.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return path


# ── reading a prior run ───────────────────────────────────────────────────────


def test_an_empty_shortlist_is_skipped_rather_than_pinned(tmp_path: Path) -> None:
    """An empty shortlist is a decline. Pinning it would freeze a retrieval failure into the next config."""
    path = _artifact(
        tmp_path,
        [
            {"question_id": "a", "schemas": ["sales"]},
            {"question_id": "b", "schemas": []},
            {"question_id": "c"},
            {"question_id": "d", "schemas": ["ops", "sales"]},
        ],
    )
    assert routing_from_artifact(path) == {"a": ["sales"], "d": ["ops", "sales"]}


def test_a_question_the_artifact_does_not_cover_is_counted_not_hidden() -> None:
    """An config labelled pinned always has some fraction that is not; the count is the honesty."""
    questions: list[dict[str, Any]] = [
        {"question_id": "a"},
        {"question_id": "b"},
        {"question_id": "c"},
    ]
    counts = attach_pinned_routing(questions, {"a": ["sales"], "c": ["ops"]})

    assert counts == {"pinned": 2, "unpinned": 1}
    assert questions[0][PINNED_SCHEMAS_KEY] == ["sales"]
    assert PINNED_SCHEMAS_KEY not in questions[1], (
        "an uncovered question must route live, not be pinned to nothing"
    )


def test_the_row_says_whether_its_own_shortlist_was_replayed(tmp_path: Path) -> None:
    """The header count does not say which rows were pinned. A constant on every row would look pinned."""
    covered = _artifact(tmp_path, [{"question_id": "replayed", "schemas": ["main"]}])
    questions: list[dict[str, Any]] = [
        {"question_id": "replayed", "question": "how many customers", "db_id": "main"},
        {"question_id": "routed_live", "question": "list customer ids", "db_id": "main"},
    ]
    counts = attach_pinned_routing(questions, routing_from_artifact(covered))
    assert counts == {"pinned": 1, "unpinned": 1}, "fixture no longer mixes the two cases"

    rows = run_config(questions, stub_config())

    assert {str(r["question_id"]): r["routing_pinned"] for r in rows} == {
        "replayed": True,
        "routed_live": False,
    }, (
        "the row does not distinguish a replayed shortlist from a live one, so the pinned "
        "fraction of a --replay-routing config is unrecoverable from its artifact"
    )


def test_a_pin_the_turn_did_not_run_on_is_not_recorded_as_a_replay(tmp_path: Path) -> None:
    """The field is whether the turn used the pin. Attachment alone counted turns that routed for themselves."""
    covered = _artifact(tmp_path, [{"question_id": "q", "schemas": ["nowhere"]}])
    questions: list[dict[str, Any]] = [
        {"question_id": "q", "question": "how many customers", "db_id": "main"}
    ]
    assert attach_pinned_routing(questions, routing_from_artifact(covered)) == {
        "pinned": 1,
        "unpinned": 0,
    }, "the fixture must attach a pin, or this asserts nothing"

    row = run_config(questions, stub_config())[0]
    assert row["schemas"] == ["main"], "fixture: the turn routed for itself"
    assert row["routing_pinned"] is False, (
        "the row claims its shortlist was replayed when the turn ran on a different one"
    )


def test_a_turn_that_ended_before_routing_is_not_recorded_as_a_replay() -> None:
    """A clarification that ends before routing is not a replay, even when a pin was attached."""
    from governed_bi.eval.harness import project_turn

    row = project_turn(
        {"answer": {"outcome": "clarification", "record": {"schemas": []}}, "messages": []},
        question={"question_id": "q", "db_id": "d", PINNED_SCHEMAS_KEY: ["a", "b"]},
        config="t",
    )
    assert row["routing_pinned"] is False

    ran = project_turn(
        {"answer": {"outcome": "answered", "record": {"schemas": ["a", "b"]}}, "messages": []},
        question={"question_id": "q", "db_id": "d", PINNED_SCHEMAS_KEY: ["a", "b"]},
        config="t",
    )
    assert ran["routing_pinned"] is True, "the check must still be able to say yes"


def test_a_partially_honoured_pin_is_not_the_pin() -> None:
    """``route_node`` drops pinned schemas the corpus does not know and keeps the rest, so the
    turn runs on a shortlist that is neither the pin nor a live route. A boolean saying "pinned"
    there would report two settings under test as one."""
    from governed_bi.eval.harness import project_turn

    row = project_turn(
        {"answer": {"outcome": "answered", "record": {"schemas": ["a"]}}, "messages": []},
        question={"question_id": "q", "db_id": "d", PINNED_SCHEMAS_KEY: ["a", "ghost"]},
        config="t",
    )
    assert row["routing_pinned"] is False


# ── the residual the flag does NOT remove ─────────────────────────────────────


def test_drift_is_measured_over_the_rows_that_moved() -> None:
    """Averaging Jaccard over the identical rows too reports ~1.0 and hides the movers."""
    baseline = {"a": ["s.t1", "s.t2"], "b": ["s.t1"], "c": ["s.t9"]}
    rows = [
        {"question_id": "a", "retrieved_tables": ["s.t1", "s.t2"]},
        {"question_id": "b", "retrieved_tables": ["s.t1", "s.t3"]},
        {"question_id": "zz", "retrieved_tables": ["s.t1"]},
    ]
    drift = retrieved_tables_drift(rows, baseline)

    assert drift["compared"] == 2 and drift["identical"] == 1 and drift["moved"] == 1
    assert drift["not_in_baseline"] == 1
    assert drift["identical_rate"] == 0.5
    # b: {t1} shared, {t1,t3} union -> 1/2. The identical row scores 1.0 and must not be
    # averaged in, or a run where one turn moved completely still reports ~1.0.
    assert drift["mean_jaccard_when_moved"] == pytest.approx(0.5)


def test_the_drift_baseline_holds_only_the_rows_the_pin_covered(tmp_path: Path) -> None:
    """Rows the pin skipped were never pinned. Counting their movement charges the pin for a change it did not claim to
    prevent."""
    path = _artifact(
        tmp_path,
        [
            {"question_id": "a", "schemas": ["s"], "retrieved_tables": ["s.t1"]},
            {"question_id": "declined", "schemas": [], "retrieved_tables": []},
            {"question_id": "b", "schemas": ["s"], "retrieved_tables": []},
        ],
    )
    baseline = retrieved_tables_baseline(path)

    assert set(baseline) == set(routing_from_artifact(path)) == {"a", "b"}
    assert "declined" not in baseline
    # An empty `retrieved_tables` on a row that *did* route is a measured zero and stays in.
    assert baseline["b"] == []


def test_an_artifact_replayed_against_itself_shows_no_drift(tmp_path: Path) -> None:
    """The zero point. Without it a drift statistic can be wrong in a way nothing reveals."""
    rows = [
        {"question_id": "a", "schemas": ["s"], "retrieved_tables": ["s.t1", "s.t2"]},
        {"question_id": "b", "schemas": ["s"], "retrieved_tables": []},
    ]
    path = _artifact(tmp_path, rows)
    baseline = {r["question_id"]: r["retrieved_tables"] for r in rows}

    assert routing_from_artifact(path) == {"a": ["s"], "b": ["s"]}
    drift = retrieved_tables_drift(rows, baseline)
    assert drift["moved"] == 0 and drift["identical_rate"] == 1.0


def test_both_sides_of_a_drift_contrast_come_out_of_one_function(tmp_path: Path) -> None:
    """Both sides of a contrast go through one function. Averaging in identical rows hides how far the movers went."""
    baseline_rows = [
        {"question_id": "same", "schemas": ["s"], "retrieved_tables": ["s.t1"]},
        {"question_id": "moved", "schemas": ["s"], "retrieved_tables": ["s.t1"]},
    ]
    path = _artifact(tmp_path, baseline_rows)
    later = [
        {"question_id": "same", "retrieved_tables": ["s.t1"]},
        {"question_id": "moved", "retrieved_tables": ["s.t1", "s.t2"]},
    ]

    drift = drift_against(path, later)
    assert (drift["identical"], drift["moved"], drift["compared"]) == (1, 1, 2)
    assert drift["mean_jaccard_when_moved"] == pytest.approx(0.5)
    # The number the mixed contrast used, spelled out so the difference is visible. Nothing in
    # `replay.py` returns it, and this asserts that: it is 0.75 here, not 0.5.
    all_rows = (drift["mean_jaccard_when_moved"] * drift["moved"] + 1.0 * drift["identical"]) / 2
    assert all_rows == pytest.approx(0.75)
    assert "mean_jaccard" not in {k for k in drift if k.endswith("_all")}


# ── how much of a config ran on the pin ────────────────────────────────


def test_the_realised_pin_count_is_readable_on_an_old_semantics_artifact() -> None:
    """On disk the flag meant the pin was attached. Realised means the turn's shortlist is that pin."""
    pinned = {"a": ["s1", "s2"], "b": ["s3"], "c": ["s4"]}
    rows = [
        {"question_id": "a", "routing_pinned": True, "schemas": ["s1", "s2"]},
        {"question_id": "b", "routing_pinned": True, "schemas": ["s3"]},
        {"question_id": "c", "routing_pinned": True, "schemas": []},
        {"question_id": "d", "routing_pinned": False, "schemas": ["s9"]},
    ]

    counts = pin_realised(rows, pinned)
    assert counts["flagged"] == 3, "what the shipped one-liner returned"
    assert counts["realised"] == 2, "the flag AND a shortlist to have run on"
    assert counts["exact"] == 2, "the independent check, which never reads the flag"
    assert counts["same_set_out_of_order"] == 0


def test_a_reordered_shortlist_is_not_an_exact_replay() -> None:
    """Order matters for an exact replay. The same schemas in another order are counted apart."""
    counts = pin_realised(
        [{"question_id": "a", "routing_pinned": True, "schemas": ["s2", "s1"]}],
        {"a": ["s1", "s2"]},
    )
    assert counts["realised"] == 1 and counts["exact"] == 0
    assert counts["same_set_out_of_order"] == 1
