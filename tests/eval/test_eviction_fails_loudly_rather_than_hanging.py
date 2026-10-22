"""Sync delete on a loop-bound saver blocks and does not raise, so the fallback refuses by name."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from governed_bi.eval import harness


def _evict_bounded(compiled: Any, thread_id: str, *, seconds: float = 5.0) -> BaseException | None:
    """Run ``_evict`` off-thread; return what it raised, or fail the test if it blocked.

    Not ``pytest.raises`` directly: the defect this file guards is a call that neither returns nor
    raises, and no assertion can observe that from the thread it happened on.
    """
    box: dict[str, BaseException | None] = {}

    def call() -> None:
        try:
            harness._evict(compiled, thread_id)
            box["result"] = None
        except BaseException as exc:  # noqa: BLE001 — the point is to report whatever came out
            box["result"] = exc

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(seconds)
    assert "result" in box, (
        f"`_evict` neither returned nor raised within {seconds} s, so it is blocking on a "
        "loop-bound `delete_thread` again — a config would hang on question one with no output"
    )
    return box["result"]


class _RenamedAwaySaver:
    """``loop`` is the discriminator. An in-memory saver has none, so its sync delete still runs."""

    loop = object()

    def __init__(self) -> None:
        self.called: list[str] = []

    def delete_thread(self, thread_id: str) -> None:  # pragma: no cover — must never be reached
        self.called.append(thread_id)


class _LoopFreeSaver:
    """``InMemorySaver``-shaped: a plain sync ``delete_thread`` and no loop of its own."""

    def __init__(self) -> None:
        self.called: list[str] = []

    def delete_thread(self, thread_id: str) -> None:
        self.called.append(thread_id)


def test_a_missing_adelete_thread_raises_instead_of_blocking() -> None:
    """The designated fallback, on a loop-bound saver, is refused by name.

    This is the scenario the guard exists for: the async method is gone, the sync namesake is
    still there, and calling it would block forever.
    """
    saver = _RenamedAwaySaver()
    compiled = SimpleNamespace(checkpointer=saver, run_coro=lambda coro: None, _loop=object())

    raised = _evict_bounded(compiled, "t-renamed")

    assert isinstance(raised, harness.EvictionWouldHang), (
        f"expected EvictionWouldHang, got {raised!r} — a loop-bound `delete_thread` must not be "
        "called, and returning quietly would leak the config's whole database instead"
    )
    assert "adelete_thread=False" in str(raised), (
        "the message must name which half was unreachable, or the failure is unactionable"
    )
    assert saver.called == [], "the blocking bridge was called after all"


def test_a_loop_free_saver_still_gets_the_synchronous_call() -> None:
    """Raising on an in-memory saver would break the path that has no pinned loop."""
    saver = _LoopFreeSaver()
    compiled = SimpleNamespace(checkpointer=saver)

    assert _evict_bounded(compiled, "t-plain") is None
    assert saver.called == ["t-plain"]


def test_the_real_durable_saver_is_loop_bound_and_is_refused(tmp_path: Path) -> None:
    """The guard is checked on a real durable saver, not only on a stand-in."""
    from governed_bi.serve.graph import compile_durable

    graph = compile_durable(path=tmp_path / "evict-guard.sqlite")
    try:
        saver = graph.checkpointer
        assert getattr(saver, "loop", None) is not None, (
            "the installed AsyncSqliteSaver no longer carries its own loop, so `_evict`'s guard "
            "no longer recognises the saver it was written for"
        )
        stranded = SimpleNamespace(checkpointer=saver, run_coro=graph.run_coro, _loop=None)
        raised = _evict_bounded(stranded, "t-stranded")
    finally:
        graph.close()

    assert isinstance(raised, harness.EvictionWouldHang)
    assert type(saver).__name__ in str(raised)
