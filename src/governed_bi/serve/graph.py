"""Serve graph wiring (ADR 0005 §3.1).

LangGraph loads this file by path, so annotations stay raw."""

import asyncio
from collections.abc import Mapping
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from governed_bi.serve.nodes.abstain import abstain_node
from governed_bi.serve.nodes.agent_core import agent_core_node
from governed_bi.serve.nodes.assemble import assemble_node
from governed_bi.serve.nodes.channels import (
    channel_entity_node,
    channel_example_node,
    channel_metric_node,
    channel_schema_node,
    channel_term_node,
)
from governed_bi.serve.nodes.finish import finish
from governed_bi.serve.nodes.guard import guard_node
from governed_bi.serve.nodes.narrate import narrate_node
from governed_bi.serve.nodes.rewrite import rewrite_node
from governed_bi.serve.nodes.route_retrieve import connect_node, resolve_node, route_node
from governed_bi.serve.nodes.terminal import decline_node, refuse_node
from governed_bi.serve.state import ServeInput, ServeOutput, ServeState
from governed_bi.serve.wrap import wrap_node

__all__ = ["build_graph", "compile_graph"]

_CHANNEL_NODES = (
    ("channel_schema", channel_schema_node),
    ("channel_term", channel_term_node),
    ("channel_metric", channel_metric_node),
    ("channel_entity", channel_entity_node),
    ("channel_example", channel_example_node),
)


def _after_accept(state: ServeState) -> Literal["guard", "finish"]:
    """A crash must not enter ``guard`` on a prior turn's channels."""
    if state.get("path_kind") == "crashed":
        return "finish"
    return "guard"


def _after_guard(state: ServeState) -> Literal["refuse", "rewrite", "finish"]:
    if state.get("path_kind") == "crashed":
        return "finish"
    guard: Mapping[str, Any] = state.get("guard") or {}
    if guard.get("outcome") == "blocked":
        return "refuse"
    return "rewrite"


def _after_rewrite(state: ServeState) -> Literal["fanout", "finish"]:
    if state.get("path_kind") == "crashed":
        return "finish"
    return "fanout"


def _after_route(state: ServeState) -> Literal["decline", "resolve", "finish"]:
    if state.get("path_kind") == "crashed":
        return "finish"
    if state.get("path_kind") == "decline":
        return "decline"
    return "resolve"


def _after_connect(state: ServeState) -> Literal["decline", "assemble", "finish"]:
    if state.get("path_kind") == "crashed":
        return "finish"
    if state.get("path_kind") == "decline":
        return "decline"
    return "assemble"


def _after_abstain(state: ServeState) -> Literal["decline", "agent_core", "finish"]:
    """Reads ``path_kind``, the same channel ``route`` and ``connect`` write (ADR 0013)."""
    if state.get("path_kind") == "crashed":
        return "finish"
    if state.get("path_kind") == "decline":
        return "decline"
    return "agent_core"


def _skip_if_terminal(state: ServeState) -> Literal["finish", "continue"]:
    if state.get("path_kind") in ("refuse", "decline", "crashed"):
        return "finish"
    return "continue"


# Only ``guard`` is timed out here. ``wrap_node`` cannot cancel a sync node's thread.
# ``narrate`` is excluded: a timeout would mark ``crashed`` after ``agent_core`` answered.
# The five channels stay off until their shared provider-quota effect is measured.
_CANCELLABLE = frozenset({"guard"})


def _node_timeout(name: str) -> float | None:
    """``agent_core`` times itself so a timeout can still project the ledger."""
    from governed_bi.registry.settings import effective_settings

    if name not in _CANCELLABLE:
        return None
    return float(effective_settings()["rail_node_timeout_s"])


def build_graph(*, accept: Any = None, record: Any = None) -> StateGraph:
    # Schemas only with `accept`: a client turn must not write the rest of state. See ADR 0007.
    # `output_schema` does not cover `values` frames or `get_state`.
    # Without `accept`, in-process callers pass and read all of ServeState on purpose.
    graph = (
        StateGraph(ServeState, input_schema=ServeInput, output_schema=ServeOutput)
        if accept is not None
        else StateGraph(ServeState)
    )

    def rail(name: str, fn: Any, **kw: Any) -> None:
        """LangGraph 1.2.10 ``add_node`` timeout re-raises and does not save the run."""
        graph.add_node(name, wrap_node(name, fn, timeout=_node_timeout(name), **kw))

    rail("guard", guard_node)
    rail("rewrite", rewrite_node)
    for name, fn in _CHANNEL_NODES:
        rail(name, fn)
    rail("route", route_node)
    rail("resolve", resolve_node)
    rail("connect", connect_node)
    rail("assemble", assemble_node)
    # `stream=False`: a disabled decision adds no timeline rows, and it emits its own (ADR 0013).
    graph.add_node("abstain", wrap_node("abstain", abstain_node, stream=False))
    rail("agent_core", agent_core_node)
    # Through `rail` so `_CANCELLABLE` stays the only timeout. Provider calls stay on `request_timeout`.
    rail("narrate", narrate_node)
    rail("refuse", refuse_node)
    rail("decline", decline_node)
    # Unwrapped: nothing after finish can record a wrap crash.
    graph.add_node("finish", finish)  # type: ignore[type-var]  # langgraph bounds node input to TypedDict-likes
    # A node with no edge from START is a write path the topology does not show.

    def _fanout_passthrough(state: ServeState) -> dict[str, Any]:
        return {}

    # Not through `rail`: the node name would be `fanout` while the stage is `channel_schema`.
    graph.add_node("fanout", wrap_node("channel_schema", _fanout_passthrough, stream=False))

    if accept is not None:
        graph.add_node("accept", wrap_node("accept", accept))
        graph.add_edge(START, "accept")
        graph.add_conditional_edges(
            "accept",
            _after_accept,
            {"guard": "guard", "finish": "finish"},
        )
    else:
        graph.add_edge(START, "guard")
    graph.add_conditional_edges(
        "guard",
        _after_guard,
        {"refuse": "refuse", "rewrite": "rewrite", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "rewrite",
        _after_rewrite,
        {"fanout": "fanout", "finish": "finish"},
    )
    for name, _ in _CHANNEL_NODES:
        graph.add_edge("fanout", name)
        graph.add_edge(name, "route")

    graph.add_conditional_edges(
        "route",
        _after_route,
        {"decline": "decline", "resolve": "resolve", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "resolve",
        _skip_if_terminal,
        {"finish": "finish", "continue": "connect"},
    )
    graph.add_conditional_edges(
        "connect",
        _after_connect,
        {"decline": "decline", "assemble": "assemble", "finish": "finish"},
    )
    graph.add_conditional_edges(
        "assemble",
        _skip_if_terminal,
        {"finish": "finish", "continue": "abstain"},
    )
    graph.add_conditional_edges(
        "abstain",
        _after_abstain,
        {"decline": "decline", "agent_core": "agent_core", "finish": "finish"},
    )
    # Terminals skip narrate: refusal and decline wording is system copy.
    graph.add_edge("agent_core", "narrate")
    graph.add_edge("narrate", "finish")
    graph.add_edge("refuse", "finish")
    graph.add_edge("decline", "finish")
    if record is not None:
        graph.add_node("record", record)
        graph.add_edge("finish", "record")
        graph.add_edge("record", END)
    else:
        graph.add_edge("finish", END)
    return graph


class _SyncApp:
    """``.invoke()`` on the raw graph raises ``TypeError``, and swallowing it looked like a crash (ADR 0014)."""

    def __init__(self, app: Any, *, loop: Any | None = None) -> None:
        self._app = app
        #: ``None`` builds a fresh loop per call. See ADR 0014.
        self._loop = loop

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)

    def run_coro(self, coro: Any) -> Any:
        """A durable saver must stay on its opening loop (ADR 0014)."""
        if self._loop is None:
            return asyncio.run(coro)
        return self._loop.run_until_complete(coro)

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return self.run_coro(self._app.ainvoke(*args, **kwargs))

    def close(self) -> None:
        """An unclosed ``aiosqlite`` worker is a non-daemon thread, so the process hangs (ADR 0014)."""
        loop, self._loop = self._loop, None
        if loop is None:
            return
        conn = getattr(getattr(self._app, "checkpointer", None), "conn", None)
        close = getattr(conn, "close", None)
        if close is not None:
            try:
                loop.run_until_complete(close())
            except Exception:  # noqa: BLE001 -- a connection that will not close is not a turn
                pass
        loop.close()

    def __enter__(self) -> "_SyncApp":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def update_state(self, config: Any, values: Any, as_node: str | None = None) -> Any:
        return self.run_coro(self._app.aupdate_state(config, values, as_node=as_node))

    def get_state(self, config: Any) -> Any:
        return self.run_coro(self._app.aget_state(config))

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        """Drained, then replayed: order is kept and incrementality is not."""

        async def drain() -> list[Any]:
            return [chunk async for chunk in self._app.astream(*args, **kwargs)]

        return iter(self.run_coro(drain()))


def as_sync(app: Any) -> _SyncApp:
    return _SyncApp(app)


def compile_graph(*, checkpointer: Any | None = None) -> _SyncApp:
    """``None`` installs an in-memory saver; ``False`` installs none, so ``ask_user`` cannot pause.

    Measured growth: 101 KB after one turn, 844 KB after six."""
    if checkpointer is False:
        return as_sync(build_graph().compile())
    saver = InMemorySaver() if checkpointer is None else checkpointer
    return as_sync(build_graph().compile(checkpointer=saver))


def compile_durable(*, path: Any | None = None) -> _SyncApp:
    """SQLite saver on a loop this app owns (ADR 0014). Tests share thread ids and must not share one file."""
    from governed_bi.serve.checkpointer import open_harness_saver

    loop = asyncio.new_event_loop()
    saver = loop.run_until_complete(open_harness_saver(path))
    return _SyncApp(build_graph().compile(checkpointer=saver), loop=loop)
