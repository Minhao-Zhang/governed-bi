"""The returned list stays ordered, but the writer must see a row when that row finishes."""

from __future__ import annotations

import threading

import pytest


def test_a_later_question_reaches_on_row_before_an_earlier_slow_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The slow item returns only after ``on_row`` has already run for the fast row."""
    from governed_bi.eval import harness

    seen: list[str] = []
    fast_ready = threading.Event()
    fast_written = threading.Event()
    questions = [{"question_id": "slow"}, {"question_id": "fast"}]

    def fake_run_one(question, **_):
        if question["question_id"] == "fast":
            fast_ready.set()
        else:
            assert fast_ready.wait(timeout=5), "fast question never signalled -- test is broken"
            assert fast_written.wait(timeout=5), (
                "the fast row never reached on_row while the slow question was still running, "
                "which is the defect: the writer is waiting for question 1 to finish before it "
                "writes question 2's finished row"
            )
        return {"question_id": question["question_id"]}

    def record(_index, row):
        seen.append(row["question_id"])
        if row["question_id"] == "fast":
            fast_written.set()

    # `run_index` calls `worker_state()`, which calls `compile_durable()` (renamed from
    # `compile_graph` by ADR 0014, which gave the harness a durable checkpointer), before it
    # calls `_run_one` -- stub it too, or this test builds a real LangGraph.
    monkeypatch.setattr(harness, "compile_durable", lambda *_a, **_k: object())
    monkeypatch.setattr(harness, "_run_one", fake_run_one)

    rows = harness._run_concurrently(
        questions,
        config=type("A", (), {"name": "a"})(),
        base_cfg={},
        session=None,
        run_id="r",
        order_sensitive_qids=frozenset(),
        workers=2,
        connector_factory=lambda: None,
        on_row=record,
    )

    assert seen == ["fast", "slow"], "the writer waited on the slow question"
    assert [r["question_id"] for r in rows] == ["slow", "fast"], "return order changed"
