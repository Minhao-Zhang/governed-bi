"""``--replay-routing``: pinning one run's shortlist onto the next.

The knob exists because ``route`` is deterministic and the five facet rewriters above it are
not. Each is a utility-model call, so two runs of one question over one corpus can hand
``route`` different hits, the shortlist moves, ``licensed`` moves, and a prompt A/B cannot say
whether its delta came from the prompt. Every test here is on a property that, if it broke,
would leave the flag *accepted and ineffective* — the failure shape this repository keeps
paying for, because an arm labelled pinned that was not is worse than no flag at all.

This half is the artifact contract: reading a prior run and measuring the residual. That the
pin actually reaches ``route_node`` is asserted in
``tests/serve/test_routing_replay_node.py``, which lives there because the two-schema fixture
corpus does — pytest shares a ``conftest`` down a package, not across siblings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from governed_bi.eval.arms import stub_arm
from governed_bi.eval.harness import run_arm
from governed_bi.eval.replay import (
    PINNED_SCHEMAS_KEY,
    attach_pinned_routing,
    licensed_drift,
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
    """``schemas: []`` is the ``no_schema_matched`` decline, not a decision to replay.

    Eight rows of the 2026-08-09 full run licensed nothing at all. Pinning those would freeze
    a retrieval failure into the next arm and make it look like a reproduced result rather than
    a carried-forward defect.
    """
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
    """An arm labelled pinned always has some fraction that is not; the count is the honesty."""
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
    """The count above lives in the run header; this is the same fact per row.

    ``attach_pinned_routing`` covers whatever the prior artifact happened to contain, so an arm
    described as pinned is always part pinned and part live. The header count says how much;
    only ``routing_pinned`` on the row says *which*, and without it no later reader can restrict
    an analysis to the replayed half.

    Driven through ``run_arm`` and asserted on the emitted rows, because that is the artifact.
    A per-row field is exactly the shape that survives being wired to a constant: ``True``
    everywhere reads as a fully pinned arm and ``False`` everywhere as one that ignored the
    flag, and both are plausible enough to be believed. So the two questions here must disagree
    in the row, not merely in the question dict the harness was handed.
    """
    covered = _artifact(tmp_path, [{"question_id": "replayed", "schemas": ["main"]}])
    questions: list[dict[str, Any]] = [
        {"question_id": "replayed", "question": "how many customers", "db_id": "main"},
        {"question_id": "routed_live", "question": "list customer ids", "db_id": "main"},
    ]
    counts = attach_pinned_routing(questions, routing_from_artifact(covered))
    assert counts == {"pinned": 1, "unpinned": 1}, "fixture no longer mixes the two cases"

    rows = run_arm(questions, stub_arm())

    assert {str(r["question_id"]): r["routing_pinned"] for r in rows} == {
        "replayed": True,
        "routed_live": False,
    }, (
        "the row does not distinguish a replayed shortlist from a live one, so the pinned "
        "fraction of a --replay-routing arm is unrecoverable from its artifact"
    )


# ── the residual the flag does NOT remove ─────────────────────────────────────


def test_drift_is_measured_over_the_rows_that_moved() -> None:
    """Averaging Jaccard over the identical rows too reports ~1.0 and hides the movers."""
    baseline = {"a": ["s.t1", "s.t2"], "b": ["s.t1"], "c": ["s.t9"]}
    rows = [
        {"question_id": "a", "licensed": ["s.t1", "s.t2"]},
        {"question_id": "b", "licensed": ["s.t1", "s.t3"]},
        {"question_id": "zz", "licensed": ["s.t1"]},
    ]
    drift = licensed_drift(rows, baseline)

    assert drift["compared"] == 2 and drift["identical"] == 1 and drift["moved"] == 1
    assert drift["not_in_baseline"] == 1
    assert drift["identical_rate"] == 0.5
    # b: {t1} shared, {t1,t3} union -> 1/2. The identical row scores 1.0 and must not be
    # averaged in, or a run where one turn moved completely still reports ~1.0.
    assert drift["mean_jaccard_when_moved"] == pytest.approx(0.5)


def test_an_artifact_replayed_against_itself_shows_no_drift(tmp_path: Path) -> None:
    """The zero point. Without it a drift statistic can be wrong in a way nothing reveals."""
    rows = [
        {"question_id": "a", "schemas": ["s"], "licensed": ["s.t1", "s.t2"]},
        {"question_id": "b", "schemas": ["s"], "licensed": []},
    ]
    path = _artifact(tmp_path, rows)
    baseline = {r["question_id"]: r["licensed"] for r in rows}

    assert routing_from_artifact(path) == {"a": ["s"], "b": ["s"]}
    drift = licensed_drift(rows, baseline)
    assert drift["moved"] == 0 and drift["identical_rate"] == 1.0
