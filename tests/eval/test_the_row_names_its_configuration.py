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


def test_a_measured_row_names_both_treatment_identities(tmp_path: Path) -> None:
    """The corpus and the prompt wording that produced the row.

    Same defect as ``knobs_resolved`` above, found the same way and later: ``Session`` mints
    both, ``stamp`` projects both, and ``project_turn``'s key list carried neither. The
    2026-08-09 artifacts therefore record which corpus produced them only in their *filename*
    — a human convention — and which prompt wording produced them not at all.

    That is fatal for the thing the prompt registry exists to enable. Two arms differing only
    in ``--prompt-variant`` would emit rows indistinguishable in every field, so a merged
    analysis could not tell the treatment from the control, and neither could a later reader.

    AGENTS.md: the corpus is the treatment identity of every measurement. An identity that
    lives in a filename is one ``mv`` away from being wrong.
    """
    rows = run_arm(_questions(), stub_arm(connector=_connector(tmp_path)))

    for row in rows:
        assert "corpus_content_hash" in row, "the row does not say which corpus produced it"
        assert "prompt_set_hash" in row, "the row does not say which prompt wording produced it"


def test_the_prompt_hash_on_the_row_moves_with_the_selected_variant() -> None:
    """Presence is not enough: the field has to *track* the selection.

    A row carrying a constant would satisfy the test above forever while reporting one
    treatment for both arms — the failure that test exists to catch, reintroduced one layer
    down. Asserted against the registry rather than against a literal, because a hardcoded
    digest here would pin the test to today's wording of every prompt in the tree.
    """
    from governed_bi.register.prompts import ANALYST, prompt_set_hash

    # Derived, not hardcoded: this asserted ``v3`` and started failing the day v3 became the
    # default, which is the test rotting rather than the property breaking. Any variant that
    # is not the default will do, and there must be one -- a registry whose only variant is
    # the default cannot express an A/B at all.
    others = sorted(v for v in ANALYST.variants if v != ANALYST.default)
    assert others, "ANALYST has no non-default variant, so no prompt A/B is expressible"

    for variant in others:
        assert prompt_set_hash({"analyst": variant}) != prompt_set_hash(), (
            f"selecting {variant!r} does not move prompt_set_hash, so a row cannot "
            "distinguish that arm from the default one"
        )
