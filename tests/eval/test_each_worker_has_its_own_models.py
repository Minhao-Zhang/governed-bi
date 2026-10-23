"""Each worker thread builds its own model clients.

An async model client binds to the event loop that first uses it, and each worker runs its own
loop, so a client shared across workers fails every worker but the first with a RuntimeError.
"""

from __future__ import annotations

import threading
from typing import Any

from governed_bi.eval import harness


def test_the_model_factory_runs_once_per_worker_and_reaches_the_question(monkeypatch) -> None:
    built: list[int] = []
    seen: list[tuple[int, Any]] = []
    closed: list[int] = []

    def factory() -> dict[str, Any]:
        thread = threading.get_ident()
        built.append(thread)

        async def aclose() -> None:
            closed.append(thread)

        return {"agent_model": f"model-{thread}", "aclose": aclose}

    def fake_run_one(question: Any, *, models: Any = None, **_kw: Any) -> dict[str, Any]:
        assert "aclose" not in (models or {})
        seen.append((threading.get_ident(), (models or {}).get("agent_model")))
        return {"question_id": question["question_id"]}

    class _Graph:
        def run_coro(self, coro: Any) -> Any:
            import asyncio

            return asyncio.run(coro)

    monkeypatch.setattr(harness, "_run_one", fake_run_one)
    monkeypatch.setattr(harness, "compile_durable", _Graph)
    questions = [{"question_id": str(i)} for i in range(12)]
    rows = harness._run_concurrently(
        questions,
        config=None,  # type: ignore[arg-type]
        base_cfg={},
        session=None,
        run_id="r",
        order_sensitive_qids=frozenset(),
        workers=3,
        connector_factory=lambda: object(),
        on_row=None,
        model_factory=factory,
    )

    assert len(rows) == 12
    assert len(built) == len(set(built)) <= 3
    assert all(model == f"model-{thread}" for thread, model in seen)
    assert sorted(closed) == sorted(built)
