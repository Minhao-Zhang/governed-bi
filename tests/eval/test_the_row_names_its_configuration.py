"""A measurement row must say what produced it.

Measured on the 2026-08-07 pooled run: ``knobs_resolved`` was absent — not empty, missing —
from 1351/1351 rows of both arms, so ``measure.gates`` returned ``cannot_evaluate`` and no
figure could be joined to the configuration it ran under. ``Session.turn`` wrote the field and
``stamp`` projected it; ``eval.harness.project_turn`` built the row from a fixed key list that
did not carry it, and the same list omitted the question's ``db_id``, which is the input every
funnel stage below ``schema_routed`` is conditioned on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from governed_bi.datasource.sqlite import SqliteConnector
from governed_bi.eval.arms import stub_arm
from governed_bi.eval.harness import run_arm
from governed_bi.eval.report import arm_population, evaluate_arm
from governed_bi.measure.gates import Verdict


def _connector(tmp_path: Path) -> SqliteConnector:
    db = tmp_path / "customers.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO customers VALUES (1, 'a'), (2, 'b')")
    conn.commit()
    conn.close()
    connector = SqliteConnector(db)
    connector._connect()  # noqa: SLF001
    return connector


def _questions() -> list[dict]:
    return [
        {
            "question_id": "q1",
            "question": "how many customers",
            "db_id": "main",
            "gold_sql": "SELECT COUNT(*) AS n FROM customers",
        },
        {
            "question_id": "q2",
            "question": "list customer ids",
            "db_id": "main",
            "gold_sql": "SELECT id FROM customers ORDER BY id",
        },
    ]


def test_a_measured_row_carries_the_configuration_and_the_gold_schema(tmp_path: Path) -> None:
    """Driven through the real harness and judged by the real gate, not by key presence.

    The gate is the thing that broke: asserting ``"knobs_resolved" in row`` would pass for a
    row carrying ``{}``, which ``_knobs_resolved_gate`` reads as a genuine one-configuration
    arm whose every knob is None.
    """
    rows = run_arm(_questions(), stub_arm(connector=_connector(tmp_path)))

    for row in rows:
        assert isinstance(row["knobs_resolved"], dict) and row["knobs_resolved"]
        assert row["db_id"] == "main", "the gold schema comes from the question, not the turn"

    knobs_gate = next(
        g for g in evaluate_arm(arm_population(rows, label="stub")) if g.field == "knobs_resolved"
    )
    assert knobs_gate.verdict is Verdict.passed, knobs_gate.render()


def test_a_crashed_row_still_names_the_arm_it_crashed_in(tmp_path: Path) -> None:
    """A crash is not a reason to lose the configuration.

    The arm's knobs and the question's gold schema are known before the turn starts. Without
    them one crashed question makes the whole arm's knobs gate ``cannot_evaluate`` — the arm
    refuses to be quoted for a reason unrelated to the crash — and drops the row out of the
    funnel's routing stage rather than counting it as a loss there.
    """
    import governed_bi.eval.harness as harness

    class _Exploding:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("provider went away")

    original = harness.compile_graph
    harness.compile_graph = lambda *a, **k: _Exploding()  # type: ignore[assignment]
    try:
        connector = _connector(tmp_path)
        rows = run_arm(
            _questions(),
            stub_arm(connector=connector),
            workers=2,
            connector_factory=lambda: connector,
        )
    finally:
        harness.compile_graph = original  # type: ignore[assignment]

    assert [r["outcome"] for r in rows] == ["crashed", "crashed"]
    for row in rows:
        assert row["db_id"] == "main"
        assert "knobs_resolved" in row


class _FakeSession:
    """The parts of ``Session`` the harness touches, so the override path is testable offline."""

    knobs_resolved = {"route_top_n": 3}

    def turn(self, question: str, **_kwargs) -> dict:
        return {
            "question": question,
            "turn_index": 1,
            "run_id": "run-fake",
            "turn_id": f"turn-{question[:8]}",
            "question_id": question[:8],
            "attempt_id": "attempt-1",
            "db_id": "main",
            "corpus_content_hash": "corpus-fake",
            "prompt_set_hash": "prompt-fake",
            # What the defect looks like: the session's value, unconditionally.
            "knobs_resolved": dict(self.knobs_resolved),
            "n_re_served": 0,
            "evidence": "",
            "messages": [],
            "usage": [],
            "clarifications": [],
        }


def test_a_per_question_knob_override_is_not_silently_replaced(tmp_path: Path) -> None:
    """``--top-n`` was a no-op on every arm that passed a session.

    ``tools/run_datalake_eval.py`` writes ``question["knobs_resolved"]`` and prints the
    override in its header; ``Session.turn`` then overwrote it with the session's own knobs, so
    the run served the register default while announcing something else. The no-session path
    (``_base_turn``) had always honoured the question, so the two paths disagreed about what a
    turn's configuration is — and with ``knobs_resolved`` now on the row, whichever one is
    wrong becomes a published lie rather than an invisible one.
    """
    questions = [{**q, "knobs_resolved": {"route_top_n": 7}} for q in _questions()]
    rows = run_arm(
        questions,
        stub_arm(connector=_connector(tmp_path)),
        session=_FakeSession(),
    )
    assert all(r["knobs_resolved"]["route_top_n"] == 7 for r in rows)
