"""``pool.map`` yields in input order, so a hung provider request on question 1 blocks question
2's already-finished row from reaching the crash-safe writer.

Experiment 008 hit this as an apparent dead run and worked around it with
``scripts/supervise.sh``. The returned list must stay ordered -- callers index it -- but the
writer must see a row the moment that row is done, not when its predecessor is.
"""

from __future__ import annotations

import threading

import pytest


def test_a_later_question_reaches_on_row_before_an_earlier_slow_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the ``as_completed`` fix. Under ``pool.map``, ``on_row`` fires in *input* order, so
    the slow question's row (index 0) reaches the writer before the fast question's row (index
    1) even though the fast one finishes first. A ``threading.Event`` makes the finish order
    deterministic instead of a ``time.sleep`` guess: the fast item sets the event, the slow item
    waits on it, so the slow item cannot finish first under any scheduler. ``workers=2`` is
    required -- with a single worker the slow item would wait on an event only the
    never-started fast item can set, and the test would hang forever without the timeout."""
    from governed_bi.eval import harness

    fast_ready = threading.Event()
    questions = [{"question_id": "slow"}, {"question_id": "fast"}]

    def fake_run_one(question, **_):
        if question["question_id"] == "fast":
            fast_ready.set()
        else:
            assert fast_ready.wait(timeout=5), "fast question never signalled -- test is broken"
        return {"question_id": question["question_id"]}

    # `run_index` calls `worker_state()`, which calls `compile_durable()` (renamed from
    # `compile_graph` by ADR 0014, which gave the harness a durable checkpointer), before it
    # calls `_run_one` -- stub it too, or this test builds a real LangGraph.
    monkeypatch.setattr(harness, "compile_durable", lambda *_a, **_k: object())
    monkeypatch.setattr(harness, "_run_one", fake_run_one)

    seen: list[str] = []
    rows = harness._run_concurrently(
        questions,
        arm=type("A", (), {"name": "a"})(),
        base_cfg={},
        session=None,
        run_id="r",
        order_sensitive_qids=frozenset(),
        workers=2,
        connector_factory=lambda: None,
        on_row=lambda _i, row: seen.append(row["question_id"]),
    )

    assert seen == ["fast", "slow"], "the writer waited on the slow question"
    assert [r["question_id"] for r in rows] == ["slow", "fast"], "return order changed"
