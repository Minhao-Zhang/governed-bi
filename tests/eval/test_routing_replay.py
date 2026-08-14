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
    drift_against,
    licensed_baseline,
    licensed_drift,
    pin_realised,
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


def test_a_pin_the_turn_did_not_run_on_is_not_recorded_as_a_replay(tmp_path: Path) -> None:
    """``routing_pinned`` is the outcome, not the intent (open-work 3.7).

    It read ``bool(question.get(PINNED_SCHEMAS_KEY))`` — the pin as the *driver attached* it,
    never as the *turn used* it. ``route_node`` applies a pin only to schemas the corpus knows
    and only if it runs at all, so the three cases the field silently reported as ``true`` were
    an unknown pin, a partial pin, and a turn that ended before routing.

    Here the pin names a schema this arm's routing never selects, so the turn runs on its own
    shortlist. The row must say so, or an analysis restricted to "the replayed half" is
    restricted to something else.
    """
    covered = _artifact(tmp_path, [{"question_id": "q", "schemas": ["nowhere"]}])
    questions: list[dict[str, Any]] = [
        {"question_id": "q", "question": "how many customers", "db_id": "main"}
    ]
    assert attach_pinned_routing(questions, routing_from_artifact(covered)) == {
        "pinned": 1,
        "unpinned": 0,
    }, "the fixture must attach a pin, or this asserts nothing"

    row = run_arm(questions, stub_arm())[0]
    assert row["schemas"] == ["main"], "fixture: the turn routed for itself"
    assert row["routing_pinned"] is False, (
        "the row claims its shortlist was replayed when the turn ran on a different one"
    )


def test_a_turn_that_ended_before_routing_is_not_recorded_as_a_replay() -> None:
    """The case that is live on disk: 3 rows on v4, 5 on v5 and 12 on v4-reflect say
    ``routing_pinned: true`` beside ``schemas: []``, and every one is a clarification that
    abstained before ``route_node``.

    Asserted through ``project_turn`` rather than through a served turn, because producing an
    interrupt from this fixture would test the interrupt rather than the field.
    """
    from governed_bi.eval.harness import project_turn

    row = project_turn(
        {"answer": {"outcome": "clarification", "record": {"schemas": []}}, "messages": []},
        question={"question_id": "q", "db_id": "d", PINNED_SCHEMAS_KEY: ["a", "b"]},
        arm="t",
    )
    assert row["routing_pinned"] is False

    ran = project_turn(
        {"answer": {"outcome": "answered", "record": {"schemas": ["a", "b"]}}, "messages": []},
        question={"question_id": "q", "db_id": "d", PINNED_SCHEMAS_KEY: ["a", "b"]},
        arm="t",
    )
    assert ran["routing_pinned"] is True, "the gate must still be able to say yes"


def test_a_partially_honoured_pin_is_not_the_pin() -> None:
    """``route_node`` drops pinned schemas the corpus does not know and keeps the rest, so the
    turn runs on a shortlist that is neither the pin nor a live route. A boolean saying "pinned"
    there would report two treatments as one."""
    from governed_bi.eval.harness import project_turn

    row = project_turn(
        {"answer": {"outcome": "answered", "record": {"schemas": ["a"]}}, "messages": []},
        question={"question_id": "q", "db_id": "d", PINNED_SCHEMAS_KEY: ["a", "ghost"]},
        arm="t",
    )
    assert row["routing_pinned"] is False


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


def test_the_drift_baseline_holds_only_the_rows_the_pin_covered(tmp_path: Path) -> None:
    """A row the pin skipped is not a row the pin failed to hold.

    The driver built the baseline from *every* row of the replayed artifact, including the
    ``no_schema_matched`` declines ``routing_from_artifact`` skips on purpose. Those were never
    pinned and are guaranteed to differ, so they entered the residual as drift the flag never
    claimed to prevent. Measured on the v4 arm: 6 such rows move the mean Jaccard over the
    movers from 0.7020 to 0.7049 and the identical rate from 0.0940 to 0.0937.

    Asserted as the same set as ``routing_from_artifact``, because two readers of one file that
    can disagree is how the two came apart in the first place.
    """
    path = _artifact(
        tmp_path,
        [
            {"question_id": "a", "schemas": ["s"], "licensed": ["s.t1"]},
            {"question_id": "declined", "schemas": [], "licensed": []},
            {"question_id": "b", "schemas": ["s"], "licensed": []},
        ],
    )
    baseline = licensed_baseline(path)

    assert set(baseline) == set(routing_from_artifact(path)) == {"a", "b"}
    assert "declined" not in baseline
    # An empty `licensed` on a row that *did* route is a measured zero and stays in.
    assert baseline["b"] == []


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


def test_both_sides_of_a_drift_contrast_come_out_of_one_function(tmp_path: Path) -> None:
    """A pinned figure and its unpinned reference must be the same statistic.

    The published sentence differenced ``mean_jaccard_when_moved`` (0.7049, 0.7029) against
    0.579 -- which is the mean over *every* compared row including the identical ones, a
    quantity ``licensed_drift`` refuses to compute because rows that scored 1.0 by definition
    drag it upward. The like-for-like value for the unpinned pair is 0.5719.

    Hand-computed here: two compared rows, one identical and one at Jaccard 1/2. The movers-only
    mean is 0.5; the all-rows mean the old sentence used would be 0.75. ``drift_against`` is the
    one door both sides go through, so the two cannot be produced by different code again.
    """
    baseline_rows = [
        {"question_id": "same", "schemas": ["s"], "licensed": ["s.t1"]},
        {"question_id": "moved", "schemas": ["s"], "licensed": ["s.t1"]},
    ]
    path = _artifact(tmp_path, baseline_rows)
    later = [
        {"question_id": "same", "licensed": ["s.t1"]},
        {"question_id": "moved", "licensed": ["s.t1", "s.t2"]},
    ]

    drift = drift_against(path, later)
    assert (drift["identical"], drift["moved"], drift["compared"]) == (1, 1, 2)
    assert drift["mean_jaccard_when_moved"] == pytest.approx(0.5)
    # The number the mixed contrast used, spelled out so the difference is visible. Nothing in
    # `replay.py` returns it, and this asserts that: it is 0.75 here, not 0.5.
    all_rows = (drift["mean_jaccard_when_moved"] * drift["moved"] + 1.0 * drift["identical"]) / 2
    assert all_rows == pytest.approx(0.75)
    assert "mean_jaccard" not in {k for k in drift if k.endswith("_all")}


# ── how much of an arm actually ran on the pin ────────────────────────────────


def test_the_realised_pin_count_is_readable_on_an_old_semantics_artifact() -> None:
    """``routing_pinned`` meant *intent* when every artifact on disk was written.

    Under the corrected semantics it is an outcome -- the turn's shortlist **is** the pinned
    one -- but the rows in ``runs/eval/`` predate that, so ``sum(r["routing_pinned"] is True)``
    returns the count of questions the pin *offered* (1 345 on v4, v5 and v4-reflect alike) and
    not the count it reached. The corrected figures 1 342 / 1 340 / 1 333 were published with no
    producer at all; this is the producer.

    Four rows, hand-counted: two ran on the pin, one carries the flag but ended before routing
    with no shortlist, and one was never pinnable. So flagged 3, realised 2, exact 2.
    """
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
    """The corroboration is only worth something if the two rules can disagree.

    ``realised`` and ``exact`` agreeing on all three arms is evidence precisely because
    ``exact`` is order-sensitive and could have come out lower. A row holding the pinned
    schemas in another order is counted separately rather than folded into either.
    """
    counts = pin_realised(
        [{"question_id": "a", "routing_pinned": True, "schemas": ["s2", "s1"]}],
        {"a": ["s1", "s2"]},
    )
    assert counts["realised"] == 1 and counts["exact"] == 0
    assert counts["same_set_out_of_order"] == 1
