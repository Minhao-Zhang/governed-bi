"""The routing driver must empty its saver as it goes.

``eval/datalake.routing_recall`` builds ``compile_graph()`` — whose default is an
``InMemorySaver`` — and gives every question its own thread, which is correct (a shared thread
would carry the previous question's per-turn channels in). What was missing is the other half:
nothing ever removed a thread, so the saver held one per question for the life of the process.
``serve/graph.compile_graph``'s own docstring puts that at roughly 135 MB over a pooled run.

It matters more here than the number suggests, because this is the driver run most often:
retrieval measurement calls no provider, so a full-scale run on a laptop is the normal case.

Asserted on **the saver's own state**, not on a byte count or a process RSS, both of which are
allocator noise dressed up as a measurement.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from governed_bi.eval import datalake


class _FakeSession:
    """Only what ``routing_recall`` asks a session for."""

    knobs_resolved: dict[str, Any] = {}

    def turn(self, question: str, *, turn_index: int = 1, **_: Any) -> dict[str, Any]:
        return {"question": question, "turn_index": turn_index}

    def configurable(self, *, question: str | None = None) -> dict[str, Any]:
        return {"configurable": {"question": question}}


class _FakeGraph:
    """``_SyncApp``-shaped, checkpoints for real, runs no nodes.

    The saver is a **real** ``InMemorySaver`` because it is the thing under test; the graph body
    is not, because routing behaviour is measured elsewhere and would need a corpus, a connector
    and a model to reach.
    """

    def __init__(self) -> None:
        self.checkpointer = InMemorySaver()
        self.threads: list[str] = []

    def invoke(self, turn: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        thread_id = config["configurable"]["thread_id"]
        self.threads.append(thread_id)
        self.checkpointer.put(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            {
                "v": 4,
                "id": f"cp-{thread_id}",
                "ts": "2026-08-20T00:00:00+00:00",
                "channel_values": {"question": turn["question"]},
                "channel_versions": {"question": "1"},
                "versions_seen": {},
            },
            {"source": "loop", "step": 1, "parents": {}},
            {"question": "1"},
        )
        return {
            "schemas": ["beer_factory"],
            "retrieved": {"schema_ranking": [("beer_factory", 1.0)]},
            "licensed": ["beer_factory.customers"],
            "path_kind": "answered",
            "terminal_reason": None,
        }


def _retained(saver: InMemorySaver) -> dict[str, Any]:
    """Threads that still hold a checkpoint.

    Not ``saver.storage`` itself: it is a ``defaultdict``, so a *read* of a missing thread
    inserts an empty namespace map and a test asserting emptiness would fail on a lookup.
    """
    return {thread: ns for thread, ns in saver.storage.items() if any(ns.values())}


def test_the_routing_loop_leaves_no_thread_in_the_saver(monkeypatch: Any) -> None:
    graph = _FakeGraph()
    # Patched at the source module: `routing_recall` imports `compile_graph` inside the function,
    # so the name is resolved from `serve.graph` on every call.
    monkeypatch.setattr("governed_bi.serve.graph.compile_graph", lambda **_: graph)

    questions = [
        {"question_id": f"q{i}", "question": f"question {i}?", "db_id": "beer_factory"}
        for i in range(6)
    ]
    rows = datalake.routing_recall(questions, session=_FakeSession())

    assert len(rows) == len(questions)
    # The loop really ran, and really used a thread per question — without this the emptiness
    # assertion below would pass on a driver that served nothing at all.
    assert len(set(graph.threads)) == len(questions)

    assert not _retained(graph.checkpointer), (
        "the routing driver retained a checkpoint per question, so a pooled run grows without "
        "bound for state no later question reads"
    )
    # `delete_thread` also drops the channel blobs, which is where the bulk of a real turn's
    # bytes live; a saver emptied of checkpoints while keeping its blobs would not be fixed.
    assert not graph.checkpointer.blobs


def test_in_memory_delete_thread_is_synchronous_at_this_version() -> None:
    """Pinned because getting it backwards is how housekeeping becomes a hang.

    ``InMemorySaver.delete_thread`` is a plain ``def`` over three dicts
    (``langgraph/checkpoint/memory/__init__.py:505``), so the routing loop calls it directly.
    ``AsyncSqliteSaver``'s sync namesake instead blocks on
    ``run_coroutine_threadsafe(...).result()`` against a loop that is not running, which is why
    the durable callers use ``adelete_thread``. If a future version makes this one a coroutine,
    the routing loop starts leaking silently — a never-awaited coroutine deletes nothing.
    """
    import inspect

    assert not inspect.iscoroutinefunction(InMemorySaver.delete_thread)
    assert inspect.iscoroutinefunction(InMemorySaver.adelete_thread)
