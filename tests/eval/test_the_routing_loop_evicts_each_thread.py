"""Each question gets its own thread, and the saver must drop it; a shared thread would carry the last question."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from governed_bi.eval import datalake


class _FakeSession:
    """Only what ``routing_recall`` asks a session for."""

    settings_resolved: dict[str, Any] = {}

    def turn(self, question: str, *, turn_index: int = 1, **_: Any) -> dict[str, Any]:
        return {"question": question, "turn_index": turn_index}

    def configurable(self, *, question: str | None = None) -> dict[str, Any]:
        return {"configurable": {"question": question}}


class _FakeGraph:
    """The saver is a real ``InMemorySaver``. The graph body is not what this file measures."""

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
            "retrieved_tables": ["beer_factory.customers"],
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
    """In-memory delete is synchronous. The sqlite sync name blocks on a stopped loop, so durable callers use the async
    one."""
    import inspect

    assert not inspect.iscoroutinefunction(InMemorySaver.delete_thread)
    assert inspect.iscoroutinefunction(InMemorySaver.adelete_thread)
