"""Production ``file_raised`` 409s on a pause and copies the thread row after a write.

The HTTP dummy log never hits this path. These fakes stand in for a checkpointer-bearing
Pregel and ``Threads.set_status`` so the suite does not boot Redis or the Agent server.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from uuid import UUID, uuid4

import pytest

from governed_bi.api.raised_write import InFlightUnknown, ThreadBusy, file_raised
from governed_bi.serve.raised import raised_row

THREAD = str(uuid4())
ROW = raised_row(
    kind="from_refusal",
    turn_id="turn-1",
    thread_id=THREAD,
    note="too strict",
    report_id="rpt-turn-1-0123456789ab",
)


class _Task:
    def __init__(self) -> None:
        self.id = "task-1"
        self.interrupts: list[Any] = []


class _Snap:
    def __init__(self, *, nxt: tuple[str, ...] = (), values: dict[str, Any] | None = None) -> None:
        self.next = nxt
        self.values = values if values is not None else {}
        self.tasks = (_Task(),)


class _Graph:
    def __init__(self, *, nxt: tuple[str, ...] = ()) -> None:
        self._next = nxt
        self.updates: list[tuple[Any, Any, str | None]] = []

    async def aget_state(self, config: dict[str, Any]) -> _Snap:
        if self.updates:
            return _Snap(nxt=(), values={"raised": [ROW]})
        return _Snap(nxt=self._next)

    async def aupdate_state(
        self, config: dict[str, Any], values: dict[str, Any], as_node: str | None = None
    ) -> dict[str, Any]:
        self.updates.append((config, values, as_node))
        return config


class _Conn:
    def __init__(self, runs: list[dict[str, Any]] | None = None) -> None:
        self.store = {"runs": list(runs or []), "threads": []}


def test_file_raised_409s_when_the_thread_is_paused() -> None:
    graph = _Graph(nxt=("agent_core",))

    async def _must_not_publish(*_a: Any) -> None:
        raise AssertionError("must not copy the thread row on a paused thread")

    with pytest.raises(ThreadBusy, match="paused"):
        asyncio.run(file_raised(THREAD, ROW, graph=graph, conn=_Conn(), publish=_must_not_publish))
    assert graph.updates == []


def test_file_raised_409s_when_a_run_is_in_flight() -> None:
    graph = _Graph()
    conn = _Conn(runs=[{"thread_id": UUID(THREAD), "status": "running"}])

    with pytest.raises(ThreadBusy, match="in-flight"):
        asyncio.run(file_raised(THREAD, ROW, graph=graph, conn=conn, publish=None))
    assert graph.updates == []


def test_file_raised_refuses_when_it_cannot_read_the_runtime_s_runs() -> None:
    """A runtime this reader does not understand is refused, not assumed idle.

    The guard reads the inmem ops store. On any other connection — Postgres keeps runs in
    a table — it has no answer, and the earlier version returned ``False``, i.e. "no run is
    in flight", without having looked. Three shapes of not-knowing are pinned here, and all
    three must land on ``InFlightUnknown`` (a ``ThreadBusy``, so the route still 409s rather
    than 500s) with the connection named in the message.
    """
    graph = _Graph()

    class _Opaque:
        """A connection with no ops store at all."""

    class _NoRuns:
        def __init__(self) -> None:
            self.store = {"threads": []}

    for conn in (_Opaque(), _NoRuns(), _Conn(runs=[object()])):  # type: ignore[list-item]
        with pytest.raises(InFlightUnknown, match="cannot tell"):
            asyncio.run(file_raised(THREAD, ROW, graph=graph, conn=conn, publish=None))
    assert graph.updates == [], "nothing may be appended on an undetermined run state"

    assert issubclass(InFlightUnknown, ThreadBusy), "the route maps ThreadBusy to 409"


def test_file_raised_treats_an_empty_run_list_as_a_real_answer() -> None:
    """``runs: []`` is not "unknown". The inmem store initialises the key at ``connect``,
    so an empty list means no runs exist and the append should go through — otherwise
    fail-closed would close the route entirely on the runtime it does support."""
    graph = _Graph()
    state = asyncio.run(file_raised(THREAD, ROW, graph=graph, conn=_Conn(runs=[]), publish=None))
    assert graph.updates[0][2] == "raise_note"
    assert state.values["raised"][0]["report_id"] == ROW["report_id"]


def test_file_raised_updates_through_raise_note_then_copies_the_thread_row() -> None:
    graph = _Graph()
    published: list[tuple[Any, UUID, dict[str, Any]]] = []

    async def _publish(conn: Any, thread_id: UUID, checkpoint: dict[str, Any]) -> None:
        published.append((conn, thread_id, checkpoint))

    conn = _Conn()
    state = asyncio.run(file_raised(THREAD, ROW, graph=graph, conn=conn, publish=_publish))
    assert graph.updates[0][2] == "raise_note"
    assert graph.updates[0][1] == {"raised": [ROW]}
    assert published[0][1] == UUID(THREAD)
    assert published[0][2]["values"]["raised"][0]["report_id"] == ROW["report_id"]
    assert published[0][2]["next"] == []
    assert state.values["raised"][0]["kind"] == "from_refusal"


def test_the_production_writer_does_not_call_threads_update() -> None:
    from governed_bi.api import raised_write

    wired = inspect.getsource(raised_write._file_raised_in_server)
    assert "handle_event" not in wired
    assert "State.post" not in wired
    assert "set_status" in wired
    assert "get_graph" in wired
    hop = inspect.getsource(raised_write._hop)
    assert "run_coroutine_threadsafe" in hop
    assert "asyncio.run" not in hop
    append = inspect.getsource(raised_write.file_raised)
    assert 'as_node="raise_note"' in append or "as_node='raise_note'" in append
