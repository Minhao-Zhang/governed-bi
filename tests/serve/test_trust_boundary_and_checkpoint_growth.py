"""The served graph returns only ``ServeOutput`` on ``invoke``, and a finished question is evicted.

``values`` frames and ``get_state`` still expose every channel. The harness keeps one
compiled graph per worker, so eviction is per thread.
"""

from __future__ import annotations

from typing import Any

from governed_bi.eval.harness import _evict
from governed_bi.serve.graph import build_graph, compile_graph

#: Channels a client must never be handed back. Not exhaustive — the schema is an allowlist and
#: this is a tripwire on the specific keys that motivated it.
_MUST_NOT_ESCAPE = ("delivery", "settings_resolved", "query_vector", "retrieve_hooks")


def test_the_served_graph_returns_only_what_the_interface_reads() -> None:
    served = build_graph(accept=lambda s: {}).compile()
    assert set(served.output_channels) == {"messages", "answer"}, sorted(served.output_channels)


def test_no_internal_channel_escapes_on_the_served_path() -> None:
    served = build_graph(accept=lambda s: {}).compile()
    leaked = [k for k in _MUST_NOT_ESCAPE if k in served.output_channels]
    assert leaked == [], (
        f"{leaked} reach the client again. `delivery` carries the rendered corpus; see ServeOutput."
    )


def test_the_in_process_path_still_sees_everything() -> None:
    """In-process callers project the record from channels a client never sees."""
    inproc = build_graph().compile()
    assert len(inproc.output_channels) > 40, len(inproc.output_channels)
    for key in _MUST_NOT_ESCAPE:
        assert key in inproc.output_channels, key


def test_no_checkpointer_is_expressible() -> None:
    """``None`` builds an ``InMemorySaver``; ``False`` means do not persist."""
    assert compile_graph(checkpointer=False)._app.checkpointer is None
    assert compile_graph()._app.checkpointer is not None


def test_a_finished_question_stops_costing_memory() -> None:
    """Per thread, because with ``workers > 1`` a blunt clear would delete a sibling mid-flight."""
    app = compile_graph()
    saver: Any = app._app.checkpointer

    def depth() -> int:
        return sum(1 for _ in saver.list(None))

    for index in range(3):
        thread_id = f"run-config-q{index}"
        app._app.update_state({"configurable": {"thread_id": thread_id}}, {"question": "q"})
        assert depth() == 1, f"question {index} wrote nothing to evict"
        _evict(app, thread_id)
        assert depth() == 0, (
            f"question {index} left checkpoints behind; over a config this is ~100 KB per "
            "question retained until the process exits"
        )


def test_eviction_never_fails_a_turn() -> None:
    """Housekeeping must not be able to kill a config 900 questions in."""
    _evict(compile_graph(), "never-existed")
    _evict(object(), "no-saver-at-all")
    _evict(compile_graph(checkpointer=False), "no-saver-compiled")
