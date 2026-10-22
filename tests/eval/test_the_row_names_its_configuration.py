"""Absent settings are missing, not an empty dict. An empty dict reads as every setting unset."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from governed_bi.datasource.sqlite import SqliteConnector
from governed_bi.eval.configs import stub_config
from governed_bi.eval.harness import run_config
from governed_bi.eval.report import config_population, evaluate_config
from governed_bi.measure.checks import Result


def _connector(tmp_path: Path) -> SqliteConnector:
    db = tmp_path / "customers.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO customers VALUES (1, 'a'), (2, 'b')")
    conn.commit()
    conn.close()
    connector = SqliteConnector(db)
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
    """``{}`` reads as a real configuration whose every setting is missing, so key presence is not the check."""
    rows = run_config(_questions(), stub_config(connector=_connector(tmp_path)))

    for row in rows:
        assert isinstance(row["settings_resolved"], dict) and row["settings_resolved"]
        assert row["db_id"] == "main", "the gold schema comes from the question, not the turn"

    settings_check = next(
        g for g in evaluate_config(config_population(rows, label="stub")) if g.field == "settings_resolved"
    )
    assert settings_check.result is Result.passed, settings_check.render()


def test_a_crashed_row_still_names_the_config_it_crashed_in(tmp_path: Path) -> None:
    """A crash still carries settings and the gold schema. They are known before the turn starts."""
    import governed_bi.eval.harness as harness

    class _Exploding:
        def invoke(self, *_args, **_kwargs):
            raise RuntimeError("provider went away")

    # `compile_durable`, not `compile_graph`: the concurrent path builds a durable graph
    # per worker so each gets its own loop-bound saver, and that is the boundary a crash
    # rides in on.
    original = harness.compile_durable
    harness.compile_durable = lambda *a, **k: _Exploding()  # type: ignore[assignment]
    try:
        connector = _connector(tmp_path)
        rows = run_config(
            _questions(),
            stub_config(connector=connector),
            workers=2,
            connector_factory=lambda: connector,
        )
    finally:
        harness.compile_durable = original  # type: ignore[assignment]

    assert [r["outcome"] for r in rows] == ["crashed", "crashed"]
    for row in rows:
        assert row["db_id"] == "main"
        assert "settings_resolved" in row


class _FakeSession:
    """The parts of ``Session`` the harness touches, so the override path is testable offline."""

    settings_resolved = {"route_top_n": 3}

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
            "settings_resolved": dict(self.settings_resolved),
            "n_re_served": 0,
            "evidence": "",
            "messages": [],
            "usage": [],
            "clarifications": [],
        }


def test_a_per_question_setting_override_is_not_silently_replaced(tmp_path: Path) -> None:
    """``Session.turn`` overwrites settings, so a per-question override must be reapplied."""
    questions = [{**q, "settings_resolved": {"route_top_n": 7}} for q in _questions()]
    rows = run_config(
        questions,
        stub_config(connector=_connector(tmp_path)),
        session=_FakeSession(),
    )
    assert all(r["settings_resolved"]["route_top_n"] == 7 for r in rows)


def test_a_measured_row_names_both_under_test_identities(tmp_path: Path) -> None:
    """Two runs that differ only in corpus or prompt must differ in those fields. A constant ``None`` would not."""
    connector = _connector(tmp_path)
    under_tests = {
        "alpha": ("corpus-alpha-0f1e", "prompt-alpha-9c8d"),
        "beta": ("corpus-beta-77aa", "prompt-beta-b31c"),
    }
    rows_by_under_test = {
        label: run_config(
            [
                {**q, "corpus_content_hash": corpus, "prompt_set_hash": prompt}
                for q in _questions()
            ],
            stub_config(connector=connector),
        )
        for label, (corpus, prompt) in under_tests.items()
    }

    for label, (corpus, prompt) in under_tests.items():
        for row in rows_by_under_test[label]:
            assert row["corpus_content_hash"] == corpus, (
                f"the row does not say which corpus produced it: {row['corpus_content_hash']!r}"
            )
            assert row["prompt_set_hash"] == prompt, (
                "the row does not say which prompt wording produced it: "
                f"{row['prompt_set_hash']!r}"
            )

    alpha, beta = rows_by_under_test["alpha"], rows_by_under_test["beta"]
    assert {r["corpus_content_hash"] for r in alpha}.isdisjoint(
        {r["corpus_content_hash"] for r in beta}
    ), "two corpora produced rows that name the same corpus"
    assert {r["prompt_set_hash"] for r in alpha}.isdisjoint(
        {r["prompt_set_hash"] for r in beta}
    ), "two prompt wordings produced rows that name the same wording"


def test_the_prompt_hash_on_the_row_moves_with_the_selected_variant() -> None:
    """The field has to track the selection. A constant would report one setting for both configs."""
    from governed_bi.registry.prompts import ANALYST, prompt_set_hash

    # Any non-default variant will do. A hardcoded default rots when the default moves.
    others = sorted(v for v in ANALYST.variants if v != ANALYST.default)
    assert others, "ANALYST has no non-default variant, so no prompt A/B is expressible"

    for variant in others:
        assert prompt_set_hash({"analyst": variant}) != prompt_set_hash(), (
            f"selecting {variant!r} does not move prompt_set_hash, so a row cannot "
            "distinguish that config from the default one"
        )
